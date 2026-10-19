# -*- coding: utf-8 -*-
"""
gen_cert.py — Apple ATS 合规双层 TLS 证书生成工具

特点：
1. 生成 Root CA（根证书 sdk_ca.crt，有效 10 年，CA=True），供 iOS / Android / PC 安装到系统受信任根证书库。
2. 生成 Server Leaf 证书（服务证书 sdk_cert.pem，有效 365 天 <= 398 天，CA=False），由上述 Root CA 签发。
3. 自动将所有深空之眼官方域名（*.ys4fun.com 等）及本机全部局域网 IPv4 写入 SAN 扩展列表。
4. 严格符合 Apple iOS 13+ ATS (App Transport Security) 规范。
"""
import base64
import datetime
import ipaddress
import os
import socket
import sys
import uuid
import argparse
import json
import plistlib
import shutil
import time

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


CERT_DNS_NAMES = (
    "open.ys4fun.com", "account.ys4fun.com", "skzy.ys4fun.com",
    "download.ys4fun.com", "download-eo.ys4fun.com", "prod-api-activity.ys4fun.com",
    "packaging.ys4fun.com", "ta.ys4fun.com", "webstatic.ys4fun.com",
    "ys4fun-prod-pub.ys4fun.com", "m.bbs.ys4fun.com", "bbs.ys4fun.com",
    "soboten.com", "*.soboten.com", "*.ys4fun.com", "*.ys4fun.cn",
    "ys4fun.com", "ys4fun.cn", "localhost",
)


def _write_with_backup(path, data):
    """只在内容变化时写入；所有覆盖提前保留同级副本。"""
    if os.path.isfile(path):
        with open(path, "rb") as source:
            if source.read() == data:
                return
        shutil.copy2(path, f"{path}.before_cert_{time.time_ns()}")
    with open(path, "wb") as target:
        target.write(data)


def _public_key_bytes(key):
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _required_ips(extra_ips=None):
    result = {"127.0.0.1"}
    for value in [*get_all_lan_ips(), *(extra_ips or [])]:
        try:
            address = ipaddress.ip_address(value)
            if not address.is_unspecified:
                result.add(str(address))
        except ValueError:
            pass
    return result


