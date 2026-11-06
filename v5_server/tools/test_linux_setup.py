"""Isolated Linux wizard tests: no real DB, package installation or system service."""
import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import linux_manager as lm
import linux_nginx as ng


class LinuxSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = ROOT.parent / "archive/linux_setup_redesign_20261029/fixtures"
        archive.mkdir(parents=True, exist_ok=True)
        cls.sandbox = Path(tempfile.mkdtemp(prefix="probe_", dir=archive))

    def config(self, **updates):
        return dict(lm.DEFAULTS, schema=2, usage="local", nginx=False,
                    nginx_dir="/etc/nginx/conf.d", **updates)

    def folder(self):
        return Path(tempfile.mkdtemp(dir=self.sandbox))

    def test_v1_migrates_in_memory_without_auth_or_version(self):
        old = dict(lm.DEFAULTS, schema=1, res_version="229", username="admin", password_hash="old-hash")
        path = self.folder() / "linux_config.json"
        original = json.dumps(old)
        lm.backup_write(path, original)
        config = lm.load_config(path)
        self.assertEqual(config["schema"], 2)
        self.assertNotIn("res_version", config)
        self.assertNotIn("password_hash", config)
        self.assertEqual(path.read_text(), original)
        lm.backup_write(path, json.dumps(config))
        self.assertEqual(next((path.parent / "archive").iterdir()).read_text(), original)

    def test_config_values_and_unicode_arguments(self):
        config = dict(self.config(), db="新存档 空格.db", host_ip="127.0.0.1")
        args = lm.config_args(lm.validate_config(config))
        self.assertEqual(args[args.index("--db") + 1], config["db"])
        self.assertNotIn("--res-version", args)
        self.assertNotIn("--game-version", args)
        self.assertNotIn("--usage", args)
        self.assertNotIn("--nginx", args)
        self.assertNotIn("--dns", args)

    def test_invalid_values_and_entire_pool_conflicts(self):
        for updates in ({"https_port": 0}, {"gw_port": True}, {"http_port": 65536},
                        {"host_ip": "0.0.0.0"}, {"host_ip": "198.18.0.1"}, {"dns": "yes"},
                        {"gw_port": 65520}, {"https_port": 8120}, {"schema": 9},
                        {"nginx": True}, {"db": ""}):
            with self.subTest(updates=updates), self.assertRaises((ValueError, TypeError)):
                lm.validate_config(dict(self.config(), **updates))

    def test_gateway_mapping_matches_actual_core(self):
        tree = ast.parse((ROOT / "save_slot_manager.py").read_text("utf-8-sig"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "slot_id_to_port")
        env = dict(BASE_GW_PORT=8102, DEFAULT_GAME_PORT=8105)
        exec(compile(ast.Module(body=[function], type_ignores=[]), "isolated-slot", "exec"), env)
        for base, game in ((8102, 8105), (9000, 8105), (9000, 9000), (9000, 9031), (9000, 9100)):
            self.assertEqual(lm.gateway_ports(base, game), [env["slot_id_to_port"](i, base, game) for i in range(1, 33)])

    def test_occupied_listener_rejected_and_untouched(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with self.assertRaisesRegex(OSError, str(port)):
                lm.check_ports(port, 0, 0, 0, 0, "127.0.0.1")
            self.assertEqual(occupied.getsockname(), ("127.0.0.1", port))
            self.assertEqual(occupied.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
        lm.check_ports(0, 0, 0, 0, 0)

    def test_all_failures_reported_and_sockets_closed(self):
        mock_socket = Mock()
        mock_socket.bind.side_effect = PermissionError("denied")
        with patch.object(lm.socket, "socket", return_value=mock_socket):
            errors = lm.probe_endpoints([(socket.SOCK_STREAM, "0.0.0.0", 443), (socket.SOCK_DGRAM, "0.0.0.0", 53)])
        self.assertEqual(len(errors), 2)
        self.assertEqual(mock_socket.close.call_count, 2)

    def test_enter_does_not_bypass_port_validation(self):
        config = self.config()
        # First game's default is busy. A second input repairs it; other fields use defaults.
        with patch("builtins.input", side_effect=["", "9105", "", "", "", ""]), patch.object(lm, "check_ports", side_effect=[OSError("busy"), None, None, None, None, None]) as probe, patch.object(lm, "is_termux", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            lm.ask_ports(config)
        self.assertEqual(config["game_port"], 9105)
        self.assertEqual(probe.call_count, 6)

    def test_environment_collects_missing_dependencies(self):
        output = io.StringIO()
        with patch.object(lm.importlib, "import_module", side_effect=ImportError("missing")), contextlib.redirect_stdout(output), self.assertRaisesRegex(ValueError, "环境检查未通过"):
            lm.runtime_check()
        for package in ("Flask", "Werkzeug", "cryptography"):
            self.assertIn(package, output.getvalue())

    def test_missing_environment_stops_before_questions(self):
        with patch.object(lm, "runtime_check", side_effect=ValueError("missing")), patch("builtins.input") as prompt, self.assertRaises(ValueError), contextlib.redirect_stdout(io.StringIO()):
            lm.setup()
        prompt.assert_not_called()

    def wizard(self, answers, previous=None, termux=False):
        target = self.folder()
        path = target / "linux_config.json"
        auth_path = target / "linux_admin_auth.json"
        # Opaque marker, never opened as SQLite by the wizard.
        (target / "account.db").write_bytes(b"opaque-save-fixture")
        if previous:
            lm.backup_write(path, json.dumps(previous))
            existing_auth = {
                "schema": 1,
                "password_hash": "existing-test-hash",
                "origin": "https://127.0.0.1",
                "session_seconds": 3600,
                "trusted_proxies": [],
            }
            auth_path.write_text(json.dumps(existing_auth), encoding="utf-8")
        with contextlib.ExitStack() as stack:
            for attribute, value in (("ROOT", target), ("CONFIG", path), ("ADMIN_AUTH_FILE", auth_path)):
                stack.enter_context(patch.object(lm, attribute, value))
            for function in ("runtime_check", "firewall_report", "network_report", "check_ports"):
                stack.enter_context(patch.object(lm, function))
            stack.enter_context(patch.object(lm, "is_termux", return_value=termux))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            lm.setup()
        self.assertEqual((target / "account.db").read_bytes(), b"opaque-save-fixture")
        return path

    def test_wizard_direct_has_no_password_or_version_prompt(self):
        path = self.wizard(["local", "", "3", "", "", "", "", "", "off", "AdminPassword123", "AdminPassword123", "y"])
        config = lm.load_config(path)
        self.assertEqual(config["host_ip"], "127.0.0.1")
        self.assertEqual(config["schema"], 2)
        self.assertNotIn("password", path.read_text())
        auth_file = path.parent / "linux_admin_auth.json"
        self.assertTrue(auth_file.is_file())
        auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
        self.assertEqual(auth_data["origin"], "https://127.0.0.1")

    def test_wizard_reconfiguration_keeps_saved_defaults(self):
        config = dict(self.config(), host_ip="10.0.0.5", usage="lan", https_port=19443, http_port=1880, admin_auth_configured=True)
        path = self.wizard([""] * 12, previous=config)
        self.assertEqual(lm.load_config(path), config)
        self.assertTrue((path.parent / "archive").is_dir())

    def test_wizard_termux_high_ports_and_no_lan_or_nginx_prompts(self):
        path = self.wizard(["", "", "", "", "", "off", "AdminPassword123", "AdminPassword123", "y"], termux=True)
        config = lm.load_config(path)
        self.assertEqual((config["https_port"], config["http_port"]), (18443, 1880))
        self.assertEqual(config["host_ip"], "127.0.0.1")
        self.assertFalse(config["dns"] or config["nginx"])

    def test_cancel_leaves_no_config(self):
        path = self.wizard(["local", "", "3", "", "", "", "", "", "off", "AdminPassword123", "AdminPassword123", "n"])
        self.assertFalse(path.exists())

    def test_wizard_requires_password_rejects_empty_and_short(self):
        answers = [
            "local", "", "3", "", "", "", "", "", "off",
            "",                          # 首次空输入，被拒绝
            "short",                     # 长度不足12，被拒绝
            "ValidPassword123",          # 有效密码
            "MismatchPassword123",       # 确认密码不匹配，被拒绝
            "ValidPassword123",          # 再次输入有效密码
            "ValidPassword123",          # 再次输入确认密码，匹配成功
            "y",                         # 确认保存
        ]
        path = self.wizard(answers)
        auth_file = path.parent / "linux_admin_auth.json"
        self.assertTrue(auth_file.is_file())
        auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
        self.assertEqual(auth_data["schema"], 1)
        self.assertEqual(auth_data["origin"], "https://127.0.0.1")
        self.assertEqual(auth_data["session_seconds"], 3600)
        from admin_session_auth import load_password_config
        cfg = load_password_config(str(auth_file))
        self.assertTrue(cfg.verify("ValidPassword123"))
        self.assertFalse(cfg.verify("WrongPassword123"))

    def test_run_auto_mounts_admin_password_file(self):
        config = self.config()
        observed = []
        target = self.folder()
        auth_file = target / "linux_admin_auth.json"
        auth_file.write_text(json.dumps({
            "schema": 1,
            "password_hash": "test_hash",
            "origin": "https://127.0.0.1",
            "session_seconds": 3600,
            "trusted_proxies": [],
        }), encoding="utf-8")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(lm, "ROOT", target))
            stack.enter_context(patch.object(lm, "runtime_check"))
            stack.enter_context(patch.object(lm, "load_config", return_value=config))
            stack.enter_context(patch.object(Path, "exists", return_value=True))
            stack.enter_context(patch.object(lm.os, "chdir"))
            stack.enter_context(patch.object(lm.runpy, "run_path", side_effect=lambda *a, **k: observed.append(os.environ.get("AG_ADMIN_PASSWORD_FILE"))))
            stack.enter_context(patch.dict(os.environ, {}, clear=False))
            os.environ.pop("AG_ADMIN_PASSWORD_FILE", None)
            lm.main(["run"])
        self.assertEqual(observed[0], str(auth_file.resolve()))

    def test_service_template_contains_admin_password_file_when_present(self):
        target = self.folder()
        auth_file = target / "linux_admin_auth.json"
        auth_file.write_text("{}", encoding="utf-8")
        with patch.object(lm, "ROOT", target), patch.dict(sys.modules, {"pwd": Mock(getpwuid=Mock(return_value=Mock(pw_name="fixture")))}), patch.object(os, "getuid", return_value=1000, create=True):
            lm.generate_templates(self.config())
        unit = (target / "linux_generated/aethergazer.service").read_text("utf-8")
        self.assertIn("Environment=\"AG_ADMIN_PASSWORD_FILE=", unit)

    def test_run_removes_legacy_auth_and_respects_cli_override(self):
        config = dict(self.config(), dns=True)
        observed = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(lm, "runtime_check"))
            stack.enter_context(patch.object(lm, "load_config", return_value=config))
            stack.enter_context(patch.object(Path, "exists", return_value=True))
            stack.enter_context(patch.object(lm.os, "chdir"))
            stack.enter_context(patch.object(lm.runpy, "run_path", side_effect=lambda *a, **k: observed.append((sys.argv[:], os.environ.get("AG_ADMIN_AUTH_FILE")))))
            stack.enter_context(patch.object(sys, "argv", ["test"]))
            stack.enter_context(patch.dict(os.environ, AG_ADMIN_AUTH_FILE=str(lm.CONFIG)))
            lm.main(["run", "--no-dns", "--https-port", "19443", "--db", "中文.db"])
        argv, auth = observed[0]
        self.assertIsNone(auth)
        self.assertNotIn("--dns", argv)
        self.assertNotIn("--no-dns", argv)
        self.assertNotIn("--res-version", argv)
        self.assertEqual(argv[-4:], ["--https-port", "19443", "--db", "中文.db"])

    def test_termux_prepare_never_invokes_pip_or_venv(self):
        with patch.object(lm, "is_termux", return_value=True), patch.object(lm.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "pkg install"):
            lm.prepare_environment()
        run.assert_not_called()

    def test_service_template_preserves_python_and_quotes_paths(self):
        target = self.folder() / "中文 $folder %space"
        with patch.object(lm, "ROOT", target), patch.dict(sys.modules, {"pwd": Mock(getpwuid=Mock(return_value=Mock(pw_name="fixture")))}), patch.object(os, "getuid", return_value=1000, create=True):
            lm.generate_templates(self.config())
        unit = (target / "linux_generated/aethergazer.service").read_text("utf-8")
        self.assertIn("$$folder %%space", unit)
        self.assertIn("PYTHON_BIN=", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn('server.sh" run', unit)
        self.assertNotIn("run_server.sh", unit)
        self.assertNotIn("AG_ADMIN_AUTH_FILE", unit)

    def nginx_fixture(self):
        target = self.folder()
        confdir = target / "conf.d"
        confdir.mkdir()
        for file in ("sdk_cert.pem", "sdk_key.pem", "sdk_ca.crt"):
            (target / file).write_text("fixture")
        config = dict(self.config(), nginx=True, nginx_dir=str(confdir), https_port=18443, http_port=0, web_bind="127.0.0.1")
        config_path = target / "linux_config.json"
        config_path.write_text("old-config")
        return target, confdir / ng.NAME, config_path, config

    def nginx_mocks(self, target, path):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(ng.shutil, "which", return_value="/usr/sbin/nginx"))
        stack.enter_context(patch.object(ng, "pid_running", return_value=True))
        stack.enter_context(patch.object(ng, "dump_config", side_effect=lambda: f"# configuration file {path}:\n" + (path.read_text() if path.exists() else "")))
        stack.enter_context(patch.object(ng, "health"))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return stack

    def test_nginx_applies_independent_site_reload_only(self):
        target, path, settings, config = self.nginx_fixture()
        website = path.parent / "website.conf"
        website.write_text("unrelated website")
        with self.nginx_mocks(target, path), patch.object(ng, "command") as run:
            ng.apply_nginx(target, config, None, settings, lambda: lm.backup_write(settings, "new-config"))
        self.assertTrue(path.read_text().startswith(ng.MARKER))
        self.assertIn("proxy_ssl_verify on", path.read_text())
        self.assertEqual(website.read_text(), "unrelated website")
        self.assertEqual(run.call_args_list[0].args[0], ["nginx", "-s", "reload"])
        self.assertEqual(settings.read_text(), "new-config")

    def test_nginx_syntax_failure_restores_old_site_without_reload(self):
        target, path, settings, config = self.nginx_fixture()
        original = ng.MARKER + "\nold site"
        path.write_text(original)
        with self.nginx_mocks(target, path), patch.object(ng, "dump_config", side_effect=["", RuntimeError("syntax")]), patch.object(ng, "command") as run, self.assertRaisesRegex(RuntimeError, "syntax"):
            ng.apply_nginx(target, config, config, settings, Mock())
        self.assertEqual(path.read_text(), original)
        self.assertEqual(settings.read_text(), "old-config")
        run.assert_not_called()

    def test_nginx_reload_failure_archives_new_site_and_restores(self):
        target, path, settings, config = self.nginx_fixture()
        with self.nginx_mocks(target, path), patch.object(ng, "command", side_effect=[RuntimeError("reload"), ""]) as run, self.assertRaisesRegex(RuntimeError, "reload"):
            ng.apply_nginx(target, config, None, settings, Mock())
        self.assertFalse(path.exists())
        self.assertTrue(list((path.parent / "archive").glob("*.disabled")))
        self.assertEqual(settings.read_text(), "old-config")
        self.assertEqual(run.call_count, 2)

    def test_nginx_post_write_save_failure_restores_both_configs(self):
        target, path, settings, config = self.nginx_fixture()
        original = ng.MARKER + "\nold site"
        path.write_text(original)
        def fail():
            lm.backup_write(settings, "new-config")
            raise OSError("disk failure")
        with self.nginx_mocks(target, path), patch.object(ng, "command"), self.assertRaisesRegex(OSError, "disk failure"):
            ng.apply_nginx(target, config, config, settings, fail)
        self.assertEqual(path.read_text(), original)
        self.assertEqual(settings.read_text(), "old-config")

    def test_nginx_health_failure_does_not_commit(self):
        target, path, settings, config = self.nginx_fixture()
        commit = Mock()
        with self.nginx_mocks(target, path), patch.object(ng, "command"), patch.object(ng, "health", side_effect=RuntimeError("route")), self.assertRaisesRegex(RuntimeError, "route"):
            ng.apply_nginx(target, config, None, settings, commit)
        self.assertFalse(path.exists())
        commit.assert_not_called()

    def test_nginx_unincluded_directory_rejected(self):
        target, path, settings, config = self.nginx_fixture()
        with self.nginx_mocks(target, path), patch.object(ng, "dump_config", return_value=""), patch.object(ng, "command") as run, self.assertRaisesRegex(ValueError, "未被Nginx加载"):
            ng.apply_nginx(target, config, None, settings, Mock())
        self.assertFalse(path.exists())
        run.assert_not_called()

    def test_nginx_existing_unmanaged_site_is_never_overwritten(self):
        target, path, settings, config = self.nginx_fixture()
        path.write_text("somebody else's site")
        with self.nginx_mocks(target, path), self.assertRaisesRegex(ValueError, "拒绝接管"):
            ng.apply_nginx(target, config, None, settings, Mock())
        self.assertEqual(path.read_text(), "somebody else's site")

    def test_nginx_other_game_domain_conflict_rejected(self):
        target, path, settings, config = self.nginx_fixture()
        other = path.parent / "existing.conf"
        dump = f"# configuration file {other}:\nserver {{ server_name open.ys4fun.com; }}"
        with self.nginx_mocks(target, path), patch.object(ng, "dump_config", return_value=dump), self.assertRaisesRegex(ValueError, "其他站点"):
            ng.apply_nginx(target, config, None, settings, Mock())
        self.assertFalse(path.exists())

    def test_nginx_disable_archives_only_managed_site(self):
        target, path, settings, config = self.nginx_fixture()
        path.write_text(ng.MARKER + "\nold site")
        with self.nginx_mocks(target, path), patch.object(ng, "command") as run:
            ng.apply_nginx(target, dict(config, nginx=False, web_bind="0.0.0.0"), config, settings, Mock())
        self.assertFalse(path.exists())
        self.assertEqual(run.call_args.args[0], ["nginx", "-s", "reload"])

    def test_nginx_unsafe_path_rejected(self):
        with self.assertRaises(ValueError):
            ng.quoted_path(self.sandbox / "$bad")

    def test_nginx_normal_systemd_flags_allowed_custom_entry_rejected(self):
        pid = self.folder() / "nginx.pid"
        pid.write_text("1234")
        for flags, allowed in (("-g daemon on; master_process on;", True), ("-c/etc/other.conf", False), ("-g pid /tmp/other.pid;", False)):
            with self.subTest(flags=flags), patch.object(ng.os, "kill"), patch.object(Path, "exists", return_value=True), patch.object(Path, "read_bytes", return_value=("nginx: master process /usr/sbin/nginx " + flags).encode()):
                if allowed:
                    self.assertTrue(ng.pid_running(f"pid {pid};"))
                else:
                    with self.assertRaises(ValueError):
                        ng.pid_running(f"pid {pid};")


    def test_wizard_external_proxy_custom_domain_and_acme_config(self):
        output = io.StringIO()
        answers = [
            "public", "43.139.154.74",  # 模式与公网 IP
            "1",                         # 外部反向代理
            "ag.moriaruruka.com",        # 自定义域名
            "3",                         # 不使用 HTTPS (纯 HTTP / 独立生成配置)
            "", "", "", "", "",          # 端口
            "off",                       # CDN
            "AdminPassword123",          # 控制面板管理密码
            "AdminPassword123",          # 确认密码
            "y",                         # 确认
        ]
        target = self.folder()
        path = target / "linux_config.json"
        (target / "account.db").write_bytes(b"opaque-save-fixture")
        with contextlib.ExitStack() as stack:
            for attr, val in (("ROOT", target), ("CONFIG", path), ("ADMIN_AUTH_FILE", target / "linux_admin_auth.json")):
                stack.enter_context(patch.object(lm, attr, val))
            for fn in ("runtime_check", "firewall_report", "network_report", "check_ports", "probe_system_ports"):
                stack.enter_context(patch.object(lm, fn))
            stack.enter_context(patch.object(lm, "is_termux", return_value=False))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(output))
            lm.setup()

        config = lm.load_config(path)
        self.assertEqual(config["usage"], "public")
        self.assertEqual(config["host_ip"], "43.139.154.74")
        self.assertEqual(config["custom_domain"], "ag.moriaruruka.com")
        self.assertEqual(config["domain_proxy_mode"], "no_https")
        self.assertEqual(config["https_port"], 18443)

        conf_file = target / "nginx_aethergazer.conf"
        self.assertTrue(conf_file.is_file())
        conf_text = conf_file.read_text(encoding="utf-8")
        self.assertIn("ag.moriaruruka.com", conf_text)
        self.assertIn("43.139.154.74", conf_text)
        self.assertIn("location ^~ /.well-known/acme-challenge/", conf_text)

        out_text = output.getvalue()
        self.assertIn("http://ag.moriaruruka.com/web/control-panel.html", out_text)

    def test_wizard_semi_auto_proxy_injection(self):
        output = io.StringIO()
        target = self.folder()
        path = target / "linux_config.json"
        (target / "account.db").write_bytes(b"opaque-save-fixture")

        # 模拟宝塔站点配置文件
        vhost_dir = target / "vhost_nginx"
        vhost_dir.mkdir()
        site_conf = vhost_dir / "ag.moriaruruka.com.conf"
        site_conf.write_text("""server {
    listen 80;
    listen 443 ssl;
    server_name ag.moriaruruka.com;
    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    location / {
        root /www/wwwroot/ag.moriaruruka.com;
    }
}
""", encoding="utf-8")

        answers = [
            "public", "43.139.154.74",  # 模式与公网 IP
            "1",                         # 外部反向代理
            "ag.moriaruruka.com",        # 自定义域名
            "1",                         # 半自动反向代理
            str(site_conf),              # 站点配置文件绝对路径
            "", "", "", "", "",          # 端口
            "off",                       # CDN
            "AdminPassword123",          # 控制面板管理密码
            "AdminPassword123",          # 确认密码
            "y",                         # 确认
        ]
        with contextlib.ExitStack() as stack:
            for attr, val in (("ROOT", target), ("CONFIG", path), ("ADMIN_AUTH_FILE", target / "linux_admin_auth.json")):
                stack.enter_context(patch.object(lm, attr, val))
            for fn in ("runtime_check", "firewall_report", "network_report", "check_ports", "probe_system_ports"):
                stack.enter_context(patch.object(lm, fn))
            stack.enter_context(patch.object(lm, "is_termux", return_value=False))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(output))
            lm.setup()

        config = lm.load_config(path)
        self.assertEqual(config["domain_proxy_mode"], "semi")
        self.assertEqual(config["site_conf_path"], str(site_conf.resolve()))

        # 检查原站点配置是否成功注入反代规则
        updated_site_text = site_conf.read_text(encoding="utf-8")
        self.assertIn("# --- AetherGazer Web GM Proxy Start ---", updated_site_text)
        self.assertIn("proxy_pass https://127.0.0.1:18443;", updated_site_text)

        # 检查是否成功生成备份文件
        baks = list(vhost_dir.glob("ag.moriaruruka.com.conf.bak.*"))
        self.assertTrue(len(baks) >= 1)

        # 检查游戏专用反代配置是否已复制且绝不包含面板域名
        game_conf = vhost_dir / "nginx_aethergazer.conf"
        self.assertTrue(game_conf.is_file())
        game_conf_text = game_conf.read_text(encoding="utf-8")
        self.assertNotIn("ag.moriaruruka.com", game_conf_text)
        self.assertIn("open.ys4fun.com", game_conf_text)
        self.assertIn("43.139.154.74", game_conf_text)

    def test_wizard_manual_proxy(self):
        output = io.StringIO()
        target = self.folder()
        path = target / "linux_config.json"
        (target / "account.db").write_bytes(b"opaque-save-fixture")

        answers = [
            "public", "43.139.154.74",  # 模式与公网 IP
            "1",                         # 外部反向代理
            "ag.moriaruruka.com",        # 自定义域名
            "2",                         # 手动配置反向代理
            "", "", "", "", "",          # 端口
            "off",                       # CDN
            "AdminPassword123",          # 控制面板管理密码
            "AdminPassword123",          # 确认密码
            "y",                         # 确认
        ]
        with contextlib.ExitStack() as stack:
            for attr, val in (("ROOT", target), ("CONFIG", path), ("ADMIN_AUTH_FILE", target / "linux_admin_auth.json")):
                stack.enter_context(patch.object(lm, attr, val))
            for fn in ("runtime_check", "firewall_report", "network_report", "check_ports", "probe_system_ports"):
                stack.enter_context(patch.object(lm, fn))
            stack.enter_context(patch.object(lm, "is_termux", return_value=False))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(output))
            lm.setup()

        config = lm.load_config(path)
        self.assertEqual(config["domain_proxy_mode"], "manual")
        # 验证生成的游戏配置绝不包含面板域名
        game_conf = target / "nginx_aethergazer.conf"
        self.assertTrue(game_conf.is_file())
        game_conf_text = game_conf.read_text(encoding="utf-8")
        self.assertNotIn("ag.moriaruruka.com", game_conf_text)
        self.assertIn("43.139.154.74", game_conf_text)

        out_text = output.getvalue()
        self.assertIn("[手动配置反向代理指南]", out_text)
        self.assertIn("宝塔面板用户 (推荐可视化操作)", out_text)

    def test_wizard_semi_auto_retry_path(self):
        output = io.StringIO()
        target = self.folder()
        path = target / "linux_config.json"
        (target / "account.db").write_bytes(b"opaque-save-fixture")

        site_conf = target / "real_site.conf"
        site_conf.write_text("server {\n    server_name ag.moriaruruka.com;\n}\n", encoding="utf-8")

        answers = [
            "public", "43.139.154.74",
            "1",
            "ag.moriaruruka.com",
            "1",
            "/invalid/path/not_exist.conf",  # 第一次输入不存在路径
            str(site_conf),                  # 第二次输入有效路径
            "", "", "", "", "",
            "off",
            "AdminPassword123",          # 控制面板管理密码
            "AdminPassword123",          # 确认密码
            "y",
        ]
        with contextlib.ExitStack() as stack:
            for attr, val in (("ROOT", target), ("CONFIG", path), ("ADMIN_AUTH_FILE", target / "linux_admin_auth.json")):
                stack.enter_context(patch.object(lm, attr, val))
            for fn in ("runtime_check", "firewall_report", "network_report", "check_ports", "probe_system_ports"):
                stack.enter_context(patch.object(lm, fn))
            stack.enter_context(patch.object(lm, "is_termux", return_value=False))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(output))
            lm.setup()

        out_text = output.getvalue()
        self.assertIn("未找到指定文件", out_text)
        self.assertIn("已锁定站点配置文件", out_text)

    def test_wizard_semi_auto_ctrl_c(self):
        output = io.StringIO()
        target = self.folder()
        path = target / "linux_config.json"
        (target / "account.db").write_bytes(b"opaque-save-fixture")

        answers = [
            "public", "43.139.154.74",
            "1",
            "ag.moriaruruka.com",
            "1",
            KeyboardInterrupt(),
        ]

        with contextlib.ExitStack() as stack:
            for attr, val in (("ROOT", target), ("CONFIG", path)):
                stack.enter_context(patch.object(lm, attr, val))
            for fn in ("runtime_check", "firewall_report", "network_report", "check_ports", "probe_system_ports"):
                stack.enter_context(patch.object(lm, fn))
            stack.enter_context(patch.object(lm, "is_termux", return_value=False))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(output))
            with self.assertRaises(SystemExit) as cm:
                lm.setup()
            self.assertEqual(cm.exception.code, 0)
        self.assertIn("已通过 Ctrl+C 中断配置向导", output.getvalue())

    def test_inject_proxy_into_site_conf_unit(self):
        folder = self.folder()
        conf = folder / "test_site.conf"

        # Case 1: Existing server block
        conf.write_text("server {\n    server_name test.com;\n}\n", encoding="utf-8")
        bak = ng.inject_proxy_into_site_conf(conf, 18443, "test.com")
        self.assertTrue(bak.is_file())
        text = conf.read_text(encoding="utf-8")
        self.assertIn("location ^~ /", text)
        self.assertIn("proxy_pass https://127.0.0.1:18443;", text)

        # Case 2: Idempotent re-injection replaces existing block
        ng.inject_proxy_into_site_conf(conf, 19443, "test.com")
        text2 = conf.read_text(encoding="utf-8")
        self.assertEqual(text2.count("location ^~ /"), 1)
        self.assertIn("proxy_pass https://127.0.0.1:19443;", text2)

        # Case 3: Empty file or without server block
        conf_empty = folder / "empty.conf"
        conf_empty.write_text("", encoding="utf-8")
        ng.inject_proxy_into_site_conf(conf_empty, 18443, "empty.com")
        empty_text = conf_empty.read_text(encoding="utf-8")
        self.assertIn("server {", empty_text)
        self.assertIn("server_name empty.com;", empty_text)
        self.assertIn("proxy_pass https://127.0.0.1:18443;", empty_text)

    def test_is_server_process_validation(self):
        target = self.sandbox / "proc_test"
        target.mkdir()
        # Invalid PIDs
        self.assertFalse(lm.is_server_process(0, target))
        self.assertFalse(lm.is_server_process(-1, target))
        self.assertFalse(lm.is_server_process(os.getpid(), target))

    def test_get_running_server_pid_and_stop_when_stopped(self):
        target = self.sandbox / "stop_test"
        target.mkdir()
        pid_file = target / ".server.pid"

        # Not running, no pid file
        self.assertIsNone(lm.get_running_server_pid(target))
        stopped, msg = lm.stop_server_process(target)
        self.assertFalse(stopped)
        self.assertIn("未在运行", msg)

        # Stale pid file pointing to non-server PID
        pid_file.write_text("999999", "utf-8")
        with patch.object(lm, "is_server_process", return_value=False):
            self.assertIsNone(lm.get_running_server_pid(target))
            self.assertFalse(pid_file.exists())  # Cleaned up

    def test_stop_server_process_when_running(self):
        target = self.sandbox / "stop_running"
        target.mkdir()
        pid_file = target / ".server.pid"
        pid_file.write_text("12345", "utf-8")

        with patch.object(lm, "is_server_process", return_value=True), \
             patch("os.kill") as mock_kill, \
             patch("shutil.which", return_value=None):
            # os.kill(pid, 0) simulates process terminating after SIGTERM
            mock_kill.side_effect = [None, ProcessLookupError()]
            stopped, msg = lm.stop_server_process(target)
            self.assertTrue(stopped)
            self.assertIn("已安全停止", msg)
            self.assertIn("12345", msg)
            self.assertFalse(pid_file.exists())

    def test_server_status_output(self):
        target = self.sandbox / "status_test"
        target.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(lm, "get_running_server_pid", return_value=None):
            lm.print_server_status(target)
        self.assertIn("游戏服务端当前未在运行", out.getvalue())

        out_running = io.StringIO()
        with contextlib.redirect_stdout(out_running), patch.object(lm, "get_running_server_pid", return_value=12345):
            lm.print_server_status(target)
        self.assertIn("游戏服务端正在运行", out_running.getvalue())
        self.assertIn("12345", out_running.getvalue())

    def test_probe_system_ports_gateway_pool_and_services(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(lm, "is_port_occupied", return_value=False):
            lm.probe_system_ports(8102, 8105)
        text = out.getvalue()
        self.assertIn("HTTP", text)
        self.assertIn("HTTPS", text)
        self.assertIn("8105", text)
        self.assertIn("6105", text)
        self.assertIn("53", text)
        self.assertIn("8102-8134", text)
        self.assertIn("全部空闲可用 (共 32 个槽位端口)", text)

        out_occ = io.StringIO()
        def mock_occupied(port, proto="tcp", host="0.0.0.0"):
            return port in (8110, 443)
        with contextlib.redirect_stdout(out_occ), \
             patch.object(lm, "is_port_occupied", side_effect=mock_occupied), \
             patch.object(lm, "find_port_process", return_value="test_proc (PID: 999)"):
            lm.probe_system_ports(8102, 8105)
        occ_text = out_occ.getvalue()
        self.assertIn("443", occ_text)
        self.assertIn("test_proc (PID: 999)", occ_text)
        self.assertIn("发现占用！", occ_text)
        self.assertIn("8110", occ_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
