# -*- coding: utf-8 -*-
"""Offline tests for Bilibili PC SDK, game-website-server, certs, and channel 10 alignment."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bilibili_sdk
import gen_cert
import server_net


class BiliSdkAndManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core = SimpleNamespace(log=lambda *a, **kw: None, db=None)
        cls.app = server_net.build_https_app(core, host_ip="127.0.0.1")
        cls.client = cls.app.test_client()

    def test_game_website_launcher_routes(self):
        expected = {
            "/game-website-server/pass/sdk/get_index_config?gameId=2": lambda d: "versionImage" in d and "ageTip" in d,
            "/game-website-server/pass/sdk/get_menu_info?gameId=2": lambda d: d == [],
            "/game-website-server/pass/sdk/get_news?type=notice&gameId=2": lambda d: d == [],
            "/game-website-server/pass/sdk/get_slide_image?gameId=2": lambda d: d == [],
            "/game-website-server/pass/sdk/get_update_version?gameId=2": (
                lambda d: d.get("gameUpdateInfo", {}).get("currentVersion") == "308"
                and d.get("pcLauncherUpdateInfo", {}).get("currentVersion") == "1.9"
            ),
        }
        for url, validator in expected.items():
            resp = self.client.get(url, headers={"Host": "open.ys4fun.com"})
            self.assertEqual(resp.status_code, 200, url)
            payload = resp.get_json()
            self.assertEqual(payload["errorCode"], "0", url)
            self.assertTrue(validator(payload["data"]), f"unexpected payload for {url}: {payload}")

        img_resp = self.client.get(
            "/game-site/image/placeholder.png",
            headers={"Host": "web-static.ys4fun.com"},
        )
        self.assertEqual(img_resp.status_code, 200)
        self.assertTrue(img_resp.data.startswith(b"\x89PNG"))

    def test_bili_pc_sdk_routes_and_session_continuity(self):
        hot_cfg = self.client.get(
            "/tool/pcgame/sdkHotConfig_pc_v2.json",
            headers={"Host": "pkgdl.biligame.net"},
        )
        self.assertEqual(hot_cfg.status_code, 200)
        self.assertEqual(hot_cfg.get_json(), {"nativeLoginType": [], "collectHardInfo": []})

        agreement = self.client.post(
            "/api/agreement/config?game_id=10503&request_id=req123",
            headers={"Host": "line3-sdk-app-api.biligame.net"},
        )
        self.assertEqual(agreement.status_code, 200)
        agr_data = agreement.get_json()
        self.assertEqual(agr_data["code"], 0)
        self.assertTrue(agr_data["success"])
        self.assertEqual(agr_data["data"]["agreement_switch"], "OFF")

        country = self.client.get(
            "/api/external/country/list?game_id=10503",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(country["code"], 0)
        self.assertTrue(country["data"]["country_list"])

        rsa_resp = self.client.post(
            "/api/pcg/rsa?game_id=10503",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(rsa_resp["code"], 0)
        self.assertEqual(len(rsa_resp["hash"]), 16)
        pub_key = serialization.load_pem_public_key(rsa_resp["rsa_key"].encode("ascii"))
        self.assertEqual(pub_key.key_size, 1024)

        login_resp = self.client.post(
            "/api/pcg/login?game_id=10503&user_id=test_offline_user&pwd=dummy",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(login_resp["code"], 0)
        access_key = login_resp["access_key"]
        bili_uid = login_resp["uid"]
        self.assertIsInstance(bili_uid, int)
        self.assertEqual(login_resp["game_open_id"], "")
        self.assertFalse(login_resp["game_open_id_enable"])
        self.assertTrue(access_key.endswith("_sh"))

        auto_resp = self.client.post(
            f"/api/pcg/auto.login?game_id=10503&access_key={access_key}",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(auto_resp["code"], 0)
        self.assertIsInstance(auto_resp["uid"], int)
        self.assertEqual(auto_resp["uid"], bili_uid)
        self.assertEqual(auto_resp["game_open_id"], "")
        self.assertFalse(auto_resp["game_open_id_enable"])

        cached_unknown = self.client.post(
            "/api/pcg/auto.login?game_id=10503&access_key=synthetic_cached_key_001_sh",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(cached_unknown["code"], 0)
        self.assertIsInstance(cached_unknown["uid"], int)
        self.assertGreater(cached_unknown["uid"], 0)

        base_id_resp = self.client.get(
            "/api/pcg/common/getGameBaseId?game_id=10503",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(base_id_resp["code"], 0)
        self.assertIsInstance(base_id_resp["game_base_id"], int)

        dlc_resp = self.client.get(
            "/api/pcg/config/dlc?game_id=10503",
            headers={"Host": "wpg-api.biligame.com"},
        ).get_json()
        self.assertEqual(dlc_resp["code"], 0)
        self.assertIsInstance(dlc_resp["product"], list)
        self.assertEqual(dlc_resp["game_open_id"], "")
        self.assertFalse(dlc_resp["game_open_id_enable"])

        for host, path in (
            ("wpg-api.biligame.com", "/api/pcg/start_captcha?game_id=10503"),
            ("line3-realtime-api.biligame.net", "/app/pcg/time/conf?app_id=10503"),
            ("realtime.api.biligame.com", "/app/pcg/time/heartbeat?app_id=10503"),
            ("wpg.biligame.com", "/cg/api/client/can.pay?game_id=10503"),
            ("sdk.biligame.com", "/health/?cef=true"),
        ):
            r = self.client.get(path, headers={"Host": host})
            self.assertEqual(r.status_code, 200, (host, path))

        for host, path in (
            ("wpg-api.biligame.com", "/api/pcg/zone/notify?game_id=10503"),
            ("line3-log.biligame.net", "/collector/api/report"),
        ):
            r = self.client.post(path, headers={"Host": host}, json={"ok": 1})
            self.assertEqual(r.status_code, 200, (host, path))

    def test_mix_login_logout_and_pc_channel10_distribution(self):
        info_json = json.dumps({"channelToken": "tok_offline_1", "channelUserId": "90012345"})
        login = self.client.post(
            "/mix-sdk-api/pass/user/login",
            data={"platformType": "pc", "channelId": "10", "channelLoginRespInfo": info_json},
            headers={"Host": "open.ys4fun.com"},
        )
        self.assertEqual(login.status_code, 200)
        ld = login.get_json()["data"]
        self.assertEqual(ld["channelId"], 10)
        self.assertEqual(ld["targetChannelId"], 6)
        self.assertEqual(ld["channelUserId"], "90012345")

        logout = self.client.post("/mix-sdk-api/pass/user/logout", headers={"Host": "open.ys4fun.com"})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.get_json(), {"errorCode": "0"})

        import resource_distribution

        expected_pc = resource_distribution.get_platform_config("pc")
        latest = self.client.get(
            "/mix-sdk-api/pass/game/updateversion/getLatest?platformType=pc&channelId=10",
            headers={"Host": "open.ys4fun.com"},
        ).get_json()
        self.assertEqual(latest["errorCode"], "0")
        no_install = next(e for e in latest["data"] if e.get("type") == "noInstall")
        self.assertEqual(no_install["matchedAppVersion"], expected_pc.get("matched_app_version", "307"))
        self.assertEqual(no_install["version"], str(expected_pc["version"]))
        self.assertEqual(no_install["assethash"], expected_pc["assethash"]["pc"])

        prefix = no_install["downloadUrl"].split(";")[0].split("ys4fun.com", 1)[1]
        for fname in (
            no_install["assethash"],
            expected_pc["voice_package_list"],
            expected_pc["voice_hashes"]["zh"],
            expected_pc["voice_hashes"]["ja"],
        ):
            r = self.client.get(prefix + fname, headers={"Host": "download-eo.ys4fun.com"})
            self.assertEqual(r.status_code, 200, fname)
            self.assertGreater(len(r.data), 50, fname)

    def test_hosts_domains_cert_sans_and_server_config_cleanliness(self):
        required_bili = {
            "wpg-api.biligame.com",
            "line3-sdk-app-api.biligame.net",
            "line1-realtime-api.biligame.net",
            "line3-realtime-api.biligame.net",
            "realtime.api.biligame.com",
            "pkgdl.biligame.net",
            "line3-log.biligame.net",
            "web-static.ys4fun.com",
        }
        forbidden = {"www.bilibili.com", "passport.bilibili.com"}

        main_cs = (REPO_ROOT / "LocalServer" / "MainPage.xaml.cs").read_text(encoding="utf-8")
        m = re.search(r"RequiredHostsDomains\s*=\s*\[(.*?)\];", main_cs, re.S)
        self.assertIsNotNone(m)
        hosts_domains = set(re.findall(r'"([^"]+)"', m.group(1)))
        self.assertTrue(required_bili.issubset(hosts_domains), required_bili - hosts_domains)
        self.assertFalse(hosts_domains & forbidden)

        self.assertTrue(required_bili.issubset(set(gen_cert.CERT_DNS_NAMES)))
        self.assertFalse(set(gen_cert.CERT_DNS_NAMES) & forbidden)

        with tempfile.TemporaryDirectory() as tmpdir:
            ok = gen_cert.generate_dual_layer_certs(
                output_dir=tmpdir,
                extra_ips=["127.0.0.1"],
                log=lambda *a, **k: None,
                force=True,
                force_ca=True,
            )
            self.assertTrue(ok)
            cert = x509.load_pem_x509_certificate((Path(tmpdir) / "sdk_cert.pem").read_bytes())
        san_names = set(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
        self.assertTrue(required_bili.issubset(san_names), required_bili - san_names)

        cfg = json.loads((ROOT / "server_config.json").read_text(encoding="utf-8"))
        read_roots = cfg.get("storage", {}).get("cdn_read_roots", [])
        self.assertFalse(any("selftest_tmp" in str(r) for r in read_roots))


if __name__ == "__main__":
    unittest.main(verbosity=2)
