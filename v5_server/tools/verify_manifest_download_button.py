"""Offline main-manifest save/API checks; all writes stay in retained fixtures."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.dont_write_bytecode = True
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import cdn_integrity as integrity
import cdn_proxy
import res_version_manager as versions
import gm_api
from flask import Flask

ROOT = SERVER.parent/'archive/manifest_download_button_20261028'/('checks_'+uuid.uuid4().hex)
ROOT.mkdir(parents=True)
def audit(event, args):
    if event == 'open':
        path, _, flags = args
        if isinstance(path, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
            if not Path(path).resolve().is_relative_to(ROOT): raise PermissionError('non-fixture write')
    if event in ('os.remove','os.rmdir','socket.connect','socket.bind'): raise PermissionError('deletion/network forbidden')
    if event in ('os.rename','shutil.copyfile') and any(not Path(p).resolve().is_relative_to(ROOT) for p in args[:2]):
        raise PermissionError('non-fixture mutation')
sys.addaudithook(audit)

class Response:
    status = 200
    def __init__(self, raw): self.body = io.BytesIO(raw); self.size = str(len(raw))
    def getheader(self, name): return self.size if name.lower() == 'content-length' else None
    def read(self, size=-1): return self.body.read(size)
class Connection:
    def close(self): pass

class Checks(unittest.TestCase):
    def setUp(self):
        self.root = ROOT/self._testMethodName
        self.root.mkdir()
        self.raw = json.dumps(dict(appVersion=308, buildCode=321, versionName='v5.3.1',
            assetHashList=['asset.ys|'+'a'*32+'|7'])).encode()
        self.name = 'assethash_308_321_'+hashlib.md5(self.raw).hexdigest()+'.bytes'
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        import cdn_transfer_control as transfers
        self.stack.enter_context(patch.object(transfers,'gate',transfers.PriorityGate(self.root/'transfers')))
        self.stack.enter_context(patch.object(versions,'STATIC_RESOURCES_DIR',str(self.root)))
        self.cache = self.stack.enter_context(patch.object(cdn_proxy,'find_cached_file',return_value=None))
        self.probe = self.stack.enter_context(patch.object(versions,'probe_official_latest',side_effect=self.info))
        self.opened = []
        def opener(host,path,*_args,**_kwargs):
            self.opened.append((host,path))
            return lambda *_: (Connection(),Response(self.raw))
        self.stack.enter_context(patch.object(cdn_proxy,'_upstream_opener',side_effect=opener))
        self.stack.enter_context(patch.object(versions,'register_version',side_effect=AssertionError('version mutation')))
        self.stack.enter_context(patch('resource_distribution.register_official_bundle',side_effect=AssertionError('selection mutation')))
    def info(self,platform):
        return True,dict(channel_id=versions.PLATFORM_CHANNEL_IDS[platform],assethash=self.name,app_version='308',version='321'),''
    def put(self, platform='ios', raw=None):
        path=self.root/platform/self.name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(self.raw if raw is None else raw); return path
    def test_01_same_name_skips_without_probe_or_download(self):
        path=self.put(); before=path.stat().st_mtime_ns
        result=versions.download_official_manifest('ios',self.name,'7')
        self.assertTrue(result['skipped']); self.assertTrue(result['valid'])
        self.probe.assert_not_called(); self.cache.assert_not_called()
        self.assertEqual(self.opened,[]); self.assertEqual(path.stat().st_mtime_ns,before)
    def test_02_invalid_existing_is_preserved(self):
        path=self.put(raw=b'bad')
        result=versions.download_official_manifest('ios',self.name,'7')
        self.assertTrue(result['skipped']); self.assertFalse(result['valid'])
        self.assertEqual(path.read_bytes(),b'bad'); self.probe.assert_not_called()
    def test_03_each_channel_saves_only_main_manifest_through_core(self):
        for platform,channel in versions.PLATFORM_CHANNEL_IDS.items():
            result=versions.download_official_manifest(platform,self.name,channel)
            self.assertTrue(result['downloaded']); self.assertTrue(result['valid'])
            path=self.root/platform/self.name
            self.assertEqual(path.read_bytes(),self.raw)
            self.assertEqual(list(path.parent.glob('*.bytes')),[path])
            self.assertIn(('download-eo.ys4fun.com',f'/{platform}/resources/{self.name}'),self.opened)
        self.assertEqual(len(self.opened),3)
    def test_04_wrong_channel_path_and_stale_metadata_rejected(self):
        for name in ('../'+self.name,'assethash_308_321_abc.bytes'):
            with self.assertRaises(ValueError): versions.download_official_manifest('pc',name,'9')
        with self.assertRaises(ValueError): versions.download_official_manifest('pc',self.name,'7')
        self.probe.return_value=None
        with patch.object(versions,'probe_official_latest',return_value=(True,dict(assethash='changed',channel_id='9'),'')):
            with self.assertRaises(ValueError): versions.download_official_manifest('pc',self.name,'9')
        self.assertEqual(self.opened,[])
    def test_05_verified_cache_copy_avoids_network(self):
        cache=self.root/'cache'/self.name; cache.parent.mkdir(); cache.write_bytes(self.raw)
        self.cache.return_value=str(cache)
        result=versions.download_official_manifest('android',self.name,'8')
        self.assertTrue(result['valid']); self.assertEqual(self.opened,[])
        self.assertEqual((self.root/'android'/self.name).read_bytes(),self.raw)
    def test_06_check_update_never_downloads_missing_manifest(self):
        app=Flask(__name__); app.register_blueprint(gm_api.gm_bp,url_prefix='/api/gm')
        with patch('resource_distribution.get_platform_config',return_value={'version':'321'}), patch.object(cdn_proxy,'download_verified') as download:
            result=app.test_client().get('/api/gm/system/check_update?platform=ios').get_json()
        download.assert_not_called()
        self.assertEqual(result['code'],0)
        self.assertFalse(result['data']['remote_manifest_present'])
        self.assertFalse(result['data']['diff_ready'])
        self.assertEqual(result['data']['remote_assethash'],self.name)
    def test_07_post_api_reports_saved_then_skipped(self):
        import cdn_download_queue as queues
        import cdn_transfer_control as transfers
        queue=queues.DownloadQueue(self.root/'queue',gate=transfers.gate,autostart=False)
        self.addCleanup(queue.close)
        self.stack.enter_context(patch.object(queues,'get_manager',return_value=queue))
        app=Flask(__name__); app.register_blueprint(gm_api.gm_bp,url_prefix='/api/gm')
        client=app.test_client(); body=dict(platform='android',channel_id='8',assethash=self.name)
        first=client.post('/api/gm/system/download_manifest',json=body).get_json()
        queue.run_once()
        second=client.post('/api/gm/system/download_manifest',json=body).get_json()
        queue.run_once()
        self.assertEqual(first['code'],0); self.assertIn('task_id',first['data'])
        self.assertEqual(second['code'],0)
        self.assertEqual(queue.status()['tasks'][1]['stats']['cached_files'],1)
        self.assertEqual(len(self.opened),1)
        self.assertNotEqual(client.post('/api/gm/system/download_manifest',json=[]).get_json()['code'],0)
    def test_08_bad_payload_is_never_published(self):
        self.raw=b'corrupt bytes'
        with self.assertRaises(ValueError): versions.download_official_manifest('pc',self.name,'9')
        self.assertFalse((self.root/'pc'/self.name).exists())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    (ROOT/'result.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors))),encoding='utf-8')
    print('Retained fixtures:',ROOT)
    sys.exit(not result.wasSuccessful())
