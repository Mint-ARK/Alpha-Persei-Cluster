"""Integration verification for Linux Setup mandatory password & Web Panel lock screen."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import admin_access as access
import admin_session_auth as auth
import linux_manager as lm


class PanelPasswordIntegrationTests(unittest.TestCase):
    def test_linux_admin_password_validation_and_origin_derivation(self):
        # 1. Test derivation of origin
        config_domain = {"custom_domain": "panel.aethergazer.io", "https_port": 18443}
        origin_proxy = lm.derive_admin_origin(config_domain, "proxy")
        self.assertEqual(origin_proxy, "https://panel.aethergazer.io")

        origin_direct = lm.derive_admin_origin(config_domain, "direct")
        self.assertEqual(origin_direct, "https://panel.aethergazer.io:18443")

        config_ip = {"host_ip": "1.2.3.4", "https_port": 18443, "custom_domain": ""}
        self.assertEqual(lm.derive_admin_origin(config_ip, "direct"), "https://1.2.3.4:18443")
        self.assertEqual(lm.derive_admin_origin(config_ip, "proxy"), "https://1.2.3.4")

        # 2. Test password length check
        test_pwd = "MySuperSecretAdminPassword2026!"
        pwd_hash = auth.hash_password(test_pwd)
        self.assertTrue(isinstance(pwd_hash, str) and pwd_hash.startswith("pbkdf2_sha256$"))

        # < 12 characters must be rejected
        with self.assertRaises(ValueError):
            auth.hash_password("short123")

    def test_config_args_excludes_admin_auth_configured(self):
        # admin_auth_configured must never be emitted into main.py arguments
        cfg = dict(lm.DEFAULTS, admin_auth_configured=True)
        args = lm.config_args(cfg)
        self.assertNotIn("--admin-auth-configured", args)

    def test_static_web_bundle_contains_lock_screen_assets(self):
        # Verify compiled frontend bundle contains lock screen and auth logic
        app_js = ROOT / "static_web/app.js"
        self.assertTrue(app_js.is_file(), "static_web/app.js must exist")
        js_text = app_js.read_text(encoding="utf-8")

        self.assertIn("/api/gm/auth/session", js_text)
        self.assertIn("/api/gm/auth/login", js_text)
        self.assertIn("SESSION SHIELD ACTIVE", js_text)
        self.assertIn("PBKDF2-SHA256", js_text)

        panel_html = ROOT / "static_web/control-panel.html"
        self.assertTrue(panel_html.is_file(), "static_web/control-panel.html must exist")
        html_text = panel_html.read_text(encoding="utf-8")
        self.assertIn("/api/gm/auth/login", html_text)


if __name__ == "__main__":
    unittest.main()
