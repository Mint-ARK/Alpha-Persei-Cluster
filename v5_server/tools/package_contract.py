#!/usr/bin/env python3
"""Shared source/staging/archive checks for both release builders.

Only staging and isolated codec imports are used here; never start the server,
open an AccountDB, change certificates or fetch resources during validation.
"""
import argparse
import ast
from dataclasses import dataclass
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = Path(__file__).with_name("release_contract.json")
INVENTORY_NAME = "package_inventory.json"
ASSET_LITERAL = re.compile(r"[\w.-]+\.(?:json|sql|bin|yaml|yml|toml|ini|csv)", re.IGNORECASE)
JUNK_DIRS = {"__pycache__", ".git", ".vscode", "node_modules", ".test_runs"}


class PackageContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    source: Path
    size: int
    sha256: str


def file_hash(path):
    with Path(path).open("rb") as stream:
        return stream_hash(stream)


def stream_hash(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def relative_name(value):
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or ".." in path.parts or ":" in value:
        raise PackageContractError(f"资源清单路径无效: {value!r}")
    return path.as_posix()


def load_contract(path=CONTRACT_PATH):
    contract = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if contract.get("version") != 1:
        raise PackageContractError("不支持的发布资源清单版本")
    for name in contract["required_files"]:
        relative_name(name)
    if any(not isinstance(reason, str) or not reason.strip() for reason in contract["optional_dependencies"].values()):
        raise PackageContractError("可选依赖必须说明不随包提供的原因")
    return contract


def _excluded(name, contract):
    path = PurePosixPath(name)
    return any(part in JUNK_DIRS for part in path.parts) or any(
        fnmatch.fnmatchcase(path.name.lower(), pattern.lower())
        for pattern in contract["exclude_names"]
    )


def _check_required(names, contract):
    names = set(names)
    missing = sorted(set(contract["required_files"]) - names)
    for pattern, minimum in contract["required_groups"].items():
        count = sum(fnmatch.fnmatchcase(name, pattern) for name in names)
        if count < minimum:
            missing.append(f"{pattern} (至少 {minimum} 项，实际 {count})")
    if missing:
        raise PackageContractError("缺少必需发布资源，停止封包:\n  " + "\n  ".join(missing))


def _check_dependency_coverage(plan, contract):
    """New literal config dependencies must be bundled or explicitly explained.

    This catches root modules introducing another config format/path that the
    collection rules do not yet cover. Dynamically composed paths still need
    a required file/group in release_contract.json.
    """
    basenames = {PurePosixPath(name).name for name in plan}
    optional = contract["optional_dependencies"]
    missing = set()
    for name, asset in plan.items():
        if "/" in name or not name.endswith(".py"):
            continue
        tree = ast.parse(asset.source.read_text(encoding="utf-8-sig"), filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if ASSET_LITERAL.fullmatch(node.value) and node.value not in basenames and node.value not in optional:
                missing.add(f"{name}:{node.lineno} -> {node.value}")
    if missing:
        raise PackageContractError("发现未纳入发布清单的运行资源引用:\n  " + "\n  ".join(sorted(missing)))


def _validate_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise PackageContractError(f"JSON 配置不可读: {path}") from exc
    if path.name == "lua_proto_messages.json":
        messages = value.get("messages") if isinstance(value, dict) else None
        if not messages or not isinstance(messages, list):
            raise PackageContractError("协议文件为空或结构错误: lua_proto_messages.json")
    return value


def collect_runtime_assets(project_root=PROJECT_ROOT, contract=None):
    """Preflight source inputs before copying a database or building any output."""
    root = Path(project_root).resolve()
    contract = contract or load_contract()
    paths = {}
    for rule in contract["source_trees"]:
        source = (root / relative_name(rule["source"])).resolve()
        if not source.is_relative_to(root):
            raise PackageContractError(f"资源来源越出工程: {source}")
        if not source.is_dir() and rule.get("fallback_source"):
            source = (root / relative_name(rule["fallback_source"])).resolve()
            if not source.is_relative_to(root):
                raise PackageContractError(f"资源来源越出工程: {source}")
        if not source.is_dir():
            raise PackageContractError(f"缺少资源目录: {source}")
        candidates = source.rglob("*") if rule["recursive"] else source.iterdir()
        for path in sorted(candidates):
            if not path.is_file():
                continue
            rel = path.relative_to(source).as_posix()
            target = relative_name(str(PurePosixPath(rule["target"]) / rel))
            if _excluded(target, contract) or not any(fnmatch.fnmatchcase(path.name.lower(), p.lower()) for p in rule["patterns"]):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise PackageContractError(f"资源不能链接到工程之外: {path}")
            if target in paths:
                raise PackageContractError(f"重复资源目标: {target}")
            paths[target] = path
    _check_required(paths, contract)
    plan = {}
    for name, source in paths.items():
        if name.lower().endswith(".json"):
            _validate_json(source)
        plan[name] = Asset(source, source.stat().st_size, file_hash(source))
    _check_dependency_coverage(plan, contract)
    return plan


def stage_runtime_assets(target, plan):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name, asset in sorted(plan.items()):
        dest = target / name
        if dest.exists():
            raise PackageContractError(f"暂存资源已存在，拒绝覆盖: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset.source, dest)
    validate_staged_assets(target, plan)


def validate_staged_assets(target, plan):
    target = Path(target)
    for name, asset in plan.items():
        path = target / name
        if not path.is_file() or path.stat().st_size != asset.size or file_hash(path) != asset.sha256:
            raise PackageContractError(f"暂存资源缺失或与源码不一致: {name}")


PROBE = r'''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import codec, decode_schema, hero_codec
for module in (codec, decode_schema, hero_codec):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root), module.__name__
assert pathlib.Path(decode_schema.DEFAULT_SCHEMA_PATH).resolve() == root / "lua_proto_messages.json"
decode_schema.ensure_schema()
for message in ("sc_10039", "sc_10043", "sc_63011"):
    payload = codec.encode(message, {"result": 1})
    assert payload == b"\x08\x01", message
    assert codec.decode(payload, message)["result"] == 1, message
hero_dir = pathlib.Path(hero_codec._HERO_DIR).resolve()
assert hero_dir.is_relative_to(root), "hero templates escaped package"
templates = list(hero_dir.glob("hero_*.json"))
assert templates, "no hero templates"
for path in templates:
    json.loads(path.read_text(encoding="utf-8-sig"))
assert hero_codec.load_hero_expanded(int(templates[0].stem[5:])), "unreadable hero template"
print(json.dumps({"schema_messages": len(decode_schema.SCHEMA), "hero_templates": len(templates), "codec_roundtrips": 3}))
'''


def probe_runtime(target, python_executable=sys.executable):
    result = subprocess.run(
        [str(python_executable), "-I", "-B", "-c", PROBE, str(Path(target).resolve())],
        cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode:
        raise PackageContractError(f"独立包内协议验证失败:\n{result.stderr or result.stdout}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise PackageContractError("包内探针未返回有效结果") from exc


def validate_release_database(path):
    """Read-only gate: no saved API profile/routing may enter a release."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='server_global_config'").fetchone():
            return
        row = conn.execute("SELECT value FROM server_global_config WHERE key='ai.api_profiles.v1'").fetchone()
        if row:
            store = json.loads(row[0])
            if not isinstance(store, dict) or store.get('version') != 1:
                raise PackageContractError('发布存档AI配置格式无效')
            if store.get('profiles') or store.get('routes') or store.get('active_id'):
                raise PackageContractError('发布存档仍含AI配置或凭据，拒绝封包')
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise PackageContractError('发布存档AI凭据检查失败') from exc
    finally:
        conn.close()


def seal_runtime(target, plan, python_executable=sys.executable):
    """Only a validated payload gets an inventory and can proceed to publication."""
    target = Path(target)
    validate_staged_assets(target, plan)
    for name in ("account.db", "run_server.bat"):
        if not (target / name).is_file() or (target / name).stat().st_size == 0:
            raise PackageContractError(f"缺少完整发布包必需文件: {name}")
    validate_release_database(target / 'account.db')
    probe = probe_runtime(target, python_executable)
    inventory = {"version": 1, "probe": probe, "files": {}}
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(target).as_posix()
        if name == INVENTORY_NAME:
            raise PackageContractError("暂存目录已有封包清单，拒绝覆盖")
        if path.suffix.lower() == ".db" and name != "account.db":
            raise PackageContractError(f"发布目录混入非主数据库: {name}")
        inventory["files"][name] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    (target / INVENTORY_NAME).write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    return inventory


def validate_inventory(target, inventory):
    target = Path(target)
    actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    if actual != set(inventory["files"]) | {INVENTORY_NAME}:
        raise PackageContractError("暂存目录在校验后发生文件增减")
    if json.loads((target / INVENTORY_NAME).read_text(encoding="utf-8")) != inventory:
        raise PackageContractError("包内清单发生变化")
    for name, expected in inventory["files"].items():
        path = target / relative_name(name)
        if path.stat().st_size != expected["size"] or file_hash(path) != expected["sha256"]:
            raise PackageContractError(f"校验后暂存文件发生变化: {name}")


def validate_installer_template(template):
    section = re.search(r"(?ims)^\[Files\]\s*(.*?)(?=^\[|\Z)", template)
    if section:
        for line in section.group(1).splitlines():
            rule = re.fullmatch(
                r'\s*Source:\s*"\{#MySourceDir\}\\\*";\s*DestDir:\s*"\{app\}";\s*Flags:\s*([\w ]+)\s*',
                line, re.IGNORECASE,
            )
            if rule and {"recursesubdirs", "createallsubdirs"}.issubset(rule[1].lower().split()):
                return
    raise PackageContractError("安装模板必须完整递归包含已验证载荷，不能省略资源子目录")


def validate_archive(path, inventory, server_prefix=""):
    prefix = relative_name(server_prefix).rstrip("/") + "/" if server_prefix else ""
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise PackageContractError("ZIP CRC 校验失败")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise PackageContractError("ZIP 存在重复文件名")
        expected_names = {prefix + name for name in inventory["files"]} | {prefix + INVENTORY_NAME}
        actual = {name for name in names if name.startswith(prefix) and not name.endswith("/")}
        if actual != expected_names:
            missing = sorted(expected_names - actual)
            extra = sorted(actual - expected_names)
            raise PackageContractError(f"ZIP 资源清单不匹配，缺少 {missing}，多出 {extra}")
        if json.loads(archive.read(prefix + INVENTORY_NAME)) != inventory:
            raise PackageContractError("ZIP 内封包清单与已验证载荷不一致")
        for name, expected in inventory["files"].items():
            member = prefix + name
            with archive.open(member) as stream:
                digest = stream_hash(stream)
            if archive.getinfo(member).file_size != expected["size"] or digest != expected["sha256"]:
                raise PackageContractError(f"ZIP 内资源哈希不匹配: {name}")
        if prefix and not {"LocalServer.exe", "runtime/python/python.exe"}.issubset(names):
            raise PackageContractError("便携包缺少启动壳或内置 Python")


def main():
    parser = argparse.ArgumentParser(description="只读检查发布资源，不构建、不启动服务")
    parser.add_argument("--check-source", action="store_true", required=True)
    parser.parse_args()
    plan = collect_runtime_assets()
    print(f"[OK] 发布资源预检通过: {len(plan)} 个文件，协议/配置引用已覆盖")


if __name__ == "__main__":
    main()