def inspect_certificate_files(output_dir=None, extra_ips=None):
    """只读检查生成器与启动壳共同依赖的证书状态，不改证书或系统信任。"""
    result = dict(ready=False, root_usable=False, requires_root_replacement=False,
                  needs_leaf_renewal=True, needs_distribution_sync=True,
                  root_thumbprint="", root_not_after="", leaf_not_after="", issues=[])
    if not HAVE_CRYPTO:
        result["issues"].append("缺少 cryptography 库，无法检查证书")
        return result
    directory = output_dir or os.path.dirname(os.path.abspath(__file__))
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(os.path.join(directory, "sdk_ca.crt"), "rb") as source:
            root = x509.load_pem_x509_certificate(source.read())
        result["root_thumbprint"] = root.fingerprint(hashes.SHA1()).hex().upper()
        result["root_not_after"] = root.not_valid_after_utc.isoformat()
        with open(os.path.join(directory, "sdk_ca.key"), "rb") as source:
            root_key = serialization.load_pem_private_key(source.read(), password=None)
        if _public_key_bytes(root_key.public_key()) != _public_key_bytes(root.public_key()):
            raise ValueError("根证书与根私钥不匹配")
        if not root.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError("根证书缺少 CA 签发权限")
        if not root.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
            raise ValueError("根证书不允许签发服务证书")
        root.verify_directly_issued_by(root)
        if root.not_valid_before_utc > now or root.not_valid_after_utc <= now + datetime.timedelta(days=30):
            raise ValueError("根证书尚未生效或将在 30 天内到期")
        result["root_usable"] = True
    except Exception as error:
        result["requires_root_replacement"] = True
        result["issues"].append(f"根证书体系需重新部署: {error}")
        return result

    try:
        with open(os.path.join(directory, "sdk_cert.pem"), "rb") as source:
            chain = x509.load_pem_x509_certificates(source.read())
        leaf = chain[0]
        result["leaf_not_after"] = leaf.not_valid_after_utc.isoformat()
        with open(os.path.join(directory, "sdk_key.pem"), "rb") as source:
            leaf_key = serialization.load_pem_private_key(source.read(), password=None)
        if _public_key_bytes(leaf_key.public_key()) != _public_key_bytes(leaf.public_key()):
            raise ValueError("服务证书与服务私钥不匹配")
        leaf.verify_directly_issued_by(root)
        if len(chain) != 2 or chain[1].fingerprint(hashes.SHA256()) != root.fingerprint(hashes.SHA256()):
            raise ValueError("服务证书链与当前根证书不一致")
        if leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError("服务证书不能作为根证书")
        if ExtendedKeyUsageOID.SERVER_AUTH not in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
            raise ValueError("服务证书缺少服务器认证用途")
        if leaf.not_valid_before_utc > now or leaf.not_valid_after_utc <= now + datetime.timedelta(days=15):
            raise ValueError("服务证书尚未生效或将在 15 天内到期")
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        missing_dns = set(CERT_DNS_NAMES) - set(san.get_values_for_type(x509.DNSName))
        missing_ips = _required_ips(extra_ips) - {str(value) for value in san.get_values_for_type(x509.IPAddress)}
        if missing_dns or missing_ips:
            raise ValueError(f"服务证书缺少当前地址: {', '.join(sorted(missing_dns | missing_ips))}")
        result["needs_leaf_renewal"] = False
    except Exception as error:
        result["issues"].append(f"服务证书需更新: {error}")

    try:
        expected = root.public_bytes(serialization.Encoding.DER)
        for filename in ("sdk_cert.crt", "sdk_cert.cer"):
            with open(os.path.join(directory, filename), "rb") as source:
                data = source.read()
            cert = x509.load_pem_x509_certificate(data) if b"BEGIN CERTIFICATE" in data else x509.load_der_x509_certificate(data)
            if cert.public_bytes(serialization.Encoding.DER) != expected:
                raise ValueError(f"{filename} 与当前根不一致")
        for filename in ("AetherGazer.mobileconfig", "sdk_ca.mobileconfig"):
            with open(os.path.join(directory, filename), "rb") as source:
                profile = plistlib.load(source)
            payloads = [item for item in profile["PayloadContent"] if item.get("PayloadType") == "com.apple.security.root"]
            if len(payloads) != 1 or payloads[0]["PayloadContent"] != expected:
                raise ValueError(f"{filename} 未包含当前根证书")
        result["needs_distribution_sync"] = False
    except Exception as error:
        result["issues"].append(f"根证书安装文件或 iOS 描述文件需同步: {error}")
    result["ready"] = not result["issues"]
    return result


def _sync_root_distribution(directory, root):
    _write_with_backup(os.path.join(directory, "sdk_cert.crt"), root.public_bytes(serialization.Encoding.PEM))
    _write_with_backup(os.path.join(directory, "sdk_cert.cer"), root.public_bytes(serialization.Encoding.DER))
    for filename in ("AetherGazer.mobileconfig", "sdk_ca.mobileconfig"):
        generate_mobileconfig(root.public_bytes(serialization.Encoding.DER), os.path.join(directory, filename))


def generate_mobileconfig(ca_cert_der: bytes, output_path: str, display_name: str = "AetherGazer V5 Root CA"):
    """生成适用于 iOS / iPadOS 的 .mobileconfig 根证书描述文件 (Apple Configuration Profile)。"""
    cert_b64 = base64.b64encode(ca_cert_der).decode("ascii")
    cert_b64_formatted = "\n\t\t\t\t".join([cert_b64[i:i+64] for i in range(0, len(cert_b64), 64)])
    profile_uuid = str(uuid.uuid4()).upper()
    payload_uuid = str(uuid.uuid4()).upper()

    mobileconfig_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>PayloadContent</key>
