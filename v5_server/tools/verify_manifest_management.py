"""Offline acceptance for native-only BASEs and isolated platform distribution."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import resource_manifests as manager
import resource_distribution as distribution
import res_version_manager as versions
import cdn_proxy
import gm_api
from flask import Flask


def fixture(root):
    """A tiny real manifest/voice group per native platform/build."""
    sources = []
    for platform in manager.PLATFORMS:
        for build in ('229', '321') + (('311',) if platform == 'pc' else ()):
            folder = root / 'static_resources' / platform
            folder.mkdir(parents=True, exist_ok=True)
            version = '30700' + build
            voice_files = {}
            for lang in ('ja', 'zh'):
                name = f'voice_hash_{lang}_{version}.bytes'
                payload = f'{platform}-{build}-{lang}'.encode()
                raw = manager._json_bytes(dict(version=int(version), versionName='native',
                    assetHashList=[f'voice/{lang}|{hashlib.md5(payload).hexdigest()}|{len(payload)}']))
                (folder / name).write_bytes(raw)
                voice_files[name] = hashlib.sha256(raw).hexdigest()
            name = f'voice_package_list_307_{build}.bytes'
            voice_raw = manager._json_bytes(dict(infos=[dict(name=lang, version=int(version), size=100) for lang in ('ja', 'zh')]))
            (folder / name).write_bytes(voice_raw)
            voice_files[name] = hashlib.sha256(voice_raw).hexdigest()
            payload = f'{platform}-{build}-asset'.encode()
            data = dict(appVersion=307, buildCode=int(build), versionName='v5.2.1' if build == '229' else 'v5.3.1',
                channel=0, assetHashList=[f'voice_package_list.bytes|{hashlib.md5(voice_raw).hexdigest()}|{len(voice_raw)}',
                    f'asset.ys|{hashlib.md5(payload).hexdigest()}|{len(payload)}'], assetPendDict={'keep': ['unchanged']})
            raw = manager._json_bytes(data)
            digest = hashlib.md5(raw).hexdigest()
            filename = f'assethash_307_{build}_{digest}.bytes'
            (folder / filename).write_bytes(raw)
            sources.append(dict(id=f'native-{platform}-{digest[:12]}', platform=platform,
                channel_id=manager.CHANNELS[platform], app_version='307', resource_version=build,
                version_name=data['versionName'], assethash=filename, sha256=hashlib.sha256(raw).hexdigest(),
                voice_files=voice_files, provenance='bundled-official'))
    (root / 'native_manifest_sources.json').write_text(json.dumps({'version': 1, 'sources': sources}), encoding='utf-8')
    return sources


class ManifestChecks(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir()
        self.sources = fixture(self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (('ROOT', self.root), ('CONFIG_PATH', self.root / 'resource_distribution.json'),
                            ('NATIVE_PATH', self.root / 'native_manifest_sources.json'),
                            ('_startup', {}), ('_pc_hint', None), ('_route_rows', {}), ('_route_stamp', None)):
            self.stack.enter_context(patch.object(manager, name, value))
        self.stack.enter_context(patch.object(manager, '_windows_host', return_value=False))
        self.stack.enter_context(patch.object(manager, '_recycle', side_effect=self.recycle))

    def recycle(self, path, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)

    def native(self, platform='pc', build='229'):
        return next(s for s in self.sources if s['platform'] == platform and s['resource_version'] == build)

    def inputs(self, platform='pc', build='229', **kwargs):
        return {**dict(platform=platform, channel_id=manager.CHANNELS[platform], base_id=self.native(platform, build)['id'],
            target_app_version='308', target_resource_version='331', label=''), **kwargs}

    def generate(self, platform='pc', build='229', **kwargs):
        data = self.inputs(platform, build, **kwargs)
        preview = manager.preview_profile(data)
        return manager.generate_profile({**data, 'preview_token': preview['preview_token']})['profile']

    def apply(self, profile):
        return manager.save_platform(dict(platform=profile['platform'], selected_id=profile['id'],
            fallback_id=self.native(profile['platform'], '321')['id']))

    def test_01_preview_is_readonly_and_generation_is_not_application(self):
        data = self.inputs()
        original = {p: p.read_bytes() for p in (self.root / 'static_resources/pc').glob('*.bytes')}
        preview = manager.preview_profile(data)
        self.assertFalse(manager.CONFIG_PATH.exists())
        self.assertFalse((self.root / 'static_resources/pc/generated').exists())
        result = manager.generate_profile({**data, 'preview_token': preview['preview_token']})
        row = result['profile']
        self.assertEqual(row['origin'], 'derived')
        self.assertEqual(result['catalogue']['platforms']['pc']['effective']['resource_version'], '321')
        raw = manager._main_path({**row, 'origin': 'derived'}).read_bytes()
        self.assertEqual(hashlib.md5(raw).hexdigest(), preview['md5'])
        self.assertEqual(row['assethash'], preview['assethash'])
        source = json.loads((self.root / 'static_resources/pc' / self.native()['assethash']).read_bytes())
        self.assertEqual(json.loads(raw), {**source, 'appVersion': 308, 'buildCode': 331})
        for path, before in original.items():
            self.assertEqual(path.read_bytes(), before)

    def test_02_only_native_can_be_base_and_import_does_not_promote(self):
        derived = self.generate()
        data = self.inputs()
        with self.assertRaisesRegex(ValueError, 'BASE'):
            manager.preview_profile({**data, 'base_id': derived['id'], 'origin': 'native'})
        name = derived['assethash']
        target = self.root / 'static_resources/pc' / name
        target.write_bytes(manager._main_path(derived).read_bytes())
        with self.assertRaises(ValueError):
            manager.preview_profile({**data, 'base_id': name})
        self.assertFalse(any(p['origin'] == 'native' and p['assethash'] == name for p in manager.get_status()['profiles']))

    def test_03_native_identity_tamper_is_rejected(self):
        source = self.native()
        path = self.root / 'static_resources/pc' / source['assethash']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'BASE'):
            manager.preview_profile(self.inputs())

    def test_04_stale_preview_and_channel_mismatch(self):
        data = self.inputs()
        preview = manager.preview_profile(data)
        with self.assertRaisesRegex(ValueError, '预览'):
            manager.generate_profile({**data, 'target_resource_version': '332', 'preview_token': preview['preview_token']})
        with self.assertRaises(ValueError):
            manager.preview_profile({**data, 'channel_id': '7'})
        for version in (True, 0, '../308', '0308', '10000', 308.0):
            with self.subTest(version=version), self.assertRaises(ValueError):
                manager.preview_profile({**data, 'target_app_version': version})

    def test_05_channels_and_gameplay_are_independent(self):
        row = self.generate('ios')
        with patch.object(versions, '_CURRENT_VERSION', '229'):
            status = self.apply(row)
            self.assertEqual(status['gameplay_version'], '229')
        self.assertEqual(status['platforms']['ios']['effective_id'], row['id'])
        self.assertEqual(status['platforms']['pc']['effective']['resource_version'], '321')
        self.assertEqual(status['platforms']['android']['effective']['resource_version'], '321')
        with self.assertRaises(ValueError):
            manager.save_platform(dict(platform='android', selected_id=row['id'], fallback_id=self.native('android','321')['id']))

    def test_06_latest_excludes_derived_numbers_and_fallback_is_real(self):
        data = self.inputs(); data['target_resource_version'] = '9999'
        preview = manager.preview_profile(data)
        row = manager.generate_profile({**data, 'preview_token': preview['preview_token']})['profile']
        self.assertEqual(manager.get_status()['platforms']['pc']['effective']['resource_version'], '321')
        self.apply(row)
        manager._main_path(row).write_bytes(b'corrupt')
        status = manager.get_status()['platforms']['pc']
        self.assertTrue(status['fallback_active'])
        self.assertEqual(status['selected_id'], self.native('pc','321')['id'])
        self.assertEqual(manager.resolve_resource(f'/pc/resources/profiles/{row["id"]}/{row["assethash"]}', 'pc'), (True, None))

    def test_07_remove_active_profile_recycles_and_cannot_recapture(self):
        row = self.generate(); self.apply(row)
        raw = manager._main_path(row).read_bytes()
        status = manager.remove_profile(row['id'])
        self.assertEqual(status['platforms']['pc']['effective_id'], self.native('pc','321')['id'])
        self.assertFalse(manager._main_path(row).exists())
        self.assertEqual(next((self.root/'archive/resource_manifests/recovery').glob('*/*')).read_bytes(), raw)
        namespace = f'/pc/resources/profiles/{row["id"]}/{row["assethash"]}'
        self.assertEqual(manager.resolve_resource(namespace, 'pc'), (True, None))
        self.assertEqual(manager.resolve_resource('/pc/resources/' + row['assethash'], 'pc'), (True, None))
        with patch.object(cdn_proxy, 'download_verified', side_effect=AssertionError('removed recapture')):
            self.assertEqual(cdn_proxy.handle_resource_request(namespace).status_code, 404)
        self.assertTrue((self.root/'static_resources/pc'/self.native()['assethash']).exists())

    def test_08_restore_does_not_auto_apply_and_native_cannot_be_removed(self):
        row = self.generate(); self.apply(row); manager.remove_profile(row['id'])
        status = manager.restore_profile(row['id'])
        restored = next(p for p in status['profiles'] if p['id'] == row['id'])
        self.assertTrue(restored['ready'])
        self.assertFalse(restored['removed'])
        self.assertNotEqual(status['platforms']['pc']['effective_id'], row['id'])
        with self.assertRaises(ValueError): manager.remove_profile(self.native()['id'])

    def test_09_same_target_different_base_voice_isolation(self):
        old, new = self.generate(build='229'), self.generate(build='321')
        self.assertNotEqual(old['id'], new['id'])
        for row, build in ((old, '229'), (new, '321')):
            for name in ('voice_package_list_308_331.bytes', 'voice_hash_ja_30800331.bytes', 'voice_hash_zh_30800331.bytes'):
                handled, path = manager.resolve_resource(f'/pc/resources/profiles/{row["id"]}/{name}', 'pc')
                self.assertTrue(handled)
                data = json.loads(Path(path).read_bytes())
                actual = data['infos'][0]['version'] if 'infos' in data else data['version']
                self.assertEqual(actual, int('30700' + build))

    def test_10_linux_skips_local_probe_and_windows_pc_only(self):
        with patch.object(versions, 'detect_client_resource_version') as detect:
            status = manager.configure_startup('auto')
            detect.assert_not_called()
            self.assertTrue(all(p['effective']['resource_version'] == '321' for p in status['platforms'].values()))
        client = self.root / 'client'; client.mkdir()
        (client/'AssetHash_Info.bytes').write_bytes((self.root/'static_resources/pc'/self.native()['assethash']).read_bytes())
        with patch.object(manager, '_windows_host', return_value=True), patch.object(versions,'_CURRENT_VERSION','321'):
            status = manager.configure_startup('auto', str(client))
            self.assertEqual(versions.get_current_version(), '321')
        self.assertEqual(status['platforms']['pc']['effective']['resource_version'], '229')
        self.assertEqual(status['platforms']['ios']['effective']['resource_version'], '321')
        self.assertEqual(status['platforms']['android']['effective']['resource_version'], '321')

    def test_11_cli_short_id_and_chinese_label(self):
        row = self.generate(label='中文兼容方案')
        status = manager.configure_startup('auto', profiles=('pc=中文兼容方案',))
        self.assertEqual(status['platforms']['pc']['effective_id'], row['id'])
        status = manager.configure_startup('auto', profiles=(f'pc={row["id"]}',))
        self.assertEqual(status['platforms']['pc']['effective_id'], row['id'])
        with self.assertRaises(ValueError): manager.configure_startup('auto', profiles=('ios='+row['id'],))

    def test_12_atomic_selection_failure_preserves_effective_state(self):
        row = self.generate(); before = manager.CONFIG_PATH.read_bytes()
        with patch.object(manager.integrity,'publish_json',side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.apply(row)
        self.assertEqual(manager.CONFIG_PATH.read_bytes(), before)
        self.assertEqual(manager.get_status()['platforms']['pc']['effective']['resource_version'], '321')

    def test_13_sdk_responses_and_real_flask_asset_bytes(self):
        import server_net
        app = server_net.build_https_app(None, host_ip='127.0.0.1')
        client = app.test_client()
        for platform in manager.PLATFORMS:
            row = self.generate(platform)
            manager.save_platform(dict(platform=platform, selected_id=row['id'], app_version='308',
                                       fallback_id=self.native(platform, '321')['id']))
            for method in ('get','post'):
                values = dict(platformType=platform, channelId=manager.CHANNELS[platform], appVersion='308')
                kwargs = {'query_string':values} if method == 'get' else {'json':values}
                result = getattr(client,method)('/mix-sdk-api/pass/game/updateversion/getLatest',**kwargs).get_json()
                self.assertEqual(result['errorCode'],'0')
                entry = next(x for x in result['data'] if x['type']=='noInstall')
                self.assertEqual((entry['matchedAppVersion'],entry['version']),('308','331'))
                prefix=f'/{platform}/resources/profiles/{row["id"]}/'
                self.assertIn(prefix,entry['downloadUrl'])
                response=client.get(prefix+entry['assethash'],headers={'Host':'download-eo.ys4fun.com'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(hashlib.md5(response.data).hexdigest(),row['md5'])
                response.close()
                response=client.get(prefix+'voice_hash_ja_30800331.bytes',headers={'Host':'download-eo.ys4fun.com'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(json.loads(response.data)['version'],30700229)
                response.close()
        mismatch=client.get('/mix-sdk-api/pass/game/updateversion/getLatest?platformType=pc&channelId=7').get_json()
        self.assertNotEqual(mismatch['errorCode'],'0')

    def test_mobile_install_auto_follows_reported_build_and_skips_unknown_build(self):
        import server_net

        client = server_net.build_https_app(None, host_ip='127.0.0.1').test_client()
        url = '/mix-sdk-api/pass/game/updateversion/getLatest'
        for platform in ('android', 'ios'):
            native = self.native(platform, '229')
            selection = dict(platform=platform, selected_id=native['id'], fallback_id=native['id'])
            manager.save_platform({**selection, 'app_version': 'auto'})
            params = dict(platformType=platform, channelId=manager.CHANNELS[platform])
            requests = (
                (client.get, dict(query_string={**params, 'appVersion': '307'}), '307'),
                (client.post, dict(data={**params, 'appVersion': '307'}), '307'),
                (client.post, dict(json={**params, 'appVersion': 308}), '308'),
                (client.get, dict(query_string=params), None),
                (client.post, dict(json={**params, 'appVersion': True}), None),
            )
            for send, kwargs, expected in requests:
                with self.subTest(platform=platform, request=kwargs):
                    result = send(url, **kwargs).get_json()
                    self.assertEqual(result['errorCode'], '0')
                    rows = {row['type']: row for row in result['data']}
                    self.assertEqual(rows['noInstall']['matchedAppVersion'], expected or '307')
                    self.assertEqual(rows['noInstall']['assethash'], native['assethash'])
                    self.assertEqual(rows['noInstall']['platformType'], platform)
                    if expected is None:
                        self.assertNotIn('install', rows)
                    else:
                        self.assertEqual(rows['install']['version'], expected)

            for build in ('307', '308'):
                manager.save_platform({**selection, 'app_version': build})
                rows = client.get(url, query_string=params).get_json()['data']
                install = next(row for row in rows if row['type'] == 'install')
                self.assertEqual((install['version'], install['platformType']), (build, platform))
                self.assertNotIn('/packagepc/', install['downloadUrl'])

    def test_ios_base_change_selects_one_matching_manifest_with_android_auto(self):
        import server_net

        android = self.native('android', '321')
        ios = self.native('ios', '229')
        compat = self.generate('ios')
        initial = manager.save_platform(dict(platform='android', selected_id='auto',
                                             fallback_id=android['id'], app_version='auto'))
        client = server_net.build_https_app(None, host_ip='127.0.0.1').test_client()
        url = '/mix-sdk-api/pass/game/updateversion/getLatest'
        for build, expected in (('307', ios), ('308', compat)):
            saved = manager.save_platform({'selections': {'ios': dict(selected_id=compat['id'],
                                          fallback_id=ios['id'], app_version=build)}})
            self.assertEqual(saved['platforms']['android'], initial['platforms']['android'])
            for platform in ('ios', 'android'):
                response = client.post(url, data=dict(channelId='7', platformType=platform)).get_json()
                self.assertEqual(response['errorCode'], '0')
                entries = response['data']
                if platform == 'ios':
                    self.assertEqual([entry['type'] for entry in entries], ['noInstall', 'install'])
                    self.assertEqual(entries[0]['assethash'], expected['assethash'])
                    self.assertEqual(entries[0]['matchedAppVersion'], build)
                    self.assertEqual(entries[1]['version'], build)
                else:
                    self.assertEqual([entry['type'] for entry in entries], ['noInstall'])
                    self.assertEqual(entries[0]['assethash'], android['assethash'])

    def test_windows_auto_uses_startup_build_when_sdk_omits_app_version(self):
        import server_net

        native = self.native('pc', '321')
        selection = dict(platform='pc', selected_id=native['id'], fallback_id=native['id'])
        manager.save_platform({**selection, 'app_version': 'auto'})
        client = server_net.build_https_app(None, host_ip='127.0.0.1').test_client()
        base = '/skzy-activity/gateway/get?action=base&version=308'
        url = '/mix-sdk-api/pass/game/updateversion/getLatest?platformType=pc&channelId=9'
        self.assertEqual(client.get(base, headers={'Host': 'prod-api-activity.ys4fun.com'}).get_json()['errorCode'], '0')

        def response(path=url, **kwargs):
            result = client.get(path, **kwargs).get_json()
            self.assertEqual(result['errorCode'], '0')
            return {row['type']: row for row in result['data']}

        rows = response()
        self.assertEqual(rows['noInstall']['matchedAppVersion'], '308')
        self.assertEqual(rows['noInstall']['assethash'], native['assethash'])
        self.assertEqual(rows['install']['version'], '308')
        self.assertIn('/packagepc/prod/308/', rows['install']['downloadUrl'])

        prefix = urlsplit(rows['noInstall']['downloadUrl'].split(';')[0]).path
        host = {'Host': 'download-eo.ys4fun.com'}
        sealed = self.root / 'static_resources' / 'pc' / 'native' / native['id']
        for name in (native['assethash'], *sorted(native['voice_files'], reverse=True)):
            requested = name.replace('307', '308', 1) if name.startswith('voice_') else name
            with client.get(prefix + requested, headers=host) as received:
                self.assertEqual(received.status_code, 200, requested)
                self.assertEqual(received.data, (sealed / name).read_bytes())
        self.assertEqual(manager.resolve_resource('/pc/resources/voice_package_list_308_321.bytes', 'pc'),
                         (False, None))
        with client.get(prefix + 'voice_package_list_308_229.bytes', headers=host) as received:
            self.assertEqual(received.status_code, 404)

        self.assertEqual(response(url + '&appVersion=307')['install']['version'], '307')
        client.get(base.replace('308', '307'), headers={'User-Agent': 'iPhone Darwin'})
        self.assertEqual(response()['install']['version'], '308')
        self.assertNotIn('install', response(environ_overrides={'REMOTE_ADDR': '192.0.2.8'}))

        manager.save_platform({**selection, 'app_version': '308'})
        manual = response(url + '&appVersion=307')
        self.assertEqual(manual['noInstall']['matchedAppVersion'], '308')
        self.assertIn('/packagepc/prod/308/', manual['install']['downloadUrl'])

    def test_14_public_api_never_accepts_derived_base(self):
        app=Flask(__name__);app.register_blueprint(gm_api.gm_bp,url_prefix='/api/gm');client=app.test_client()
        inputs=self.inputs('ios')
        preview=client.post('/api/gm/system/resource_manifests/preview',json=inputs).get_json()['data']
        result=client.post('/api/gm/system/resource_manifests/generate',json={**inputs,'preview_token':preview['preview_token']}).get_json()
        self.assertEqual(result['code'],0)
        bad=client.post('/api/gm/system/resource_manifests/preview',json={**inputs,'base_id':result['data']['profile']['id'],'origin':'native'}).get_json()
        self.assertNotEqual(bad['code'],0)
        self.assertEqual(client.get('/api/gm/system/resource_distribution').get_json()['data']['native_only_base'],True)

    def test_15_sealed_base_survives_flat_voice_update(self):
        row = self.generate()
        filename = 'voice_hash_ja_30700229.bytes'
        original = (self.root/'static_resources/pc'/filename).read_bytes()
        (self.root/'static_resources/pc'/filename).write_bytes(b'changed by later synchronization')
        status = manager.get_status()
        self.assertTrue(next(p for p in status['profiles'] if p['id'] == row['id'])['ready'])
        handled, path = manager.resolve_resource(f'/pc/resources/profiles/{row["id"]}/voice_hash_ja_30800331.bytes','pc')
        self.assertTrue(handled)
        self.assertEqual(Path(path).read_bytes(), original)

    def test_16_asset_routes_do_not_repeat_catalogue_validation(self):
        from concurrent.futures import ThreadPoolExecutor
        row = self.generate(); self.apply(row)
        path=f'/pc/resources/profiles/{row["id"]}/'+'a'*32+'.ys'
        with patch.object(manager,'_records',side_effect=AssertionError('full catalogue on asset request')):
            with ThreadPoolExecutor(max_workers=13) as pool:
                self.assertEqual(list(pool.map(lambda _:manager.resolve_resource(path,'pc'),range(13))),[(False,None)]*13)

    def test_17_official_registration_preserves_distinct_voice_identity(self):
        source=self.native('ios','321')
        info=dict(assethash=source['assethash'])
        manager.register_official_bundle('ios',info)
        original_path=self.root/'static_resources/ios/voice_hash_ja_30700321.bytes'
        body=json.loads(original_path.read_bytes());body['assetHashList']=[]
        original_path.write_bytes(manager._json_bytes(body))
        manager.register_official_bundle('ios',info)
        status=manager.get_status()
        candidates=[p for p in status['profiles'] if p['platform']=='ios' and p['resource_version']=='321']
        self.assertEqual(len(candidates),2)
        self.assertTrue(all(p['ready'] for p in candidates))
        self.assertNotEqual(status['platforms']['ios']['effective_id'],source['id'])

    def test_bilingual_manifest_download_becomes_selectable_in_distribution(self):
        import copy
        import cdn_download_queue as downloads
        import cdn_transfer_control as transfers

        platform, build = 'ios', '332'
        folder = manager._folder(platform)
        source = self.native(platform, '321')
        data = json.loads((folder/source['assethash']).read_bytes())
        data.update(buildCode=int(build), versionName='v5.3.6')
        raw = manager._json_bytes(data)
        filename = f'assethash_307_{build}_{hashlib.md5(raw).hexdigest()}.bytes'
        (folder/filename).write_bytes(raw)
        gate = transfers.PriorityGate(self.root/'transfer')
        self.stack.enter_context(patch.object(transfers, 'gate', gate))
        self.stack.enter_context(patch.object(versions, 'STATIC_RESOURCES_DIR', str(folder.parent)))
        self.stack.enter_context(patch.object(cdn_proxy, 'DEFAULT_STATIC_DIR', str(folder.parent)))
        configs = copy.deepcopy(versions.VERSION_CONFIGS)
        configs.pop(build, None)
        self.stack.enter_context(patch.object(versions, 'VERSION_CONFIGS', configs))
        supported = [version for version in versions.SUPPORTED_VERSIONS if version != build]
        self.stack.enter_context(patch.object(versions, 'SUPPORTED_VERSIONS', supported))
        self.stack.enter_context(patch.object(versions, '_save_persisted_dynamic_versions'))
        queue = downloads.DownloadQueue(self.root/'queue', gate=gate, autostart=False)
        self.addCleanup(queue.close)
        self.stack.enter_context(patch.object(downloads, 'get_manager', return_value=queue))
        app = Flask(__name__)
        app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        client = app.test_client()
        status_url = '/api/gm/system/resource_distribution'
        queue_url = '/api/gm/system/download_queue'
        row = dict(platform=platform, filename=filename)
        self.assertIn(row, client.get(queue_url+'/catalogue').get_json()['data']['files'])
        self.assertIn(row, client.get(status_url).get_json()['data']['saved_downloads'])

        def enqueue(languages, **options):
            result = client.post(queue_url, json={**row, 'kind': 'manifest', 'download': False,
                'voice_languages': languages, **options}).get_json()
            self.assertEqual(result['code'], 0, result)
            ident = result['data']['task_id']
            queue.run_once()
            self.assertEqual(queue.report(ident)['task']['state'], 'ready')
            return ident

        def start(ident):
            result = client.post(queue_url+'/action', json=dict(action='start', task_id=ident)).get_json()
            self.assertEqual(result['code'], 0, result)
            queue.run_once()
            task = queue.report(ident)['task']
            self.assertEqual(task['state'], 'completed', task)
            return task

        def fetch_voice(plat, name, directory, **kwargs):
            self.assertEqual(plat, platform)
            version = 30700332
            payload = (dict(infos=[dict(name=lang, version=version, size=100) for lang in ('zh', 'ja')])
                       if name.startswith('voice_package_list') else dict(version=version, assetHashList=[]))
            (Path(directory)/name).write_bytes(manager._json_bytes(payload))

        info = dict(assethash=filename, channel_id='7', app_version='307', version=build)
        with patch.object(versions, 'probe_official_latest', return_value=(True, info, '')) as probe, \
             patch.object(cdn_proxy, 'download_verified', side_effect=fetch_voice) as fetch:
            single = enqueue(['zh'])
            fetch.assert_not_called()
            start(single)
            self.assertIn(row, client.get(status_url).get_json()['data']['saved_downloads'])
            self.assertNotIn(build, versions.SUPPORTED_VERSIONS)
            task = start(enqueue(['zh', 'ja']))
            self.assertEqual((task['stats']['cached_files'], task['stats']['done_files']), (3, 1))
            self.assertEqual(task['message'], '分发方案已就绪，可手动应用')
            retry = start(enqueue(['zh', 'ja']))
            explicit = start(enqueue([], for_distribution=True))
            self.assertEqual(retry['stats']['cached_files'], 4)
            self.assertEqual(explicit['stats']['cached_files'], 4)
        self.assertEqual(probe.call_count, 4)
        self.assertEqual(fetch.call_count, 3)
        status = client.get(status_url).get_json()['data']
        self.assertNotIn(row, status['saved_downloads'])
        profiles = [p for p in status['profiles'] if p['platform'] == platform and p['assethash'] == filename]
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertTrue(profile['ready'])
        self.assertEqual(status['platforms'][platform]['effective']['resource_version'], '321')
        self.assertEqual(self.apply(profile)['platforms'][platform]['effective_id'], profile['id'])
        self.assertEqual(manager.resolve_connection_version(platform)['version'], build)
        self.assertEqual(versions.get_assethash(platform, build), filename)

    def test_18_legacy_alias_migrates_without_losing_other_channel_choices(self):
        base=self.native('ios','229')
        body=json.loads((self.root/'static_resources/ios'/base['assethash']).read_bytes())
        body.update(appVersion=308,buildCode=331)
        raw=manager._json_bytes(body);name='assethash_308_331_'+hashlib.md5(raw).hexdigest()+'.bytes'
        (self.root/'static_resources/ios'/name).write_bytes(raw)
        manager.CONFIG_PATH.write_text(json.dumps(dict(version=1,platforms=dict(ios=dict(source_version='229',alias_enabled=True,
            alias=dict(app_version='308',resource_version='331',source_version='229',assethash=name))))),encoding='utf-8')
        before=manager.get_status()['platforms']['ios']['effective_id']
        self.assertTrue(before.startswith('legacy-'))
        row=self.generate('pc');status=self.apply(row)
        self.assertEqual(status['platforms']['ios']['effective_id'],before)

    def test_20_malformed_namespace_and_unavailable_flat_profile_are_denied(self):
        row = self.generate()
        self.assertEqual(manager.resolve_resource('/pc/resources/profiles/bad/extra/' + row['assethash'], 'pc'), (True, None))
        base = self.native()
        (self.root / 'static_resources/pc/native' / base['id'] / base['assethash']).write_bytes(b'corrupt')
        self.assertEqual(manager.resolve_resource('/pc/resources/' + row['assethash'], 'pc'), (True, None))

    def test_19_failed_version_commit_rolls_back_native_authority(self):
        before=manager._state()
        def staged(_platform,_info):
            state=manager._state();state.setdefault('native_sources',{})['pending-test-only']={'pending':True}
            manager._save(state)
        def failed():raise OSError('version registry write failed')
        with patch.object(manager,'register_official_bundle',side_effect=staged), self.assertRaises(OSError):
            manager.commit_official_bundles({'ios':{}},failed)
        self.assertEqual(manager._state(),before)


    def test_21_legacy_android_channels_are_normalized_without_rewriting_storage(self):
        row = self.generate('android')
        self.apply(row)
        native = json.loads(manager.NATIVE_PATH.read_text('utf-8'))
        for source in native['sources']:
            if source['platform'] == 'android':
                source['channel_id'] = '8'
        manager.NATIVE_PATH.write_text(json.dumps(native), encoding='utf-8')
        state = manager._state()
        state['profiles'][row['id']]['channel_id'] = '8'
        manager._save(state)
        before = (manager.NATIVE_PATH.read_bytes(), manager.CONFIG_PATH.read_bytes())
        status = manager.get_status()
        self.assertEqual(manager.CHANNELS, versions.PLATFORM_CHANNEL_IDS)
        self.assertEqual(status['platforms']['android']['channel_id'], '7')
        self.assertTrue(all(p['channel_id'] == '7' for p in status['profiles'] if p['platform'] == 'android'))
        self.assertEqual((manager.NATIVE_PATH.read_bytes(), manager.CONFIG_PATH.read_bytes()), before)


if __name__ == '__main__':
    ROOT = SERVER.parent/'archive/manifest_manager_20261027'/('checks_'+uuid.uuid4().hex)
    ROOT.mkdir(parents=True)
    def audit(event,args):
        if event=='open':
            name,_,flags=args
            if isinstance(name,(str,bytes,os.PathLike)) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
                if not Path(name).resolve().is_relative_to(ROOT):raise PermissionError('Probe refuses non-fixture writes')
        if event in ('os.remove','os.rmdir','socket.connect','socket.bind'):raise PermissionError('Probe forbids deletion/network')
        if event in ('os.rename','shutil.copyfile') and any(not Path(p).resolve().is_relative_to(ROOT) for p in args[:2]):
            raise PermissionError('Probe refuses non-fixture mutation')
    sys.addaudithook(audit)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ManifestChecks))
    (ROOT/'result.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors)),indent=2),encoding='utf-8')
    print('Retained fixtures:',ROOT)
    sys.exit(not result.wasSuccessful())
