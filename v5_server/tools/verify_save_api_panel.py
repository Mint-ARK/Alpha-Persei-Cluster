"""Bounded per-save API/Flask/generator probes; no live accounts or external API."""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from account_db import AccountDB
import ai_api_profiles as profiles
import ai_bot_config
import gm_save_scope

UID = 261026130001
CID = 90001001


def fixture(nick='测试存档'):
    db = AccountDB.new_save_workspace()
    db.upsert('users', UID, dict(nick=nick, level=70, extra='{}'))
    db.set_system_config('save.owner_uid', str(UID))
    db.execute("INSERT INTO ai_characters(char_id,char_name,hero_ids,system_prompt,is_active) VALUES(?,?,?,'测试人格',1)",
               (CID, '海拉', '1094,1194'))
    return db


def config(name='第一出口', **kwargs):
    return dict(name=name, provider='deepseek', model='fixture-flash', type='openai',
                base_url='https://fixture.invalid/v1', api_key='fixture-secret-alpha', **kwargs)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.db = fixture()
        self.other = fixture()
        self.addCleanup(self.db.close)
        self.addCleanup(self.other.close)

    def add(self, **kwargs):
        return profiles.update(self.db, config(**kwargs))['profiles'][-1]['id']

    def test_two_saves_same_uid_isolated_and_reads_masked(self):
        self.add()
        self.assertIsNone(profiles.load(self.other))
        public = json.dumps(profiles.view(self.db))
        self.assertNotIn('fixture-secret-alpha', public)
        self.assertNotIn('fixture-secret-alpha', json.dumps(profiles.view(self.other)))
        self.assertTrue(profiles.view(self.db)['profiles'][0]['has_api_key'])

    def test_default_and_role_selection_and_hero_alias(self):
        first = self.add()
        data = config('第二出口'); data.update(api_key='fixture-secret-beta', model='fixture-pro')
        second = profiles.update(self.db, data)['profiles'][-1]['id']
        profiles.update(self.db, dict(action='route', char_id=CID, id=second))
        self.assertEqual(ai_bot_config.resolve_character_config(self.db, UID, 1194)['api_profile_id'], second)
        self.assertEqual(profiles.resolve(self.db)['api_profile_id'], first)
        profiles.update(self.db, dict(action='activate', id=second))
        self.assertEqual(profiles.resolve(self.db)['providers']['deepseek']['api_key'], 'fixture-secret-beta')
        profiles.update(self.db, dict(action='route', char_id=CID))
        self.assertFalse(profiles.load(self.db)['routes'])

    def test_blank_mask_keep_key_clear_does_not_fall_back_to_environment(self):
        pid = self.add()
        for key in ('', '***'):
            profiles.update(self.db, dict(id=pid, api_key=key))
            self.assertEqual(profiles.resolve(self.db)['providers']['deepseek']['api_key'], 'fixture-secret-alpha')
        profiles.update(self.db, dict(id=pid, clear_key=True))
        with patch.dict(os.environ, DEEPSEEK_API_KEY='must-not-inherit'):
            self.assertEqual(ai_bot_config.resolve_character_config(self.db, UID, CID)['providers']['deepseek']['api_key'], '')

    def test_provider_change_does_not_reuse_previous_key(self):
        pid = self.add()
        profiles.update(self.db, dict(id=pid, provider='gemini'))
        cfg = profiles.resolve(self.db)
        self.assertEqual(cfg['providers']['gemini']['api_key'], '')
        self.assertEqual(cfg['providers']['deepseek']['api_key'], '')

    def test_remove_references_is_blocked_then_removable(self):
        first = self.add()
        second = profiles.update(self.db, config('第二出口'))['profiles'][-1]['id']
        with self.assertRaises(ValueError): profiles.update(self.db, dict(action='remove', id=first))
        profiles.update(self.db, dict(action='route', char_id=CID, id=second))
        with self.assertRaises(ValueError): profiles.update(self.db, dict(action='remove', id=second))
        profiles.update(self.db, dict(action='route', char_id=CID))
        profiles.update(self.db, dict(action='remove', id=second))
        self.assertEqual(len(profiles.load(self.db)['profiles']), 1)

    def test_validation_atomic_unknown_id_nan_and_routes(self):
        self.add()
        before = copy.deepcopy(profiles.load(self.db))
        for data in (dict(action='activate', id='missing'), dict(temperature=float('nan')),
                     dict(base_url='file:///not-allowed'), dict(action='route', char_id=123)):
            with self.assertRaises((ValueError, TypeError)): profiles.update(self.db, data)
            self.assertEqual(profiles.load(self.db), before)

    def test_unconfigured_save_has_no_global_fallback_or_mutation(self):
        base = ai_bot_config.get_default_config()
        base['providers'][base['provider']]['api_key'] = 'fixture-global-must-not-inherit'
        original = copy.deepcopy(base)
        resolved = ai_bot_config.resolve_character_config(self.db, UID, CID, cfg=base)
        self.assertEqual(resolved['api_profile_id'], '')
        self.assertFalse(any(p.get('api_key') for p in resolved['providers'].values()))
        self.add()
        ai_bot_config.resolve_character_config(self.db, UID, CID, cfg=base)
        self.assertEqual(base, original)

    def test_real_chat_consumes_selected_db_profile(self):
        import ai_bot_service
        from types import SimpleNamespace
        self.add()
        self.db.set_character_persona_prompt(UID, CID, '你是海拉，简短自然地聊天。')
        with patch.object(ai_bot_service, 'call_llm', return_value='管理员，早上好。') as outbound, \
             patch.object(ai_bot_service, 'get_history_manager', return_value=SimpleNamespace(
                 get_history=lambda *a, **k: dict(messages=[], source='fixture'))):
            result = ai_bot_service.chat_sync(UID, CID, '你好。', self.db)
        self.assertTrue(result['success'])
        cfg = outbound.call_args.args[2]
        self.assertEqual(cfg['providers']['deepseek']['api_key'], 'fixture-secret-alpha')
        self.assertEqual(cfg['providers']['deepseek']['model'], 'fixture-flash')


class RequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flask import Flask
        import gm_api
        from save_slot_manager import SaveSlotManager
        import uuid
        cls.root = SERVER.parent / ('archive/panel_save_api_20261026/fixtures_' + uuid.uuid4().hex[:8])
        cls.root.mkdir(parents=True, exist_ok=True)
        for name in ('中文A', 'EnglishB'):
            path = cls.root / (name + '.db')
            if path.exists():
                raise RuntimeError('Fixture already exists; preserve it and use a new fixture directory')
            db = fixture(name)
            db.export_new_save(path)
            db.close()
        cls.manager = SaveSlotManager(str(cls.root))
        cls.manager.set_active_db(str(cls.root / 'EnglishB.db'))
        cls.app = Flask(__name__)
        cls.app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        cls.app.testing = True

    def setUp(self):
        self.patcher = patch('save_slot_manager.get_slot_manager', return_value=self.manager)
        self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.client = self.app.test_client()

    def headers(self, name='中文A.db'):
        return {'X-Save-Name': quote(name)}

    def test_get_is_readonly_no_registry_or_schema_mutation(self):
        target = self.root / '中文A.db'
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        r = self.client.get('/api/gm/account/profile', headers=self.headers()).get_json()
        self.assertEqual(r['code'], 0)
        self.assertEqual(r['data']['nickname'], '中文A')
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        self.assertFalse(self.manager.registry_file.exists())

    def test_post_bound_save_and_prewrite_snapshot(self):
        original = AccountDB.backup_save_file
        calls = []
        def capture(path):
            backup = original(path); calls.append((path, backup)); return backup
        with patch.object(AccountDB, 'backup_save_file', side_effect=capture):
            r = self.client.post('/api/gm/account/modify_basic', headers=self.headers(),
                                 json=dict(uid=UID, nickname='仅修改A')).get_json()
        self.assertEqual(r['code'], 0)
        self.assertEqual(len(calls), 1)
        old = AccountDB.open_existing(calls[0][1], readonly=True)
        other = AccountDB.open_existing(self.root / 'EnglishB.db', readonly=True)
        try:
            self.assertEqual(old.get('users', UID)['nick'], '中文A')
            self.assertEqual(other.get('users', UID)['nick'], 'EnglishB')
        finally: old.close(); other.close()
        self.assertEqual(self.manager.get_active_db(), str(self.root / 'EnglishB.db'))

    def test_parallel_requests_keep_save_selection_local(self):
        from concurrent.futures import ThreadPoolExecutor
        def read(name):
            with self.app.test_client() as client:
                return client.get('/api/gm/aichat/apis', headers=self.headers(name)).get_json()['data']['profiles']
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(read, ['中文A.db', 'EnglishB.db'] * 3))
        self.assertTrue(all(bool(row) == (index % 2 == 0) for index, row in enumerate(rows)))

    def test_traversal_missing_save_wrong_uid_rejected(self):
        for name in ('../account.db', 'missing.db'):
            r = self.client.get('/api/gm/aichat/apis', headers=self.headers(name)).get_json()
            self.assertNotEqual(r['code'], 0)
        r = self.client.post('/api/gm/account/modify_basic', headers=self.headers(), json=dict(uid=UID+1, nick='x')).get_json()
        self.assertNotEqual(r['code'], 0)

    def test_api_roundtrip_db_scope_and_diagnostic_selection(self):
        response = self.client.post('/api/gm/aichat/apis', headers=self.headers(), json=config()).get_json()
        self.assertEqual(response['code'], 0)
        self.assertNotIn('fixture-secret-alpha', json.dumps(response))
        other = self.client.get('/api/gm/aichat/apis', headers=self.headers('EnglishB.db')).get_json()
        self.assertEqual(other['data']['profiles'], [])
        pid = response['data']['active_id']
        import ai_bot_service
        with patch.object(ai_bot_service, 'test_llm_connection', return_value={'success': True, 'provider': 'deepseek'}) as probe:
            r = self.client.post('/api/gm/aichat/test_connection', headers=self.headers(), json=dict(profile_id=pid)).get_json()
        self.assertEqual(r['code'], 0)
        self.assertEqual(probe.call_args.kwargs['cfg']['providers']['deepseek']['api_key'], 'fixture-secret-alpha')

    def test_preview_validates_does_not_create_or_copy_credentials(self):
        data = dict(name='预览', options=dict(level=70, skip_intro=True, clear_through=5, support_mail=True), preview=True)
        before = set(self.root.glob('*'))
        r = self.client.post('/api/gm/saves/create', json=data).get_json()
        self.assertEqual(r['code'], 0)
        self.assertGreater(r['data']['cleared_stages_count'], 0)
        self.assertEqual(set(self.root.glob('*')), before)
        data['options']['level'] = 101
        self.assertNotEqual(self.client.post('/api/gm/saves/create', json=data).get_json()['code'], 0)

    def test_z_generate_unicode_save_registers_and_has_no_api_credentials(self):
        import save_generator
        with patch.object(save_generator, 'SLOTS', self.root):
            r = self.client.post('/api/gm/saves/create', json=dict(name='完整生成_中文',
                preset='quick_start')).get_json()
        self.assertEqual(r['code'], 0, r)
        generated = AccountDB.open_existing(self.root / r['data']['filename'], readonly=True)
        try:
            self.assertEqual(len(generated.query('SELECT uid FROM users')), 1)
            self.assertEqual(generated.get('users', r['data']['uid'])['level'], 70)
            self.assertEqual(profiles.load(generated)['profiles'], {})
            self.assertNotIn('fixture-secret', json.dumps(generated.query('SELECT * FROM server_global_config')))
            with patch.dict(os.environ, OPENCODE_GO_API_KEY='must-not-inherit'):
                cfg = ai_bot_config.resolve_character_config(generated, r['data']['uid'], CID)
                self.assertFalse(cfg['providers'][cfg['provider']]['api_key'])
        finally: generated.close()
        self.assertIsNotNone(r['data']['slot_id'])
        self.assertEqual(self.manager.get_active_db(), str(self.root / 'EnglishB.db'))
        first_api = self.client.post('/api/gm/aichat/config', headers=self.headers(r['data']['filename']),
                                     json=config()).get_json()
        self.assertEqual(first_api['code'], 0, first_api)
        self.assertTrue(first_api['data']['has_api_key'])
        self.assertNotIn('fixture-secret-alpha', json.dumps(first_api))


