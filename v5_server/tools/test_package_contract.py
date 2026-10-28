"""Bounded packaging regressions. All fixture outputs stay in system TEMP."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import build_all_distributions as full
import build_release_package as backend
import package_contract as pc
from package_lifecycle import recycle_or_raise


def fixture_database():
    conn = sqlite3.connect(':memory:')
    try:
        conn.execute('CREATE TABLE server_global_config(key TEXT PRIMARY KEY,value TEXT)')
        blob = conn.serialize()
    finally:
        conn.close()
    backup_dir = Path(__file__).resolve().parents[2] / 'db_backup'
    backup_dir.mkdir(exist_ok=True)
    (backup_dir / ('package_fixture_' + uuid.uuid4().hex + '.db')).write_bytes(blob)
    return blob


class PackageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = Path(tempfile.mkdtemp(prefix="V5PkgTests_"))

    @classmethod
    def tearDownClass(cls):
        # Project rule: no physical deletion, including temporary test files.
        recycle_or_raise(cls.sandbox)

    def setUp(self):
        self.root = self.sandbox / uuid.uuid4().hex[:8]
        self.source = self.root / "v5_server"
        self.source.mkdir(parents=True)
        (self.source / "data").mkdir()
        (self.source / "codec.py").write_text('CONFIG = "cfg.json"\n', encoding="utf-8")
        (self.source / "cfg.json").write_text('{"enabled": true}', encoding="utf-8")
        (self.source / "lua_proto_messages.json").write_text(
            '{"messages": [{"name": "sc_10039", "fields": []}]}', encoding="utf-8")
        (self.source / "data/config.json").write_text('{}', encoding="utf-8")
        # Not a SQLite database: a static sentinel detects accidental copying.
        (self.source / "data/account_test.db").write_bytes(b"must-not-ship")
        (self.source / "account.db").write_bytes(fixture_database())
        (self.source / ".env").write_text("DO_NOT_SHIP=fixture", encoding="utf-8")
        (self.source / "scratch_probe.json").write_text('{}', encoding="utf-8")
        self.contract = {
            "version": 1,
            "source_trees": [
                {"source": "v5_server", "target": ".", "recursive": False,
                 "patterns": ["*.py", "*.json", "*.toml", "*.sql"]},
                {"source": "v5_server/data", "target": "data", "recursive": True,
                 "patterns": ["*"]},
            ],
            "required_files": ["codec.py", "cfg.json", "lua_proto_messages.json", "data/config.json"],
            "required_groups": {"data/*.json": 1},
            "exclude_names": ["*.db", "scratch_*", "*.before*", "*.pyc"],
            "optional_dependencies": {},
        }

    def plan(self):
        return pc.collect_runtime_assets(self.root, self.contract)

    def stage(self, prefix=""):
        target = self.root / "payload" / prefix
        plan = self.plan()
        pc.stage_runtime_assets(target, plan)
        (target / "account.db").write_bytes(fixture_database())
        (target / "run_server.bat").write_text("fixture", encoding="utf-8")
        with patch.object(pc, "probe_runtime", return_value={"fixture": True}):
            inventory = pc.seal_runtime(target, plan)
        return target, plan, inventory

    def archive(self, target, *, drop=None, replace=None, prefix=""):
        path = self.root / ("bundle" + str(len(list(self.root.glob('bundle*.zip')))) + ".zip")
        with zipfile.ZipFile(path, "w") as archive:
            for file in target.rglob("*"):
                if not file.is_file():
                    continue
                name = file.relative_to(target).as_posix()
                if name != drop:
                    archive.writestr(prefix + name, (replace or {}).get(name, file.read_bytes()))
            if prefix:
                archive.writestr("LocalServer.exe", b"fixture-shell")
                archive.writestr("runtime/python/python.exe", b"fixture-python")
        return path

    def test_vendored_catalog_fallback_and_path_boundary(self):
        (self.source / "vendor").mkdir()
        (self.source / "vendor/hero_fixture.json").write_text('{}', encoding="utf-8")
        self.contract["source_trees"].append({"source":"missing/catalog", "fallback_source":"v5_server/vendor",
            "target":"vendor", "recursive":False, "patterns":["*.json"]})
        self.assertIn("vendor/hero_fixture.json", self.plan())
        self.contract["source_trees"][-1]["fallback_source"] = "../outside"
        with self.assertRaises(pc.PackageContractError):
            self.plan()

    def test_host_slot_registry_is_not_a_release_asset(self):
        (self.source / "data/slot_registry.json").write_text('{"3":"private-test-save.db"}', encoding="utf-8")
        self.contract["exclude_names"] = pc.load_contract()["exclude_names"]
        self.assertNotIn("data/slot_registry.json", self.plan())

    def test_additional_saves_must_be_explicit_and_each_is_key_free(self):
        target = self.root / "multi-save"
        plan = self.plan()
        pc.stage_runtime_assets(target, plan)
        for name in ("account.db", "预设_开荒.db"):
            (target / name).write_bytes(fixture_database())
        (target / "run_server.bat").write_text("fixture", encoding="utf-8")
        with patch.object(pc, "probe_runtime", return_value={"fixture": True}):
            with self.assertRaisesRegex(pc.PackageContractError, "未指定数据库"):
                pc.seal_runtime(target, plan)
            backup_dir = pc.PROJECT_ROOT / "db_backup"
            backup_dir.mkdir(exist_ok=True)
            shutil.copy2(target / "预设_开荒.db", backup_dir / ("package_multi_" + uuid.uuid4().hex + ".db"))
            conn = sqlite3.connect(target / "预设_开荒.db")
            conn.execute("INSERT INTO server_global_config VALUES(?,?)", ("ai.api_profiles.v1", json.dumps(dict(version=1,active_id="x",profiles={"x":{}},routes={}))))
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(pc.PackageContractError, "凭据"):
                pc.seal_runtime(target, plan, additional_saves=("预设_开荒.db",))
            backup_dir = pc.PROJECT_ROOT / "db_backup"
            backup_dir.mkdir(exist_ok=True)
            shutil.copy2(target / "预设_开荒.db", backup_dir / ("package_multi_" + uuid.uuid4().hex + ".db"))
            conn = sqlite3.connect(target / "预设_开荒.db")
            conn.execute("DELETE FROM server_global_config")
            conn.commit()
            conn.close()
            inventory = pc.seal_runtime(target, plan, additional_saves=("预设_开荒.db",))
        self.assertEqual(inventory["save_files"], ["account.db", "预设_开荒.db"])
        pc.validate_archive(self.archive(target), inventory)

    def test_source_missing_schema_is_fatal_even_with_analysis_fallback(self):
        (self.source / "lua_proto_messages.json").rename(self.source / "schema.before")
        analysis = self.root / "analysis_scripts/协议分析"
        analysis.mkdir(parents=True)
        (analysis / "lua_proto_messages.json").write_text('{"messages": [{}]}', encoding="utf-8")
        with self.assertRaisesRegex(pc.PackageContractError, "lua_proto_messages.json"):
            self.plan()

    def test_missing_nested_config_and_invalid_json_are_fatal(self):
        (self.source / "data/config.json").rename(self.source / "data/config.before")
        with self.assertRaisesRegex(pc.PackageContractError, "data/config.json"):
            self.plan()
        (self.source / "data/config.json").write_text('{broken', encoding="utf-8")
        with self.assertRaisesRegex(pc.PackageContractError, "JSON"):
            self.plan()

    def test_archived_mail_templates_are_required_and_kept_at_runtime_path(self):
        production = pc.load_contract()
        name = "mail_archive/mail_templates.json"
        self.assertIn(name, production["required_files"])
        self.assertNotIn("mail_templates.json", production["optional_dependencies"])
        rule = next(r for r in production["source_trees"] if r["source"] == "v5_server/mail_archive")
        self.contract["source_trees"].append(copy.deepcopy(rule))
        self.contract["required_files"].append(name)
        (self.source / "mail_archive").mkdir()
        source = self.source / name
        source.write_text('[{"id":1007,"title":"资源包（剩余%s天）"}]', encoding="utf-8")
        target, plan, inventory = self.stage()
        self.assertEqual((target / name).read_bytes(), source.read_bytes())
        pc.validate_archive(self.archive(target), inventory)
        with self.assertRaisesRegex(pc.PackageContractError, "mail_archive/mail_templates.json"):
            pc.validate_archive(self.archive(target, drop=name), inventory)
        source.rename(source.with_suffix(".before"))
        with self.assertRaisesRegex(pc.PackageContractError, "mail_archive/mail_templates.json"):
            self.plan()

    def test_new_dependencies_are_collected_or_blocked(self):
        (self.source / "codec.py").write_text('CONFIG = "new_feature.toml"', encoding="utf-8")
        with self.assertRaisesRegex(pc.PackageContractError, "new_feature.toml"):
            self.plan()
        (self.source / "new_feature.toml").write_text('enabled = true', encoding="utf-8")
        self.assertIn("new_feature.toml", self.plan())
        (self.source / "db_schema.sql").write_text('CREATE TABLE fixture(id);', encoding="utf-8")
        self.assertIn("db_schema.sql", self.plan())

    def test_test_databases_private_env_and_scratch_are_excluded(self):
        plan = self.plan()
        self.assertNotIn("data/account_test.db", plan)
        self.assertNotIn("scratch_probe.json", plan)
        self.assertNotIn(".env", plan)
        self.assertNotIn("account.db", plan)

    def test_missing_or_changed_staged_files_fail(self):
        target, plan, inventory = self.stage()
        (target / "cfg.json").rename(target / "cfg.before")
        with self.assertRaises(pc.PackageContractError):
            pc.validate_staged_assets(target, plan)
        with self.assertRaises(pc.PackageContractError):
            pc.validate_inventory(target, inventory)

    def test_valid_backend_and_portable_archives_match_inventory(self):
        target, _, inventory = self.stage()
        pc.validate_archive(self.archive(target), inventory)
        pc.validate_archive(self.archive(target, prefix="v5_server/"), inventory, "v5_server")
        pc.validate_inventory(target, inventory)

    def test_crc_valid_archive_missing_or_changed_config_is_rejected(self):
        target, _, inventory = self.stage()
        missing = self.archive(target, drop="lua_proto_messages.json")
        with zipfile.ZipFile(missing) as z:
            self.assertIsNone(z.testzip())
        with self.assertRaisesRegex(pc.PackageContractError, "lua_proto_messages.json"):
            pc.validate_archive(missing, inventory)
        changed = self.archive(target, replace={"cfg.json": b'{"enabled":false}'})
        with self.assertRaisesRegex(pc.PackageContractError, "cfg.json"):
            pc.validate_archive(changed, inventory)

    def test_rewritten_internal_inventory_cannot_hide_missing_asset(self):
        target, _, inventory = self.stage()
        tampered = copy.deepcopy(inventory)
        tampered["files"].pop("cfg.json")
        path = self.archive(target, drop="cfg.json", replace={pc.INVENTORY_NAME: json.dumps(tampered).encode()})
        with self.assertRaises(pc.PackageContractError):
            pc.validate_archive(path, inventory)

    def test_probe_failure_prevents_sealing(self):
        target = self.root / "payload"
        plan = self.plan()
        pc.stage_runtime_assets(target, plan)
        (target / "account.db").write_bytes(fixture_database())
        (target / "run_server.bat").write_bytes(b"fixture-launcher")
        with patch.object(pc, "probe_runtime", side_effect=pc.PackageContractError("probe failed")):
            with self.assertRaisesRegex(pc.PackageContractError, "probe failed"):
                pc.seal_runtime(target, plan)
        self.assertFalse((target / pc.INVENTORY_NAME).exists())

    def test_both_entrypoints_preflight_before_database_or_publication(self):
        for builder, entry in ((backend, backend.build_release_package), (full, full.main)):
            with self.subTest(builder=builder.__name__), ExitStack() as stack:
                stack.enter_context(patch.object(builder, "collect_runtime_assets", side_effect=pc.PackageContractError("missing")))
                vacuum = stack.enter_context(patch.object(builder, "vacuum_database"))
                publish = stack.enter_context(patch.object(builder, "publish_package_to_debug_output"))
                with self.assertRaises(pc.PackageContractError):
                    entry()
                vacuum.assert_not_called()
                publish.assert_not_called()

    def test_backend_zip_validation_failure_preserves_published_output(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(backend, "PROJECT_ROOT", self.root))
            stack.enter_context(patch.object(backend, "V5_SERVER", self.source))
            stack.enter_context(patch.object(backend, "DIST_DIR", self.root / "build"))
            stack.enter_context(patch.object(backend, "TEMP_STAGE", self.root / "build/stage"))
            stack.enter_context(patch.object(backend, "collect_runtime_assets", return_value=self.plan()))
            stack.enter_context(patch.object(backend, "vacuum_database", side_effect=shutil.copy2))
            stack.enter_context(patch.object(pc, "probe_runtime", return_value={"fixture": True}))
            stack.enter_context(patch.object(backend, "validate_archive", side_effect=pc.PackageContractError("missing asset")))
            publish = stack.enter_context(patch.object(backend, "publish_package_to_debug_output"))
            with self.assertRaisesRegex(pc.PackageContractError, "missing asset"):
                backend.build_release_package()
            publish.assert_not_called()

    def test_full_server_stage_uses_same_resource_plan(self):
        target = self.root / "portable/v5_server"
        with patch.object(full, "V5_SERVER", self.source), patch.object(
            full, "vacuum_database", side_effect=shutil.copy2
        ), patch.object(pc, "probe_runtime", return_value={"fixture": True}):
            inventory = full.stage_clean_v5_server(target, self.plan())
        pc.validate_inventory(target, inventory)
        self.assertIn("lua_proto_messages.json", inventory["files"])
        self.assertNotIn("data/account_test.db", inventory["files"])

    def test_installer_template_must_include_nested_runtime_resources(self):
        template = full.TEMPLATE_ISS_FILE.read_text(encoding="utf-8")
        pc.validate_installer_template(template)
        with self.assertRaises(pc.PackageContractError):
            pc.validate_installer_template(template.replace("recursesubdirs", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
