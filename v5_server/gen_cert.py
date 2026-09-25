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

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


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
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(mobileconfig_xml)


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


def generate_dual_layer_certs(output_dir=None, extra_ips=None, log=print, force=False):
    """生成双层证书体系 (Root CA + 365天 Server Leaf Cert 及 iPhone 描述文件)。"""
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

    # 1. 检查既有证书：若未指定 force 且检测到已存在全部对应证书与 iPhone 描述文件，不主动重新生成
    if not force:
        if (os.path.isfile(ca_key_path) and os.path.isfile(ca_crt_path) and
            os.path.isfile(server_key_path) and os.path.isfile(server_crt_path) and
            os.path.isfile(cer_path) and os.path.isfile(crt_path) and
            os.path.isfile(mobileconfig_path)):
            try:
                with open(ca_crt_path, "rb") as f:
                    _check_ca = x509.load_pem_x509_certificate(f.read())
                _not_after = getattr(_check_ca, "not_valid_after_utc", None) or _check_ca.not_valid_after.replace(tzinfo=datetime.timezone.utc)
                if _not_after > now + datetime.timedelta(days=30):
                    log(f"[CERT] [EXISTING] 检测到已有完整证书体系与 iPhone 描述文件，保持既有证书（不主动生成）: {ca_crt_path}")
                    return True
            except Exception:
                pass

    all_ips = set(["127.0.0.1"])
    for ip in get_all_lan_ips():
        all_ips.add(ip)
    if extra_ips:
        for ip in extra_ips:
            if ip and ip != "0.0.0.0":
                all_ips.add(ip)

    # 2. 检查并持久化复用 Root CA（若非 force，尽量复用 Root CA 避免已安装用户重复导入）
    ca_key = None
    ca_cert = None
    if not force and os.path.isfile(ca_key_path) and os.path.isfile(ca_crt_path):
        try:
            with open(ca_key_path, "rb") as f:
                ca_key = serialization.load_pem_private_key(f.read(), password=None)
            with open(ca_crt_path, "rb") as f:
                ca_cert = x509.load_pem_x509_certificate(f.read())
            # 检查有效期是否还有 30 天以上
            not_after = getattr(ca_cert, "not_valid_after_utc", None) or ca_cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
            if not_after > now + datetime.timedelta(days=30):
                log(f"[CERT] [REUSE] 成功复用既有 Root CA 根证书: {ca_crt_path}")
            else:
                ca_key = None
                ca_cert = None
        except Exception as e:
            log(f"[CERT] 加载已有 Root CA 异常 ({e})，将重新生成")
            ca_key = None
            ca_cert = None

    if ca_key is None or ca_cert is None:
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

        with open(ca_key_path, "wb") as f:
            f.write(ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))

        with open(ca_crt_path, "wb") as f:
            f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    # 2. 生成 Server Leaf 证书 (由上述固定的 Root CA 签发，有效 365 天 <= 398 天)
    log(f"[CERT] 正在生成 Server Leaf 服务端证书 (有效期 365 天, 符合 Apple ATS 规范)...")
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "YongShi AetherGazer V5 Server"),
        x509.NameAttribute(NameOID.COMMON_NAME, "open.ys4fun.com"),
    ])

    # 构造完整 SAN 列表（全量包含 PC 端与移动端全部业务域名及泛域名）
    san_names = [
        x509.DNSName("open.ys4fun.com"),
        x509.DNSName("account.ys4fun.com"),
        x509.DNSName("skzy.ys4fun.com"),
        x509.DNSName("download.ys4fun.com"),
        x509.DNSName("download-eo.ys4fun.com"),
        x509.DNSName("prod-api-activity.ys4fun.com"),
        x509.DNSName("packaging.ys4fun.com"),
        x509.DNSName("ta.ys4fun.com"),
        x509.DNSName("webstatic.ys4fun.com"),
        x509.DNSName("ys4fun-prod-pub.ys4fun.com"),
        x509.DNSName("m.bbs.ys4fun.com"),
        x509.DNSName("bbs.ys4fun.com"),
        x509.DNSName("soboten.com"),
        x509.DNSName("*.soboten.com"),
        x509.DNSName("*.ys4fun.com"),
        x509.DNSName("*.ys4fun.cn"),
        x509.DNSName("ys4fun.com"),
        x509.DNSName("ys4fun.cn"),
        x509.DNSName("localhost"),
    ]

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
        .not_valid_after(now + datetime.timedelta(days=365))  # 365 天 < 398 天限制
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

    # 3. 写入文件
    with open(ca_key_path, "wb") as f:
        f.write(ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    with open(ca_crt_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    with open(server_key_path, "wb") as f:
        f.write(server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    # 服务端证书链（Server Cert + Root CA）
    with open(server_crt_path, "wb") as f:
        f.write(server_cert.public_bytes(serialization.Encoding.PEM))
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    # 同时输出 .crt 后缀和 .cer 副本便于 Windows 与移动端直接双击安装
    with open(os.path.join(out_dir, "sdk_cert.crt"), "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    with open(os.path.join(out_dir, "sdk_cert.cer"), "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.DER))

    # 生成适用于 iPhone / iOS 的根证书描述文件 (.mobileconfig)
    ca_der = ca_cert.public_bytes(serialization.Encoding.DER)
    generate_mobileconfig(ca_der, mobileconfig_path)
    generate_mobileconfig(ca_der, os.path.join(out_dir, "sdk_ca.mobileconfig"))

    log(f"[CERT] 证书体系构建成功！")
    log(f"  - Root CA (供手机/系统安装): {ca_crt_path}")
    log(f"  - Windows 证书 (供直接导入): {cer_path}")
    log(f"  - iPhone 描述文件: {mobileconfig_path}")
    log(f"  - Server Cert (供 HTTPS 服务加载): {server_crt_path} (有效期 365 天, SANs: {[s.value if hasattr(s, 'value') else str(s) for s in san_names]})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AetherGazer V5 TLS & MobileConfig Generator")
    parser.add_argument("--force", action="store_true", help="强制重新生成证书体系与描述文件")
    parser.add_argument("--out", type=str, default=None, help="输出目录")
    args = parser.parse_args()
    generate_dual_layer_certs(output_dir=args.out, force=args.force)
