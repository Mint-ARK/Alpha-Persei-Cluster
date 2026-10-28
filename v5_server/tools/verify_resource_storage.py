"""Isolated shared-storage probes; retain fixtures, forbid network/deletion."""
from contextlib import ExitStack
import hashlib
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
import resource_storage as module
import cdn_proxy as cdn
import cdn_transfer_control as transfers
import cdn_integrity as integrity
from flask import Flask
import gm_api

ROOT = SERVER.parent/'archive/resource_storage_20261028'/('checks_'+uuid.uuid4().hex)
ROOT.mkdir(parents=True)

def inside(path):
    return Path(str(Path(path).resolve()).removeprefix('\\\\?\\')).is_relative_to(ROOT)

def audit(event, args):
    if event == 'open':
        path, _, flags = args
        if isinstance(path, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC) and not inside(path):
            raise PermissionError('non-fixture write')
    if event in ('os.remove', 'os.rmdir', 'socket.connect', 'socket.bind'):
        raise PermissionError('network/deletion forbidden')
    if event in ('os.rename', 'shutil.copyfile') and any(not inside(p) for p in args[:2]):
        raise PermissionError('non-fixture mutation')
sys.addaudithook(audit)

class StorageChecks(unittest.TestCase):
    def setUp(self):
        self.root = ROOT/self._testMethodName
        self.root.mkdir()
        self.config = self.root/'server_config.json'
        self.config.write_text(json.dumps(dict(storage=dict(cdn_cache_dir='旧缓存 空格'),
            network=dict(capture_cdn=False, private_value=7), client=dict(game_install_dir='游戏目录'), custom={'keep':True})))
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {key:'' for key in ('CDN_CACHE_DIR','CDN_PC_DIR','CDN_IOS_DIR','CDN_ANDROID_DIR')}))
        self.storage = module.Storage(self.config)
        self.gate = transfers.PriorityGate(self.root/'runtime')
        self.stack.enter_context(patch.object(module, 'storage', self.storage))
        self.stack.enter_context(patch.object(cdn, 'storage', self.storage))
        self.stack.enter_context(patch.object(cdn, 'PLATFORM_DIRS', self.storage.directories()))
        self.stack.enter_context(patch.object(cdn, 'DEFAULT_STATIC_DIR', str(self.root/'static')))
        self.stack.enter_context(patch.object(cdn, '_SERVER_DIR', str(self.root)))
        self.stack.enter_context(patch.object(transfers, 'gate', self.gate))

    def test_01_shared_merge_unicode_history_and_backup(self):
        before = self.config.read_bytes()
        result = self.storage.apply(str(self.root/'新目录 中文 空格'))
        data = json.loads(self.config.read_bytes())
        self.assertEqual(data['network'], dict(capture_cdn=False, private_value=7))
        self.assertEqual(data['custom'], dict(keep=True))
        self.assertEqual(result['read_roots'], [str(self.root/'旧缓存 空格')])
        self.assertTrue(all(Path(p).is_dir() for p in result['directories'].values()))
        self.assertTrue(any(p.read_bytes()==before for p in self.root.glob('server_config.json.before_publish_*')))
        reloaded = module.Storage(self.config)
        self.assertEqual(reloaded.directories(), self.storage.directories())

    def test_02_repeat_switch_keeps_deduplicated_platform_roots(self):
        old = self.storage.root
        a = str(self.root/'A'); b = str(self.root/'B')
        self.storage.apply(a); self.storage.apply(b); self.storage.apply(a)
        self.assertEqual(self.storage.read_roots, [old,b])
        self.assertEqual(self.storage.history('ios'), [str(Path(old)/'ios'),str(Path(b)/'ios')])

    def test_03_invalid_config_is_not_overwritten(self):
        self.config.write_bytes(b'broken')
        with self.assertRaises(ValueError): self.storage.apply(str(self.root/'target'))
        self.assertEqual(self.config.read_bytes(), b'broken')
        self.assertFalse((self.root/'target').exists())

    def test_04_unwritable_destination_preserves_authority(self):
        blocked = self.root/'file'; blocked.write_bytes(b'not a directory')
        old = self.storage.root; before = self.config.read_bytes()
        with self.assertRaises(OSError): self.storage.apply(str(blocked))
        self.assertEqual(self.storage.root, old); self.assertEqual(self.config.read_bytes(), before)

    def test_05_environment_override_is_visible_and_not_silently_replaced(self):
        with patch.dict(os.environ, {'CDN_IOS_DIR': str(self.root/'override')}):
            self.assertTrue(self.storage.status()['overridden'])
            self.assertEqual(self.storage.directories()['ios'], str(self.root/'override'))
            with self.assertRaises(ValueError): self.storage.apply(str(self.root/'target'))

    def test_06_active_or_passive_transfer_blocks_switch(self):
        with self.gate.active():
            with self.assertRaises(ValueError):
                with self.gate.maintenance(): pass
        with self.gate.passive():
            with self.assertRaises(ValueError):
                with self.gate.maintenance(): pass
        with self.gate.maintenance():
            result = self.storage.apply(str(self.root/'target'))
        self.assertEqual(result['root'], str(self.root/'target'))

    def test_07_old_cache_remains_readable_and_platform_isolated(self):
        old = Path(self.storage.root); (old/'pc').mkdir(parents=True)
        body = b'valid resource'; name = hashlib.md5(body).hexdigest()+'.ys'
        source = old/'pc'/name; source.write_bytes(body)
        self.storage.apply(str(self.root/'new')); cdn.PLATFORM_DIRS = self.storage.directories()
        self.assertEqual(cdn.find_cached_file(name,'pc'), str(source))
        self.assertIsNone(cdn.find_cached_file(name,'ios'))
        self.assertEqual(cdn.get_data_dir('pc'), str(self.root/'new/pc'))

    def test_08_browser_is_server_directory_only(self):
        (self.root/'文件夹').mkdir(); (self.root/'a.txt').write_bytes(b'a'); (self.root/'.hidden').mkdir()
        result = module.browse(str(self.root))
        self.assertEqual([p['name'] for p in result['directories']], ['文件夹'])
        self.assertEqual(result['path'], str(self.root))
        with self.assertRaises(ValueError): module.browse(str(self.root/'missing'))

    def test_09_browser_bounds_and_new_path_validation(self):
        for n in range(203): (self.root/str(n)).mkdir()
        result = module.browse(str(self.root))
        self.assertTrue(result['truncated']); self.assertEqual(len(result['directories']),200)
        for value in ('', 'a\x00b', True, 123):
            with self.assertRaises(ValueError): self.storage.apply(value)

    def test_10_api_save_and_busy_rejection(self):
        app = Flask(__name__); app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        client = app.test_client()
        self.assertEqual(client.get('/api/gm/system/resource_storage').get_json()['code'],0)
        with self.gate.active():
            self.assertNotEqual(client.post('/api/gm/system/resource_storage', json={'root':str(self.root/'new')}).get_json()['code'],0)
        result = client.post('/api/gm/system/resource_storage', json={'root':str(self.root/'new')}).get_json()
        self.assertEqual(result['code'],0); self.assertEqual(cdn.PLATFORM_DIRS,self.storage.directories())
        self.assertEqual(client.get('/api/gm/system/resource_storage/browse',query_string={'path':str(self.root)}).get_json()['code'],0)

    def test_11_failed_primary_directory_does_not_silently_write_elsewhere(self):
        blocked = self.root/'file'; blocked.write_bytes(b'x')
        cdn.PLATFORM_DIRS['pc'] = str(blocked/'pc')
        with self.assertRaises(OSError): cdn.get_data_dir('pc')
        self.assertFalse((self.root/'static/pc').exists())

    def test_12_empty_windows_default_never_selects_c(self):
        if os.name != 'nt': self.skipTest('Windows drive preference')
        with patch.object(module, 'volumes', return_value=['C:\\','E:\\','D:\\']), \
             patch.object(module.os, 'access', return_value=True), \
             patch.object(module.shutil, 'disk_usage', return_value=type('Usage',(),{'free':100})()):
            self.assertTrue(module.recommend().startswith('D:'))
        with patch.object(module, 'volumes', return_value=['C:\\']):
            self.assertEqual(module.recommend(),'')

    def test_13_windows_factory_default_is_recommended_but_old_cache_survives(self):
        if os.name != 'nt': self.skipTest('Windows factory default')
        self.config.write_text(json.dumps({'storage':{'cdn_cache_dir':'./cdn_cache'}}))
        with patch.object(module, 'recommend', return_value=str(self.root/'recommended')):
            storage = module.Storage(self.config)
            self.assertFalse(storage.status()['root_selected'])
            self.assertEqual(storage.root, str(self.root/'recommended'))
            self.assertIn(str(self.root/'cdn_cache'),storage.read_roots)
            storage.apply(str(self.root/'selected'))
            self.assertTrue(storage.status()['root_selected'])
            self.assertIn(str(self.root/'cdn_cache'),storage.read_roots)
            # Explicitly selecting even the factory spelling remains authoritative.
            storage.apply('./cdn_cache')
            self.assertEqual(module.Storage(self.config).root,str(self.root/'cdn_cache'))

    def test_14_sessions_refresh_external_changes_before_index_or_receive(self):
        self.gate.on_acquired = lambda: self.storage.refresh()
        with self.gate.active():
            self.assertEqual(self.storage.root,str(self.root/'旧缓存 空格'))
        external = module.Storage(self.config)
        external.apply(str(self.root/'external'))
        with self.gate.active():
            self.assertEqual(self.storage.root,str(self.root/'external'))
        external.apply(str(self.root/'next'))
        with self.gate.passive():
            self.assertEqual(self.storage.root,str(self.root/'next'))

if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StorageChecks))
    (ROOT/'result.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors))))
    print('Retained fixtures:', ROOT)
    sys.exit(not result.wasSuccessful())
