"""Memory-only regression checks for A237/A238/A240/A365/A376."""
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
import account_db
from account_db import AccountDB
import admin_cat_explore_service as cat
import battle_server as battle
import codec
import core
import generator
import operations
import task_listener
import test_battle_entry_timing as timing
from event_bus import bus, Events
from inventory_service import InventoryService
from shop_service import ShopService

UID = 42
NOW = 1792771200


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.db = AccountDB.new_save_workspace()
        self.addCleanup(self.db.close)
        owner = patch.object(account_db, '_db', self.db)
        owner.start(); self.addCleanup(owner.stop)
        pulse = patch('lazy_timer.lazy_timer.pulse')
        pulse.start(); self.addCleanup(pulse.stop)
        self.db.execute('INSERT INTO users(uid,nick) VALUES(?,?)', (UID, '探针'))
        self.core = core.Core(db=self.db, generator=generator, codec_encode=codec.encode,
                              codec_decode=codec.decode, log=lambda *a, **k: None)
        self.conn = core.Connection(uid=UID, db=self.db, generator=generator,
                                    codec_encode=codec.encode, codec_decode=codec.decode,
                                    log=lambda *a, **k: None)

    def call(self, cmd, data, sc):
        response = self.core.dispatch(core.CoreRequest(cmd, codec.encode('cs_' + str(cmd), data), uid=UID), self.conn)
        packets = [codec.decode(f['payload'], 'sc_' + str(sc)) for f in response.frames if f['sc'] == sc]
        self.assertTrue(packets, (sc, response.scs))
        return packets[0], response

    def balance(self, item):
        return InventoryService.get_item_balance(self.db, UID, item)[1]


class CatTests(MemoryTests):
    def setUp(self):
        super().setUp()
        # New-save memory workspaces have no item catalog. Native exploration
        # currencies are required by the strict17023 path exercised below.
        for iid in (53,54,55,56):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))
        clock = patch.object(cat, 'time', SimpleNamespace(time=lambda: NOW, localtime=time.localtime))
        clock.start(); self.addCleanup(clock.stop)
        self.svc = cat.AdminCatExploreService.get_instance()
        self.svc.get_or_create_explore_data(self.db, UID)
        self.db.set_currency(UID, 56, 2000)

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('currency', 'admin_cat_list', 'admin_cat_explore_data', 'admin_cat_explore_queue')}

    def upgrade(self, skill):
        return self.call(67002, dict(mimir_id=10001, skill_id=skill), 67003)

    def test_wrong_skill_ids_do_not_spend_or_upgrade(self):
        before = self.state()
        for skill in (10002, 999999, 0):
            with self.subTest(skill=skill):
                packet, _ = self.upgrade(skill)
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual(self.state(), before)

    def test_valid_skill_and_cold_snapshot(self):
        self.assertEqual(self.upgrade(10001)[0]['result'], 0)
        self.assertEqual(self.balance(56), 1700)
        cold = codec.decode(generator.gen_payload(67001, uid=UID, db=self.db), 'sc_67001')
        self.assertEqual(cold['mimir'][0]['skill'], [dict(skill_id=10001, skill_level=2)])

    def test_skill_guards_and_write_failure_retry(self):
        self.db.set_currency(UID, 56, 299)
        self.assertEqual(self.upgrade(10001)[0]['result'], 402)
        self.db.set_currency(UID, 56, 2000)
        before = self.state()
        with patch.object(self.db, 'update_admin_cat_skill', side_effect=RuntimeError('isolated skill write')):
            self.assertNotEqual(self.upgrade(10001)[0]['result'], 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.upgrade(10001)[0]['result'], 0)
        self.assertEqual(self.balance(56), 1700)
        self.db.update_admin_cat_skill(UID, 10001, 10)
        self.assertNotEqual(self.upgrade(10001)[0]['result'], 0)
        self.assertEqual(self.balance(56), 1700)

    def prepare_unlock(self, mid, level):
        cfg = cat.EXPLORE_MEOW_CFG[mid]
        data = self.db.get_admin_cat_explore_data(UID)
        data['total_explore_c'] = cfg['unlock_condition'][0][1]
        self.db.save_admin_cat_explore_data(UID, data)
        total_exp = sum(cat.EXPLORE_LEVEL_CFG[i]['exp'] for i in range(1, level))
        self.db.set_currency(UID, 53, total_exp)
        self.db.set_currency(UID, 56, 2000)

    def unlock(self, mid):
        return self.call(67010, dict(mimir_id=mid), 67011)[0]['result']

    def test_level_gates_for_each_new_cat_and_existing_idempotency(self):
        for mid, level in ((10002, 2), (10003, 3), (10004, 4), (10005, 5)):
            with self.subTest(mid=mid):
                self.prepare_unlock(mid, level - 1)
                before = self.state()
                self.assertNotEqual(self.unlock(mid), 0)
                self.assertEqual(self.state(), before)
                self.prepare_unlock(mid, level)
                self.assertEqual(self.unlock(mid), 0)
                self.assertEqual(self.balance(56), 2000 - cat.EXPLORE_MEOW_CFG[mid]['consume'][0][1])
                self.db.set_currency(UID, 53, 0)
                before = self.state()
                self.assertEqual(self.unlock(mid), 0)
                self.assertEqual(self.state(), before)

    def test_unlock_cost_total_and_write_failure_retry(self):
        self.prepare_unlock(10002, 2)
        data = self.db.get_admin_cat_explore_data(UID)
        data['total_explore_c'] = 399
        self.db.save_admin_cat_explore_data(UID, data)
        self.assertNotEqual(self.unlock(10002), 0)
        self.prepare_unlock(10002, 2)
        self.db.set_currency(UID, 56, 599)
        self.assertEqual(self.unlock(10002), 402)
        self.prepare_unlock(10002, 2)
        before = self.state()
        with patch.object(self.db, 'unlock_admin_cat', side_effect=RuntimeError('isolated unlock write')):
            self.assertNotEqual(self.unlock(10002), 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.unlock(10002), 0)
        self.assertEqual(self.balance(56), 1400)

    def test_exact_ceiling_reward_persistence_receipt_and_retry(self):
        self.db.save_admin_cat_queue(UID, 10001, 10001, NOW - 14400, NOW, 4, [])
        before = {item: self.balance(item) for item in (53, 54, 56)}
        with patch.object(cat.random, 'randint', return_value=100):
            packet, _ = self.call(67006, dict(area_id=10001), 67007)
        self.assertEqual(packet['result'], 0)
        rewards = {r['id']: r['num'] for r in packet['reward_list']}
        self.assertEqual(rewards, {53: 220, 54: 264, 56: 36})
        for item, amount in rewards.items():
            self.assertEqual(self.balance(item) - before[item], amount)
        self.assertEqual(self.db.get_admin_cat_explore_data(UID)['total_explore_c'], 300)
        after = self.state()
        self.assertNotEqual(self.call(67006, dict(area_id=10001), 67007)[0]['result'], 0)
        self.assertEqual(self.state(), after)


class ShopTaskTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for tid, params, need in ((6005, [0, 0], 1), (40308, [[32], 0], 5),
                                  (120005, [[2], 0], 3), (160074, [32, 0], 1)):
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,additional_parameter) VALUES(?,4,600,?,?)',
                            (tid, need, json.dumps(params)))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,4)', (UID, tid))

    def progress(self):
        return {r['task_id']: r['progress'] for r in self.db.query('SELECT task_id,progress FROM task WHERE uid=?', (UID,))}

    def buy_event(self, shop, count=1):
        ctx = self.conn.to_handler_ctx()
        ctx.pending_frames = []
        bus.emit(Events.SHOP_BUY, ctx, UID, count=count, shop_id=shop)
        return [codec.decode(f.payload, 'sc_28007') for f in ctx.pending_frames if f.cmd == 28007]

    def test_shop_restriction_nested_legacy_flat_and_online_diff(self):
        packets = self.buy_event(32, 2)
        self.assertEqual(self.progress(), {6005: 1, 40308: 2, 120005: 0, 160074: 1})
        ids = [r['id'] for p in packets for r in p['progress_list']]
        self.assertEqual(set(ids), {6005, 40308, 160074})
        self.assertEqual(len(ids), len(set(ids)))
        self.buy_event(2)
        self.assertEqual(self.progress()[40308], 2)
        self.assertEqual(self.progress()[120005], 1)
        self.buy_event(32, 99)
        self.assertEqual(self.progress()[40308], 5)

    def test_missing_shop_and_malformed_restrictions_do_not_match(self):
        for value in ('broken-json', '[["bad"],0]', '[[32],99]'):
            with self.subTest(value=value):
                self.db.execute('UPDATE task SET progress=0 WHERE task_id=40308')
                self.db.execute('UPDATE task_cfg SET additional_parameter=? WHERE task_id=40308', (value,))
                self.buy_event(32)
                self.assertEqual(self.progress()[40308], 0)
        self.db.execute("UPDATE task_cfg SET additional_parameter='[[32],0]' WHERE task_id=40308")
        self.buy_event(None)
        self.assertEqual(self.progress()[40308], 0)
        self.assertEqual(self.progress()[6005], 1)

    def test_legacy_csv_multiple_shops_and_wildcard(self):
        for params in ('32,0', '[32,0]', '[[32,10],0]'):
            with self.subTest(params=params):
                self.db.execute('UPDATE task SET progress=0 WHERE task_id=40308')
                self.db.execute('UPDATE task_cfg SET additional_parameter=? WHERE task_id=40308', (params,))
                self.buy_event(2)
                self.assertEqual(self.progress()[40308], 0)
                self.buy_event(32)
                self.assertEqual(self.progress()[40308], 1)
        self.db.execute("UPDATE task_cfg SET additional_parameter='0,0' WHERE task_id=6005")
        self.db.execute('UPDATE task SET progress=0 WHERE task_id=6005')
        self.buy_event(10)
        self.assertEqual(self.progress()[6005], 1)

    def test_claimed_and_inactive_tasks_stay_unchanged(self):
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=40308')
        self.db.execute('DELETE FROM task WHERE task_id=120005')
        self.buy_event(32)
        self.buy_event(2)
        self.assertEqual(self.progress()[40308], 0)
        self.assertNotIn(120005, self.progress())

    def test_actual_purchase_uses_goods_owner_and_failed_buy_does_not_count(self):
        singleton = patch.object(ShopService, '_instance', None)
        singleton.start(); self.addCleanup(singleton.stop)
        # Explicit minimal goods fixtures; no real shelf or account is changed.
        for gid, sid in ((32000001, 32), (10000001, 10)):
            self.db.execute('INSERT INTO shop_goods(goods_id,shop_id,item_id,cost_id,cost,limit_num) VALUES(?,?,2,56,10,5)', (gid, sid))
        self.db.set_currency(UID, 56, 100)
        result, response = self.call(20012, dict(shop_id=32, buy_goods_list=[dict(buy_id=32000001, buy_num=2)]), 20013)
        self.assertEqual(result['result'], 0)
        self.assertEqual(self.balance(56), 80)
        self.assertEqual(self.progress()[40308], 2)
        ids = [r['id'] for f in response.frames if f['sc'] == 28007
               for r in codec.decode(f['payload'], 'sc_28007')['progress_list']]
        self.assertIn(40308, ids)
        # Existing wrong-shop acceptance is A275; it must not manufacture a
        # shop32 task fact when the purchased goods actually belong to shop10.
        result, _ = self.call(20012, dict(shop_id=32, buy_goods_list=[dict(buy_id=10000001, buy_num=1)]), 20013)
        self.assertEqual(self.progress()[40308], 2)
        self.db.set_currency(UID, 56, 0)
        before = self.progress()
        result, _ = self.call(20012, dict(shop_id=32, buy_goods_list=[dict(buy_id=32000001, buy_num=1)]), 20013)
        self.assertNotEqual(result['result'], 0)
        self.assertEqual(self.progress(), before)

    def test_actual_mixed_batch_failure_rolls_back_purchase_and_task(self):
        singleton = patch.object(ShopService, '_instance', None)
        singleton.start(); self.addCleanup(singleton.stop)
        self.db.execute('INSERT INTO shop_goods(goods_id,shop_id,item_id,cost_id,cost) VALUES(32000001,32,2,56,10)')
        self.db.set_currency(UID, 56, 100)
        before = self.progress()
        packet, _ = self.call(20012, dict(shop_id=32, buy_goods_list=[dict(buy_id=32000001, buy_num=1),
                                  dict(buy_id=987654321, buy_num=1)]), 20013)
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(self.balance(56), 100)
        self.assertEqual(self.balance(2), 0)
        self.assertEqual(self.progress(), before)

    def test_mixed_shop_counts_and_daily_pool_ownership(self):
        singleton = patch.object(ShopService, '_instance', None)
        singleton.start(); self.addCleanup(singleton.stop)
        self.db.execute('UPDATE task_cfg SET need=10 WHERE task_id=6005')
        for gid, sid in ((32000001, 32), (10000001, 10)):
            self.db.execute('INSERT INTO shop_goods(goods_id,shop_id,item_id,cost_id,cost) VALUES(?,?,2,56,10)', (gid, sid))
        self.db.set_currency(UID, 56, 100)
        packet, _ = self.call(20012, dict(shop_id=32, buy_goods_list=[dict(buy_id=32000001, buy_num=2),
                                  dict(buy_id=10000001, buy_num=3)]), 20013)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.progress()[6005], 5)
        self.assertEqual(self.progress()[40308], 2)
        self.assertEqual(self.balance(56), 50)
        shop = ShopService.get_instance(db=self.db)
        pool_good = dict(goods_id=2001001, item_id=2, cost_id=56, cost=10)
        with patch.object(shop, '_get_daily_shop_pool', return_value={'1': [pool_good]}):
            self.assertEqual(shop._find_goods_cfg(2001001, 32)['shop_id'], 2)
        self.assertNotIn('shop_id', pool_good)