class SaveDeleteTests(unittest.TestCase):
    def setUp(self):
        import account_db
        from flask import Flask
        import gm_api
        import save_slot_manager

        self.root = SERVER.parent/'archive/panel_save_delete_20261113'/uuid4().hex
        self.manager = save_slot_manager.SaveSlotManager(self.root/'server')
        self.target = self.manager.server_dir/'待删除 存档.db'
        self.mounted = self.manager.server_dir/'当前存档.db'
        self.primary = self.manager.server_dir/'account.db'
        self.registry = {3: self.primary.name, 6: self.target.name, 8: self.mounted.name}
        for path in (self.target, self.mounted, self.primary):
            db = fixture(path.stem)
            db.export_new_save(path)
            db.close()
        self.manager.set_active_db(self.mounted)
        self.manager._save_registry(self.registry, strict=True)
        app = Flask(__name__)
        app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        self.client = app.test_client()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(save_slot_manager, 'get_slot_manager', return_value=self.manager))
        stack.enter_context(patch.object(account_db, '__file__', str(self.manager.server_dir/'account_db.py')))

    def delete(self, filename=None, uid=UID):
        return self.client.post('/api/gm/saves/delete', json=dict(filename=filename or self.target.name, uid=uid)).get_json()

    def test_delete_backs_up_and_releases_only_target_slot(self):
        import save_slot_manager

        def recycle(paths):
            paths[0].rename(self.root/'recycled')

        with patch.object(save_slot_manager, '_recycle_save_files', side_effect=recycle):
            response = self.delete()
        self.assertEqual(response['code'], 0, response)
        self.assertFalse(self.target.exists())
        backups = list((self.root/'db_backup').glob('*.db'))
        self.assertEqual(len(backups), 1)
        db = AccountDB.open_existing(backups[0], readonly=True)
        try:
            self.assertEqual(db.get('users', UID)['nick'], self.target.stem)
        finally:
            db.close()
        self.assertEqual(self.manager._load_registry(), {3: self.primary.name, 8: self.mounted.name})
        rows = self.client.get('/api/gm/saves').get_json()['data']['saves']
        self.assertEqual({row['filename'] for row in rows}, {self.mounted.name, self.primary.name})
        self.assertEqual(self.manager.get_active_db(), str(self.mounted))
        self.assertNotEqual(self.delete()['code'], 0)

    def test_protected_and_stale_targets_rejected(self):
        with patch.object(AccountDB, 'backup_save_file') as backup:
            self.assertNotEqual(self.delete(self.primary.name)['code'], 0)
            self.assertNotEqual(self.delete(self.mounted.name)['code'], 0)
            self.assertNotEqual(self.delete('../' + self.target.name)['code'], 0)
            self.assertNotEqual(self.delete(uid=UID + 1)['code'], 0)
            backup.assert_not_called()
        self.assertEqual(self.manager._load_registry(), self.registry)

    def test_online_and_panel_requests_block_recycle(self):
        from flask import Flask
        import server_net

        conn = SimpleNamespace(db=SimpleNamespace(path=str(self.target)))
        with patch.dict(server_net._online_game_conns, {UID: [conn]}, clear=True):
            self.assertIn('退出', self.delete()['msg'])

        app = Flask(__name__)
        with app.test_request_context(headers={'X-Save-Name': quote(self.target.name)}):
            gm_save_scope.bind()
            try:
                with patch.object(AccountDB, 'backup_save_file') as backup:
                    self.assertIn('正在读取', self.delete()['msg'])
                    backup.assert_not_called()
            finally:
                gm_save_scope.close()
        self.assertEqual(self.manager._panel_requests, {})

    def test_unregistered_save_recycles_with_sidecars(self):
        import save_slot_manager

        self.manager._save_registry({3: self.primary.name, 8: self.mounted.name}, strict=True)
        sidecar = self.target.with_name(self.target.name + '-journal')
        snapshot = AccountDB.backup_save_file
        def backup_with_sidecar(path):
            result = snapshot(path)
            sidecar.write_bytes(b'journal-fixture')
            return result
        archived = []
        def recycle(paths):
            archived.extend(path.name for path in paths[0].iterdir())
            paths[0].rename(self.root/'recycled')
        with patch.object(save_slot_manager, '_recycle_save_files', side_effect=recycle), \
             patch.object(AccountDB, 'backup_save_file', side_effect=backup_with_sidecar):
            result = self.delete()
            self.assertEqual(result['code'], 0, result)
        if os.name != 'nt':
            archived = [path.name for path in self.manager.trash_dir.glob('*/*')]
        self.assertCountEqual(archived, [self.target.name, sidecar.name])
        self.assertFalse(sidecar.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