\t<array>
\t\t<dict>
\t\t\t<key>PayloadCertificateFileName</key>
\t\t\t<string>AetherGazer_CA.crt</string>
\t\t\t<key>PayloadContent</key>
\t\t\t<data>
\t\t\t\t{cert_b64_formatted}
\t\t\t</data>
\t\t\t<key>PayloadDescription</key>
\t\t\t<string>配置受信任的深空之眼 V5 本地根证书 (Root CA)</string>
\t\t\t<key>PayloadDisplayName</key>
\t\t\t<string>{display_name}</string>
\t\t\t<key>PayloadIdentifier</key>
\t\t\t<string>com.ys4fun.aethergazer.ca.credential</string>
\t\t\t<key>PayloadType</key>
\t\t\t<string>com.apple.security.root</string>
\t\t\t<key>PayloadUUID</key>
\t\t\t<string>{payload_uuid}</string>
\t\t\t<key>PayloadVersion</key>
\t\t\t<integer>1</integer>
\t\t</dict>
\t</array>
\t<key>PayloadDescription</key>
\t<string>安装此描述文件以信任深空之眼单机仿真的本地 TLS 根证书</string>
\t<key>PayloadDisplayName</key>
\t<string>深空之眼 V5 本地根证书描述文件</string>
\t<key>PayloadIdentifier</key>
\t<string>com.ys4fun.aethergazer.ca.profile</string>
\t<key>PayloadOrganization</key>
\t<string>YongShi AetherGazer V5 Server</string>
\t<key>PayloadRemovalDisallowed</key>
\t<false/>
\t<key>PayloadType</key>
\t<string>Configuration</string>
\t<key>PayloadUUID</key>
\t<string>{profile_uuid}</string>
\t<key>PayloadVersion</key>
\t<integer>1</integer>
</dict>
</plist>
"""
    _write_with_backup(output_path, mobileconfig_xml.encode("utf-8"))


def get_all_lan_ips():
    """获取本机所有有效局域网 IPv4 地址列表，优先排序真实 LAN (192.168/10/172)。"""
    ips = set()
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    # 尝试出网 UDP 探测
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            ips.add(ip)
    except Exception:
        pass

    if not ips:
        ips.add("192.168.0.105")

    # 排序：优先 192.168 / 10 / 172.16~31，排除 198.18
    def _priority(ip):
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            return 2
        if ip.startswith("198.18."):
            return 9  # TUN 代理网卡放最后
        return 5

    sorted_ips = sorted(list(ips), key=_priority)
    return sorted_ips


def get_default_lan_ip():
    """获取本机首选局域网 IPv4 地址。"""
    ips = get_all_lan_ips()
    return ips[0] if ips else "192.168.0.105"


def generate_dual_layer_certs(output_dir=None, extra_ips=None, log=print, force=False, force_ca=False):
    """生成双层证书体系 (Root CA + 365天 Server Leaf Cert 及 iPhone 描述文件)。

    安全架构保护：
    Root CA 是操作系统的信任锚点 (Trust Anchor)。即使 force=True (比如局域网 IP 变更需重新签发服务证书)，
    也必须永久复用既有 Root CA，严禁覆写 sdk_ca.key 与 sdk_ca.crt，避免用户系统的信任链断裂！
    既有根缺失/损坏/临期时停止并提示重新部署；只有首次初始化或显式 force_ca 才生成新根。
    """
    if not HAVE_CRYPTO:
        log("[CERT] 缺少 cryptography 库，无法生成证书")
        return False

    out_dir = output_dir or os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)

    ca_key_path = os.path.join(out_dir, "sdk_ca.key")
    ca_crt_path = os.path.join(out_dir, "sdk_ca.crt")
    server_key_path = os.path.join(out_dir, "sdk_key.pem")
    server_crt_path = os.path.join(out_dir, "sdk_cert.pem")
    mobileconfig_path = os.path.join(out_dir, "AetherGazer.mobileconfig")
    cer_path = os.path.join(out_dir, "sdk_cert.cer")
    crt_path = os.path.join(out_dir, "sdk_cert.crt")

    now = datetime.datetime.now(datetime.timezone.utc)

    status = inspect_certificate_files(out_dir, extra_ips)
    all_ips = _required_ips(extra_ips)
    # 网络接口暂时消失时保留原地址，避免来回切换网络重复签发。
    try:
        with open(server_crt_path, "rb") as source:
            old_leaf = x509.load_pem_x509_certificate(source.read())
        all_ips.update(str(value) for value in old_leaf.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress))
    except Exception:
        pass

    # 2. 检查并持久化复用 Root CA（关键保护：即使 force，只要非 force_ca，一律严格复用既有 Root CA）
    ca_key = None
    ca_cert = None
    is_new_ca = False
    if not force_ca:
        if status["root_usable"]:
            with open(ca_key_path, "rb") as source:
                ca_key = serialization.load_pem_private_key(source.read(), password=None)
            with open(ca_crt_path, "rb") as source:
                ca_cert = x509.load_pem_x509_certificate(source.read())
            if not force and not status["needs_leaf_renewal"]:
                if status["needs_distribution_sync"]:
                    _sync_root_distribution(out_dir, ca_cert)
                    log("[CERT] 已同步 Windows 安装证书及两份 iOS 描述文件；根指纹不变。")
                else:
                    log("[CERT] [EXISTING] 根证书、服务证书和 iOS 描述文件检查通过，保持既有证书。")
                return True
            log("[CERT] [REUSE] 复用现有根证书，仅更新服务证书。")
        elif any(os.path.exists(os.path.join(out_dir, filename)) for filename in (
                "sdk_ca.key", "sdk_ca.crt", "sdk_cert.pem", "sdk_key.pem",
                "AetherGazer.mobileconfig", "sdk_ca.mobileconfig", "sdk_cert.crt", "sdk_cert.cer")):
            log("[CERT] [ERROR] " + "; ".join(status["issues"]))
            log("[CERT] 请在启动壳点击重新部署；普通启动不会自动替换现有根证书。")
            return False

    if ca_key is None or ca_cert is None:
        is_new_ca = True
        log(f"[CERT] 正在生成全新的持久化 Root CA 根证书 (有效期 10 年)...")
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "YongShi AetherGazer V5 Private CA"),
            x509.NameAttribute(NameOID.COMMON_NAME, "AetherGazer V5 Root CA (open.ys4fun.com)"),
        ])

        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

    # 2. 生成 Server Leaf 证书 (由上述固定的 Root CA 签发，有效 365 天 <= 398 天)
    log(f"[CERT] 正在生成 Server Leaf 服务端证书 (有效期 365 天, 符合 Apple ATS 规范)...")
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "YongShi AetherGazer V5 Server"),
        x509.NameAttribute(NameOID.COMMON_NAME, "open.ys4fun.com"),
    ])

    # 构造完整 SAN 列表（全量包含 PC 端与移动端全部业务域名及泛域名）
    san_names = [x509.DNSName(name) for name in CERT_DNS_NAMES]

    for ip_str in sorted(all_ips):
        try:
            san_names.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError:
            pass

    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(min(now + datetime.timedelta(days=365), ca_cert.not_valid_after_utc))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_names),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # 3. 写入文件（若复用已有 Root CA，绝不重复覆写原版根证书私钥与证书）
    if is_new_ca:
        _write_with_backup(ca_key_path, ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        _write_with_backup(ca_crt_path, ca_cert.public_bytes(serialization.Encoding.PEM))

    _write_with_backup(server_key_path, server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    # 服务端证书链（Server Cert + Root CA）
    _write_with_backup(server_crt_path, server_cert.public_bytes(serialization.Encoding.PEM)
                       + ca_cert.public_bytes(serialization.Encoding.PEM))

    # 同时输出 .crt 后缀和 .cer 副本便于 Windows 与移动端直接双击安装
    _sync_root_distribution(out_dir, ca_cert)

    log(f"[CERT] 证书体系构建成功！")
    log(f"  - Root CA (供手机/系统安装): {ca_crt_path} {'[全新创建]' if is_new_ca else '[已安全复用既有指纹]'}")
    log(f"  - Windows 证书 (供直接导入): {cer_path}")
    log(f"  - iPhone 描述文件: {mobileconfig_path}")
    log(f"  - Server Cert (供 HTTPS 服务加载): {server_crt_path} (有效期 365 天, SANs: {[s.value if hasattr(s, 'value') else str(s) for s in san_names]})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AetherGazer V5 TLS & MobileConfig Generator")
    parser.add_argument("--force", action="store_true", help="强制重新签发服务端叶子证书 (严格复用既有 Root CA 根信任)")
    parser.add_argument("--force-ca", action="store_true", help="高危操作：强制重新生成全新的 Root CA 根证书 (需所有设备重新导入信任)")
    parser.add_argument("--out", type=str, default=None, help="输出目录")
    parser.add_argument("--check", action="store_true", help="只读检查证书体系，不生成或导入证书")
    parser.add_argument("--json", action="store_true", help="检查结果输出为 JSON，供启动壳读取")
    args = parser.parse_args()
    if args.check:
        status = inspect_certificate_files(args.out)
        # 结构化输出只含 ASCII，避免 Windows 控制台代码页改变 JSON 编码。
        print(json.dumps(status, ensure_ascii=True) if args.json else
              ("[CERT] 检查通过" if status["ready"] else "[CERT] " + "; ".join(status["issues"])))
        sys.exit(0 if status["ready"] else 1)
    try:
        ok = generate_dual_layer_certs(output_dir=args.out, force=args.force, force_ca=args.force_ca)
    except Exception as error:
        print(f"[CERT] [ERROR] {error}", file=sys.stderr)
        ok = False
    sys.exit(0 if ok else 1)
