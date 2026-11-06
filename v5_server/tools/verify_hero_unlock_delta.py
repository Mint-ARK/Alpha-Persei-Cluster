"""A64: memory Core/codec plus a bounded projection of native unlock events."""
from collections import Counter
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_hero_acquisition import AcquisitionTests, HERO
from verify_item_use_atomic_random import entry
from verify_report_small_fixes import UID
from verify_hero_race_collect import PUBLIC, RaceCollectTests
from verify_instance_delta_flow import InstanceDeltaTests
import codec
import generator
import hero_codec
import illustrated_listener as illustrated
import inventory_service
import operations
from event_bus import bus, Events
from mail_service import MailService

ROOT = Path(__file__).resolve().parents[2]


class UnlockDeltaTests(AcquisitionTests):
    def setUp(self):
        super().setUp()
        self.race = PUBLIC[HERO]
        self.existing = sorted(h for h, race in PUBLIC.items()
                               if race == self.race and h != HERO)[:2]
        for hid in self.existing:
            self.db.upsert('hero', UID, hero_codec.initial_hero_row(hid, 100), keys=('uid', 'id'))
        self.db.execute('INSERT INTO hero_race_collect(uid,race_type,received_cnt_list) VALUES(?,?,?)',
                        (UID, self.race, '[]'))
        for iid, kind in ((40103, 4),):
            self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(?,?)', (iid, kind))

    def state(self):
        state = super().state()
        for name in ('hero_race_collect', 'mail'):
            state[name] = self.db.query('SELECT * FROM ' + name + ' ORDER BY rowid')
        return state

    def project(self, response, expected=(HERO,)):
        # HeroData.InitHero seeds every public template, including locked ones.
        unlocked = set(self.existing)
        counts = Counter(PUBLIC[h] for h in unlocked)
        unlock_events = []
        for frame in response.frames:
            if frame['sc'] == 17023:
                data = codec.decode(frame['payload'], 'sc_17023')
                for item in data.get('normal_items', []):
                    hid = item['id']
                    if hid in PUBLIC:
                        self.assertEqual(item['num'], 1)
                        # ItemAction -> HeroAction -> HeroData.UnlockHeroSuccess
                        # -> HERO_UNLOCK_SUCCESS -> NewHeroUnlock recalculates counts.
                        unlocked.add(hid)
                        unlock_events.append(hid)
                        counts = Counter(PUBLIC[h] for h in unlocked)
            elif frame['sc'] == 14007:
                data = codec.decode(frame['payload'], 'sc_14007')['hero']
                if data['unlock'] == 1:
                    unlocked.add(data['hero_base_info']['id'])
                # ModifyHero only emits HERO_DATA_MODIFY, not unlock success.
        self.assertEqual(unlock_events, list(expected))
        self.assertEqual(dict(counts), {r: n for r, n in illustrated.get_hero_race_counts(self.db, UID).items() if n})
        self.assertNotIn(14009, response.scs)
        self.assertNotIn(52025, response.scs)
        return counts

    def test_box_unlock_event_reaches_threshold_and_preserves_claims(self):
        before = self.db.query('SELECT * FROM hero_race_collect')
        with self.bundle([[HERO, 1]]):
            packet, response = self.use([entry(30009)])
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.project(response)[self.race], 3)
        self.assertEqual(self.db.query('SELECT * FROM hero_race_collect'), before)
        award, _ = self.call(52022, dict(collect_info_list=[dict(race_type=self.race, cnt_list=[3])]), 52023)
        self.assertEqual(award['result'], 0)
        cold = codec.decode(generator.gen_payload(52025, uid=UID, db=self.db), 'sc_52025')
        self.assertIn(dict(race_type=self.race, cnt_list=[3]), cold['hero_race_collect'])
        portrait = __import__('profile_rules').skin_portrait_config()[str(HERO)]['portrait_id']
        self.assertTrue(self.db.query("SELECT 1 FROM player_card WHERE uid=? AND kind='portrait' AND item_id=? AND obtained=1", (UID, portrait)))

    def test_locked_placeholder_gets_unlock_event_without_reset(self):
        self.locked()
        with self.bundle([[HERO, 1]]):
            packet, response = self.use([entry(30009)])
        self.assertEqual(packet['result'], 0)
        self.project(response)
        self.assertEqual(self.db.get('hero', UID, 'AND id=?', (HERO,))['level'], 37)

    def test_duplicate_box_sends_piece_only_no_unlock_event(self):
        self.db.upsert('hero', UID, hero_codec.initial_hero_row(HERO, 300), keys=('uid', 'id'))
        with self.bundle([[HERO, 1]]):
            packet, response = self.use([entry(30009)])
        self.assertEqual(packet['result'], 0)
        delta = next(codec.decode(f['payload'], 'sc_17023') for f in response.frames if f['sc'] == 17023)
        self.assertNotIn(HERO, [x['id'] for x in delta.get('normal_items', [])])
        self.assertIn(dict(id=11075, num=30), delta['normal_items'])
        self.assertNotIn(52025, response.scs)

    def test_draw_ten_unlocks_once_and_remaining_copies_convert(self):
        self.locked()
        packet, response = self.draw_one(count=10)
        self.assertEqual(packet['result'], 0)
        self.project(response)
        self.assertEqual(self.balance(11075), 270)
        self.assertEqual(response.scs.count(14007), 1)

    def test_mail_unlocks_once_and_repeated_claim_has_no_unlock(self):
        mail = MailService(self.db)
        with patch.object(MailService, '_instance', mail):
            mid = mail.send_mail(UID, 'unlock', 'probe', attachments=[[HERO, 1]])
            packet, response = self.call(30004, dict(id=mid), 30005)
            self.assertEqual(packet['result'], 0)
            self.project(response)
            packet, response = self.call(30004, dict(id=mid), 30005)
            for frame in response.frames:
                if frame['sc'] == 17023:
                    delta = codec.decode(frame['payload'], 'sc_17023')
                    self.assertNotIn(HERO, [x['id'] for x in delta.get('normal_items', [])])

    def test_multiple_new_heroes_share_one_atomic_frame(self):
        other = next(h for h in PUBLIC if h not in self.existing and h != HERO)
        self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,2)', (other,))
        self.db.execute('INSERT OR REPLACE INTO hero_cfg(hero_id,unlock_star,name) VALUES(?,100,?)', (other, 'probe'))
        with self.bundle([[HERO, 1], [other, 1]]):
            packet, response = self.use([entry(30009)])
        self.assertEqual(packet['result'], 0)
        self.assertEqual(response.scs.count(17023), 1)
        # Generator intentionally iterates a set: item order is not a protocol contract.
        delta = next(codec.decode(f['payload'], 'sc_17023') for f in response.frames if f['sc'] == 17023)
        self.assertEqual({x['id'] for x in delta['normal_items'] if x['id'] in PUBLIC}, {HERO, other})
        self.assertEqual(response.scs.count(14007), 2)
        self.assertNotIn(52025, response.scs)

    def test_missing_hero_frame_or_delta_rolls_back_and_retry_unlocks(self):
        for target in ('hero', 'delta'):
            with self.subTest(target=target):
                before = self.state()
                if target == 'hero':
                    fault = patch.object(hero_codec, 'build_hero_14007_frame', return_value=None)
                else:
                    real = generator.gen_payload
                    fault = patch.object(generator, 'gen_payload', side_effect=lambda cmd, *a, **k: None if cmd == 17023 else real(cmd, *a, **k))
                with self.bundle([[HERO, 1]]), fault:
                    packet, response = self.use([entry(30009)])
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual(response.scs, [17013])
                self.assertEqual(self.state(), before)
        with self.bundle([[HERO, 1]]):
            packet, response = self.use([entry(30009)])
        self.assertEqual(packet['result'], 0)
        self.project(response)

    def test_zero_balance_hero_never_emits_false_unlock(self):
        self.locked()
        payload = generator.gen_payload(17023, uid=UID, db=self.db, touched_items={HERO, 11075}, strict=True)
        data = codec.decode(payload, 'sc_17023')
        self.assertNotIn(HERO, [r['id'] for r in data.get('normal_items', [])])
        self.assertIn(dict(id=11075, num=0), data['normal_items'])


