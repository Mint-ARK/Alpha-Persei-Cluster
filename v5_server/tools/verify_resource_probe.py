"""Bounded metadata/channel probes. All upstream and manifest reads are mocked."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import res_version_manager as versions


def response_items(platform):
    return [
        dict(type='noInstall', platformType=platform, matchedAppVersion='308', version='321',
             versionName='v5.3.1', extraData=json.dumps({'assethash': 'assethash_308_321_' + 'a' * 32 + '.bytes'})),
        dict(type='install', platformType=platform, version='308', versionName='308'),
        dict(type='noInstall', platformType=platform, matchedAppVersion='307', version='331',
             versionName='v5.3.1', assethash='assethash_307_331_' + 'b' * 32 + '.bytes'),
    ]


class OfficialProbeChecks(unittest.TestCase):
    def test_01_channels_and_signature_include_actual_platform_channel(self):
        for platform, channel in [('pc', '9'), ('android', '8'), ('ios', '7')]:
            with self.subTest(platform=platform), patch('cdn_proxy.fetch_upstream') as fetch:
                fetch.return_value = 200, {}, json.dumps({'errorCode': '0', 'data': response_items(platform)}).encode()
                ok, info, _ = versions.probe_official_latest(platform)
                self.assertTrue(ok)
                params = {k: v[0] for k, v in parse_qs(fetch.call_args.kwargs['query']).items()}
                self.assertEqual(params['channelId'], channel)
                self.assertEqual(params['platformType'], platform)
                signature = params.pop('signature')
                text = ''.join(f'{key}={params[key]}&' for key in sorted(params)) + f'appSecret={versions.OFFICIAL_APP_SECRET}'
                self.assertEqual(signature, hashlib.md5(text.encode()).hexdigest())
                self.assertEqual(info['channel_id'], channel)

    def test_02_preserve_all_rows_and_choose_new_app_not_last_old_resource(self):
        items = response_items('ios')
        items.append({**items[0], 'matchedAppVersion': '999', 'channelId': '9'})
        items.append({**items[0], 'matchedAppVersion': '999', 'platformType': 'android'})
        with patch('cdn_proxy.fetch_upstream', return_value=(200, {}, json.dumps({'data': items}).encode())):
            ok, info, _ = versions.probe_official_latest('ios')
        self.assertTrue(ok)
        self.assertEqual((info['app_version'], info['version'], info['install_version']), ('308', '321', '308'))
        self.assertEqual(info['assethash'], 'assethash_308_321_' + 'a' * 32 + '.bytes')
        self.assertEqual(len(info['candidates']), 3)
        self.assertEqual(info['candidates'][2]['app_version'], '307')

    def test_03_error_code_without_code_is_not_success(self):
        with patch('cdn_proxy.fetch_upstream', return_value=(200, {}, b'{"errorCode":"500","data":[]}')):
            ok, _, message = versions.probe_official_latest('ios')
        self.assertFalse(ok)
        self.assertIn('500', message)

    def test_04_same_resource_build_new_app_uses_full_metadata(self):
        local_name = versions.get_assethash('ios', '321')
        remote_name = 'assethash_308_321_' + 'a' * 32 + '.bytes'
        remote = dict(platform='ios', channel_id='7', version='321', version_name='v5.3.1',
                      app_version='308', assethash=remote_name, candidates=response_items('ios'), install_version='308')
        manifest = {'appVersion': 307, 'versionName': 'v5.3.1', 'assetHashList': ['keep|' + 'a' * 32 + '|10']}
        with patch.object(versions, '_load_manifest_content', side_effect=[manifest, {**manifest, 'appVersion': 308}]):
            result = versions.calculate_manifest_diff('321', '321', 'ios', remote_metadata=remote)
        self.assertTrue(result['has_update'])
        self.assertEqual((result['local_app_version'], result['remote_app_version']), ('307', '308'))
        self.assertEqual((result['local_assethash'], result['remote_assethash']), (local_name, remote_name))
        self.assertEqual(result['channel_id'], '7')
        self.assertEqual(result['diff_summary']['modified_count'], 0)

    def test_05_metadata_survives_manifest_download_failure_in_gm_api(self):
        from flask import Flask
        import gm_api
        app = Flask(__name__)
        app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        data = dict(success=False, error='isolated manifest unavailable', remote_assethash='full_308_321.bytes',
                    local_assethash='full_307_321.bytes', remote_app_version='308', local_app_version='307',
                    remote_version='321', remote_version_name='v5.3.1', local_version='321', channel_id='7',
                    remote_candidates=[{'assethash': 'full_308_321.bytes'}])
        with patch('resource_distribution.get_platform_config', return_value={'version': '321'}), \
             patch.object(versions, 'calculate_manifest_diff', return_value=data):
            result = app.test_client().get('/api/gm/system/check_update?platform=ios').get_json()
        self.assertEqual(result['code'], 0)
        self.assertFalse(result['data']['diff_ready'])
        self.assertEqual(result['data']['remote_app_version'], '308')
        self.assertEqual(result['data']['remote_assethash'], 'full_308_321.bytes')


if __name__ == '__main__':
    unittest.main(verbosity=2)
