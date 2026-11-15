"""GM writes against isolated saves through the real Flask blueprint."""
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v5_server'))
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
from flask import Flask
from account_db import AccountDB
import account_db
from birthday_service import commemorative_items
import gm_api
import gm_reader
import gm_save_scope
import inventory_service as inventory
from save_generator import generation_config
import server_net
from save_slot_manager import SaveSlotManager

UID = 42
HERO = 1075


class GmApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.folder = Path(folder)
        self.paths = [self.folder / name for name in ('selected.db', 'other.db')]
        self.protected = set(commemorative_items()) | set(generation_config()['permanent_replay_items'])
        workspace = AccountDB.new_save_workspace()
        try:
            workspace.execute('INSERT INTO users(uid,nick) VALUES(?,?)', (UID, 'GM probe'))
            workspace.execute('INSERT INTO hero_cfg(hero_id,unlock_star,name) VALUES(?,300,?)', (HERO, 'probe'))
            workspace.execute('INSERT INTO hero_cfg(hero_id,unlock_star,name) VALUES(1084,100,?)', ('starter',))
            workspace.execute('INSERT INTO item_catalog(id,type) VALUES(?,2)', (HERO,))
            items = {41201: 6, 1013011: 10, 90001: 20, 30009: 5, 99999999: 4, 88000000: 4, 2: 1}
            items.update({iid: 10 for iid in self.protected})
            workspace.set_currency(UID, 2, 777)
            for iid, kind in items.items():
                workspace.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)', (iid, kind))
                workspace.execute('INSERT INTO material(uid,id,num) VALUES(?,?,7)', (UID, iid))
            workspace.execute('INSERT INTO material(uid,id,num) VALUES(43,41201,9)')
            prefabs = json.loads((ROOT / 'v5_server/data/hero_details_catalog.json').read_text('utf-8'))['equip_prefabs']
            for key, info in prefabs.items():
                if info['suit_id'] == 1:
                    workspace.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,7)', (int(key),))
            for path in self.paths:
                with closing(sqlite3.connect(path)) as target:
                    workspace._conn().backup(target)
        finally:
            workspace.close()
        self.db = AccountDB.open_existing(self.paths[0])
        self.addCleanup(self.db.close)
        self.stack.enter_context(patch.object(account_db, '_db', self.db))
        self.stack.enter_context(patch.object(gm_api, '_global_get_db', return_value=self.db))
        self.stack.enter_context(patch.object(gm_save_scope, 'catalog', return_value=[
            dict(filename=p.name, uid=UID) for p in self.paths]))
        self.stack.enter_context(patch('save_slot_manager.get_slot_manager', return_value=SaveSlotManager(self.folder)))
        self.backups = self.stack.enter_context(patch.object(AccountDB, 'backup_save_file', return_value='isolated-backup'))
        self.stack.enter_context(patch.object(server_net, '_online_game_conns', {}))
        self.stack.enter_context(patch.object(gm_reader, '_ITEMS_CATALOG_MAP', None))
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(gm_api.gm_bp, url_prefix='/api/gm')
        self.client = app.test_client()

    def post(self, route, body, filename='selected.db'):
        response = self.client.post('/api/gm' + route, json=body, headers={'X-Save-Name': filename})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def state(self, path=None):
        with closing(sqlite3.connect(path or self.paths[0])) as db:
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in ('hero', 'hero_piece', 'material', 'currency', 'equip', 'player_card')}

    def test_unsupported_routes_never_open_writer_or_backup(self):
        with patch.object(gm_api, 'get_db', side_effect=AssertionError('writer opened')):
            for route in ('/heroes/graduate', '/stages/complete_achievements', '/stages/set_polyhedron', '/server/hot_reload'):
                result = self.post(route, dict(uid=UID))
                self.assertEqual(result['code'], gm_api.GMErrorCode.SERVICE_ERROR)
                self.assertIn('未支持', result['msg'])
                self.assertEqual(result['data'], {})
        self.backups.assert_not_called()

    def test_unlock_is_real_idempotent_and_save_scoped(self):
        other = self.state(self.paths[1])
        result = self.post('/heroes/unlock', dict(uid=UID, hero_id=HERO))
        self.assertEqual(result['code'], 0, result)
        hero = self.db.query('SELECT * FROM hero WHERE uid=? AND id=?', (UID, HERO))[0]
        self.assertEqual((hero['unlock'], hero['level'], hero['star']), (1, 1, 300))
        self.assertTrue(json.loads(hero['skill_list']))
        self.db.execute('UPDATE hero SET level=37,weapon_exp=123 WHERE uid=? AND id=?', (UID, HERO))
        self.db.execute('INSERT INTO hero_piece(uid,hero_id,num) VALUES(?,?,13)', (UID, HERO))
        before = self.state()
        self.assertEqual(self.post('/heroes/unlock', dict(uid=UID, hero_id=HERO))['code'], 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.state(self.paths[1]), other)

    def test_all_requires_json_boolean_and_rejects_unknown_hero(self):
        before = self.state()
        for value in ('false', 1, None):
            self.assertNotEqual(self.post('/heroes/unlock', dict(uid=UID, all=value))['code'], 0)
            self.assertEqual(self.state(), before)
        self.assertNotEqual(self.post('/heroes/unlock', dict(uid=UID, hero_id=999999))['code'], 0)
        result = self.post('/heroes/unlock', dict(uid=UID, all=True))
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(self.db.query('SELECT id FROM hero WHERE uid=? AND unlock=1 ORDER BY id', (UID,)),
                         [{'id': HERO}, {'id': 1084}])

    def test_wrong_uid_and_online_guards_preserve_assets(self):
        before = self.state()
        self.assertNotEqual(self.post('/heroes/unlock', dict(uid=43, hero_id=HERO))['code'], 0)
        for connection in (SimpleNamespace(db=SimpleNamespace(path=str(self.paths[0]))), SimpleNamespace()):
            with patch.object(server_net, '_online_game_conns', {UID: [connection]}):
                for route, body in (('/heroes/unlock', dict(hero_id=HERO)), ('/inventory/add_suit', dict(suit_id=1, star=3)),
                                    ('/inventory/clear', dict(category='material'))):
                    self.assertNotEqual(self.post(route, dict(uid=UID, **body))['code'], 0)
                    self.assertEqual(self.state(), before)
        with patch.object(server_net, '_online_game_conns', {UID: [SimpleNamespace(db=SimpleNamespace(path=str(self.paths[1])))]}):
            self.assertEqual(self.post('/heroes/unlock', dict(uid=UID, hero_id=HERO))['code'], 0)

    def test_suit_has_six_configured_slots_and_rolls_back_on_failure(self):
        result = self.post('/inventory/add_suit', dict(uid=UID, suit_id=1, star=3))
        self.assertEqual(result['code'], 0, result)
        rows = self.db.query('SELECT prefab_id FROM equip WHERE uid=? ORDER BY id', (UID,))
        cfg = json.loads((ROOT / 'v5_server/data/hero_details_catalog.json').read_text('utf-8'))['equip_prefabs']
        self.assertEqual(sorted((cfg[str(r['prefab_id'])]['suit_id'], cfg[str(r['prefab_id'])]['star'],
                                 cfg[str(r['prefab_id'])]['pos']) for r in rows), [(1, 3, pos) for pos in range(1, 7)])
        before = self.state()
        self.db.execute("CREATE TRIGGER fail_equip BEFORE INSERT ON equip WHEN NEW.id=9 BEGIN SELECT RAISE(FAIL,'injected third write'); END")
        self.assertNotEqual(self.post('/inventory/add_suit', dict(uid=UID, suit_id=1, star=3))['code'], 0)
        self.assertEqual(self.state(), before)

    def test_clear_only_audited_material_preserves_legacy_assets(self):
        other = self.state(self.paths[1])
        before = self.state()
        for category in (None, 'all', 'item', 'ingredient', 'currency', 'unknown'):
            body = dict(uid=UID) if category is None else dict(uid=UID, category=category)
            self.assertNotEqual(self.post('/inventory/clear', body)['code'], 0)
            self.assertEqual(self.state(), before)
        result = self.post('/inventory/clear', dict(uid=UID, category='material'))
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(result['data']['deleted'], {'material': 1})
        after = self.state()
        for table in before:
            if table != 'material':
                self.assertEqual(after[table], before[table], table)
        self.assertEqual(self.db.query('SELECT uid,id,num FROM material WHERE id=41201'), [{'uid': 43, 'id': 41201, 'num': 9}])
        for iid in self.protected | {1013011, 90001, 30009, 99999999, 88000000, 2}:
            self.assertEqual(self.db.query('SELECT num FROM material WHERE uid=? AND id=?', (UID, iid)), [{'num': 7}], iid)
        self.assertEqual(self.post('/inventory/clear', dict(uid=UID, category='material'))['data']['deleted'], {'material': 0})
        self.assertEqual(self.state(self.paths[1]), other)

    def test_add_item_failure_rolls_back_without_currency_fallback(self):
        before = self.state()
        original = inventory.InventoryService.grant_item
        def fail(ctx, uid, item_id, *args, **kwargs):
            original(ctx, uid, item_id, *args, **kwargs)
            raise RuntimeError('injected after grant')
        with patch.object(inventory.InventoryService, 'grant_item', side_effect=fail):
            self.assertNotEqual(self.post('/inventory/add_item', dict(uid=UID, item_id=41201, num=2))['code'], 0)
        self.assertEqual(self.state(), before)

    def test_missing_shop_goods_returns_existing_error_code_without_mutation(self):
        before = self.state()
        result = self.post('/shop/buy_goods', dict(uid=UID, shop_id=1, goods_id=99999999, free_cost=True))
        self.assertEqual(result['code'], gm_api.GMErrorCode.NOT_FOUND, result)
        self.assertEqual(self.state(), before)

    def test_suit_prevalidation_rejects_incomplete_config_before_writing(self):
        before = self.state()
        mapping = dict(inventory._get_equip_map())
        mapping.pop('330001')
        with patch.object(inventory, '_EQUIP_MAP_CACHE', mapping):
            self.assertNotEqual(self.post('/inventory/add_suit', dict(uid=UID, suit_id=1, star=3))['code'], 0)
        self.assertEqual(self.state(), before)

    def test_clear_protection_and_catalog_class_override_delete_allowlist(self):
        before = self.state()
        with patch('birthday_service.commemorative_items', return_value=self.protected | {41201}):
            result = self.post('/inventory/clear', dict(uid=UID, category='material'))
        self.assertEqual(result['data']['deleted'], {'material': 0})
        self.assertEqual(self.state(), before)
        self.db.execute('UPDATE item_catalog SET type=1 WHERE id=41201')
        result = self.post('/inventory/clear', dict(uid=UID, category='material'))
        self.assertEqual(result['data']['deleted'], {'material': 0})
        self.assertEqual(self.state(), before)


if __name__ == '__main__':
    unittest.main()