if __name__ == '__main__':
    if '--before' in sys.argv:
        # Restore only pre-change definitions; keep all data isolated in memory.
        base = ROOT / 'archive/hero_unlock_delta_20261103/before/v5_server'
        for module, name in ((generator, 'gen_payload'), (operations, '_refresh_frames'),
                             (illustrated, 'on_hero_unlock')):
            path = base / (module.__name__ + '.py')
            node = next(n for n in ast.parse(path.read_text('utf-8-sig')).body
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            node.decorator_list = []
            namespace = dict(vars(module))
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
            setattr(module, name, namespace[name])
            if name == 'on_hero_unlock':
                bus.register(Events.HERO_UNLOCK, namespace[name])
        path = base / 'inventory_service.py'
        cls = next(n for n in ast.parse(path.read_text('utf-8-sig')).body
                   if isinstance(n, ast.ClassDef) and n.name == 'InventoryService')
        node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'grant_items')
        node.decorator_list = []
        namespace = dict(vars(inventory_service))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        inventory_service.InventoryService.grant_items = classmethod(namespace['grant_items'])
    suite = unittest.TestSuite(UnlockDeltaTests(n) for n in UnlockDeltaTests.__dict__ if n.startswith('test_'))
    if '--only' not in sys.argv and '--before' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(RaceCollectTests))
        suite.addTests(InstanceDeltaTests(n) for n in InstanceDeltaTests.__dict__ if n.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
