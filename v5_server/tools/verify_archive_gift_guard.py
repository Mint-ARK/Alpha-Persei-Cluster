"""A155: first-tier gift eligibility, actual Core and memory Inventory/SQLite/codec."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from extract_autochess_cfg import parse_lua_file
from archive_service import ArchiveService
from event_bus import Events, bus

ROOT = Path(__file__).resolve().parents[2]
ITEMS = parse_lua_file(ROOT / 'decompiled_v2/x64/game/config/itemcfg.lua')
FIRST_TIER = {iid for iid, cfg in ITEMS.items() if isinstance(iid, int)
              and cfg.get('type') == 10 and cfg.get('sub_type') == 1}
PREFERRED = 50002
NORMAL = next(iid for iid in sorted(FIRST_TIER) if iid != PREFERRED)


class GiftTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = ArchiveService()
        self.db.execute('INSERT INTO hero(uid,id,unlock) VALUES(?,1284,1)', (UID,))
        self.db.execute('INSERT INTO hero_archive(uid,archive_id) VALUES(?,1084)', (UID,))
        for iid in FIRST_TIER | {1, 30012, 40103, 99999999}:
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)', (iid, ITEMS.get(iid, {}).get('type', 4)))
            if iid == 1:
                self.db.set_currency(UID, iid, 20)
            else:
                self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,?,20)', (UID, iid))

    def gift(self, gifts, archive=1084):
        with patch.object(ArchiveService, 'get_instance', return_value=self.svc):
            return self.call(14100, dict(archive_id=archive, gift_list=gifts), 14101)

    def row(self):
        return self.db.get('hero_archive', UID, 'AND archive_id=1084')

    def snapshot(self):
        return {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid')
                for t in ('hero', 'hero_archive', 'currency', 'material')}

    def test_export_matches_all_current_first_tier_gifts(self):
        cfg = json.loads((ROOT / 'v5_server/archive_config.json').read_text('utf-8'))
        self.assertEqual(set(cfg.get('first_tier_gift_ids', [])), FIRST_TIER)
        self.assertTrue(FIRST_TIER)
        self.assertNotIn(30012, FIRST_TIER)

    def test_invalid_single_and_mixed_requests_are_rejected_before_consumption(self):
        for iid in (1, 30012, 40103, 99999999):
            for gifts in ([dict(id=iid, num=1)], [dict(id=PREFERRED, num=1), dict(id=iid, num=1)]):
                with self.subTest(item=iid, mixed=len(gifts) == 2):
                    before = self.snapshot()
                    answer, response = self.gift(gifts)
                    self.assertEqual(answer['result'], 2)
                    self.assertEqual(response.scs, [14101])
                    self.assertEqual(self.snapshot(), before)

    def test_preferred_normal_and_duplicate_gifts_keep_paid_amount_and_exp(self):
        gifts = [dict(id=PREFERRED, num=1), dict(id=NORMAL, num=1), dict(id=PREFERRED, num=2)]
        answer, response = self.gift(gifts)
        self.assertEqual(answer['result'], 0)
        self.assertEqual(self.balance(PREFERRED), 17)
        self.assertEqual(self.balance(NORMAL), 19)
        self.assertEqual(self.row()['exp'], 35)
        self.assertEqual(json.loads(self.row()['gift_list']), sorted([dict(id=PREFERRED, num=3), dict(id=NORMAL, num=1)], key=lambda x:x['id']))
        self.assertIn(17023, response.scs)

    def test_every_configured_normal_gift_is_accepted(self):
        gifts = [dict(id=iid, num=1) for iid in sorted(FIRST_TIER)]
        self.assertEqual(self.gift(gifts)[0]['result'], 0)
        self.assertEqual(self.row()['exp'], 5 * len(FIRST_TIER) + 5)
        self.assertTrue(all(self.balance(iid) == 19 for iid in FIRST_TIER))

    def test_cap_and_missing_ownership_still_apply(self):
        self.db.execute('UPDATE hero_archive SET exp=995 WHERE uid=?', (UID,))
        self.assertEqual(self.gift([dict(id=PREFERRED, num=2)])[0]['result'], 0)
        self.assertEqual(self.row()['exp'], 1000)
        self.assertEqual(self.balance(PREFERRED), 18)
        before = self.snapshot()
        self.assertEqual(self.gift([dict(id=PREFERRED, num=1)])[0]['result'], 2)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.gift([dict(id=PREFERRED, num=1)], archive=1011)[0]['result'], 2)

    def test_insufficient_balance_rolls_back_other_valid_gift(self):
        before = self.snapshot()
        answer, response = self.gift([dict(id=PREFERRED, num=1), dict(id=NORMAL, num=21)])
        self.assertNotEqual(answer['result'], 0)
        self.assertEqual(response.scs, [14101])
        self.assertEqual(self.snapshot(), before)

    def test_archive_write_failure_rolls_back_then_retry(self):
        before = self.snapshot()
        real = self.db.execute
        def fail(sql, *args, **kwargs):
            if sql.startswith('UPDATE hero_archive SET exp='):
                raise RuntimeError('isolated archive write failure')
            return real(sql, *args, **kwargs)
        with patch.object(self.db, 'execute', side_effect=fail):
            self.assertNotEqual(self.gift([dict(id=PREFERRED, num=1)])[0]['result'], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.gift([dict(id=PREFERRED, num=1)])[0]['result'], 0)
        self.assertEqual(self.balance(PREFERRED), 19)
        self.assertEqual(self.row()['exp'], 10)

    def test_direct_service_invalid_batch_does_not_publish_exp_or_write(self):
        before = self.snapshot()
        with patch.object(bus, 'emit', wraps=bus.emit) as emitted:
            with self.assertRaises(ValueError):
                self.svc.give_gifts(self.conn.to_handler_ctx(), UID, 1084,
                                    [dict(id=PREFERRED, num=1), dict(id=30012, num=1)])
        self.assertFalse(any(call.args[0] == Events.HERO_ARCHIVE_EXP_CHANGE for call in emitted.call_args_list))
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