class BattleCleanupTests(unittest.TestCase):
    def setUp(self):
        self.srv = battle.BattleServer.__new__(battle.BattleServer)
        self.srv._lock = threading.Lock()
        self.srv._conns = {}; self.srv._pong_seq = {}; self.srv._results = {}
        self.srv.server_ts = lambda: NOW
        self.srv.log = lambda *a: None
        self.sent = []
        self.srv._send_frame = lambda *a, **k: self.sent.append((a, k))

    def session(self, cid=1):
        return self.srv._new_session(cid, 123, ('127.0.0.1', 10000))

    def ping(self, st):
        frame = battle.xs_encode_frame(battle.OP_PING, struct.pack('<Q', NOW * 1000), 2, 7)
        self.srv._handle_frame(frame, st)

    def gc(self, now):
        self.srv.running = True
        with patch.object(battle.time, 'sleep', side_effect=lambda _: setattr(self.srv, 'running', False)), \
             patch.object(battle.time, 'time', return_value=now):
            self.srv._gc_loop()

    def test_idle_cleanup_keeps_live_cursor_and_retry_results(self):
        old = self.session(); live = self.session(2)
        old['last_seen'] = 0; live['last_seen'] = 1
        self.ping(old); self.ping(live)
        self.srv._results = {i: {'result': 1} for i in range(206)}
        self.gc(battle.SESSION_IDLE_TIMEOUT)
        self.assertIn(1, self.srv._conns)
        self.gc(battle.SESSION_IDLE_TIMEOUT + 1)
        self.assertNotIn(1, self.srv._pong_seq)
        self.assertEqual(self.srv._pong_seq, {2: 1})
        self.assertEqual(len(self.srv._results), 200)
        self.assertEqual(self.srv.get_result(205), self.srv.get_result(205))

    def test_fin_err_cleanup(self):
        for op in (battle.FIN, battle.ERR):
            self.ping(self.session())
            self.srv._handle_pkt(bytes([op]) + struct.pack('<I', 1), ('127.0.0.1', 10000))
            self.assertEqual(self.srv._conns, {})
            self.assertEqual(self.srv._pong_seq, {})

    def test_replacement_resets_cursor_and_old_ping_cannot_change_new_one(self):
        old = self.session(); self.ping(old); self.ping(old)
        new = self.session()
        self.ping(new)
        self.assertEqual(self.srv._pong_seq[1], 1)
        sent = len(self.sent)
        self.ping(old)
        self.assertEqual(self.srv._pong_seq[1], 1)
        self.assertEqual(len(self.sent), sent)
        self.assertIs(self.srv._conns[1], new)
        self.assertEqual(struct.unpack('<QQ', self.sent[-1][0][3]), (NOW * 1000, NOW * 1000))

    def test_stale_cleanup_does_not_remove_replacement_and_compat_clock(self):
        old = self.session(); new = self.session()
        self.srv.server_ts = None
        self.ping(new)
        with self.srv._lock:
            self.assertFalse(self.srv._drop_session(1, old))
        self.assertIs(self.srv._conns[1], new)
        self.assertEqual(self.srv._pong_seq[1], 1)
        ct, srv = struct.unpack('<QQ', self.sent[-1][0][3])
        self.assertEqual(srv - ct, battle.PONG_DELTAS[0])


class BattleTimingMemoryTests(timing.BattleEntryTests):
    """Reuse the six timing checks with actual packet handlers and memory I/O."""
    def setUp(self):
        self.logs = []
        self.server = battle.BattleServer.__new__(battle.BattleServer)
        self.server._lock = threading.Lock()
        self.server._conns = {}; self.server._pong_seq = {}; self.server._results = {}
        self.server.log = self.logs.append
        self.server.server_ts = None
        self.server.running = False
        self.packets = []
        self.server.sock = SimpleNamespace(sendto=lambda packet, addr: self.packets.append(packet))
        self.exchange(bytes([battle.SYN]) + struct.pack('<II', 42, 0), 1)
        self.st = self.server._conns[42]

    def exchange(self, packet, count):
        self.packets.clear()
        self.server._handle_pkt(packet, ('127.0.0.1', 10000))
        self.assertEqual(len(self.packets), count)
        return list(self.packets)

    def tearDown(self):
        self.server.stop()


if __name__ == '__main__':
    unittest.main(verbosity=2)
