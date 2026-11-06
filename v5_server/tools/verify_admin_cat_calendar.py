"""A233/A234: memory Core/codec checks for 05:00 days and weekend claims."""
import datetime
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import admin_cat_explore_service as cat
import codec
import generator
from lazy_timer import get_daily_5am_ts, get_weekly_mon_5am_ts


def stamp(value):
    # Exercise the same configured local game timezone as lazy_timer.
    return int(datetime.datetime.fromisoformat(value).timestamp())


class CalendarTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now = stamp('2026-11-04T12:00:00')
        clock = patch('time.time', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        self.svc = cat.AdminCatExploreService.get_instance()
        self.svc.get_or_create_explore_data(self.db, UID)
        for iid in (53, 54, 55, 56):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)', (iid,))
        self.db.set_currency(UID, 53, 2400)
        self.db.unlock_admin_cat(UID, 10002, skill_level=1)

    def data(self):
        return self.db.get_admin_cat_explore_data(UID)

    def seed(self, **values):
        data = self.svc.get_or_create_explore_data(self.db, UID)
        data.update(values)
        self.db.save_admin_cat_explore_data(UID, data)

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('currency', 'material', 'admin_cat_explore_data', 'admin_cat_explore_queue',
                 'admin_cat_list', 'ops_common', 'task')}

    def dispatch(self, area=10001, mimir=10001):
        return self.call(67004, dict(area_id=area, mimir_id=mimir, hour_time=4), 67005)

    def claim(self):
        return self.call(67008, {}, 67009)

    def two_dispatches(self, first, second, expected):
        self.now = stamp(first)
        self.assertEqual(self.dispatch()[0]['result'], 0)
        first_queue = self.db.get_admin_cat_queues(UID)[0]
        self.now = stamp(second)
        packet, response = self.dispatch(10002, 10002)
        self.assertEqual(packet['result'], 0)
        self.assertEqual(self.data()['weekly_time'], expected)
        self.assertEqual(self.db.get_admin_cat_queues(UID)[0], first_queue)
        snapshot = next(codec.decode(f['payload'], 'sc_67001') for f in response.frames if f['sc'] == 67001)
        self.assertEqual(snapshot['weekly_time'], expected)
        self.assertLess(response.scs.index(67001), response.scs.index(67005))
        cold = codec.decode(generator.gen_payload(67001, uid=UID, db=self.db), 'sc_67001')
        self.assertEqual(cold, snapshot)
        return first_queue

    def test_midnight_stays_in_one_game_day(self):
        self.two_dispatches('2026-11-04T23:59:00', '2026-11-05T00:01:00', 1)
        self.assertEqual(self.data()['daily_time'], stamp('2026-11-04T23:59:00'))

    def test_exact_five_am_starts_a_new_day(self):
        self.two_dispatches('2026-11-05T04:59:59', '2026-11-05T05:00:00', 2)
        self.assertEqual(self.data()['daily_time'], self.now)

    def test_monday_resets_week_and_counts_first_new_day_preserving_old_queue_total(self):
        self.now = stamp('2026-11-09T04:59:59')
        self.seed(weekly_time=6, weekly_reward_state=1, weekly_scene_open=1, total_explore_c=987)
        old = self.two_dispatches('2026-11-09T04:59:59', '2026-11-09T05:00:00', 1)
        data = self.data()
        self.assertEqual((data['weekly_reward_state'], data['weekly_scene_open'], data['total_explore_c']), (0, 0, 987))
        self.assertEqual(data['last_weekly_reset_ts'], self.now)
        self.now = old['stop_time']
        with patch.object(cat.random, 'randint', return_value=100):
            packet, _ = self.call(67006, dict(area_id=10001), 67007)
        self.assertEqual(packet['result'], 0)
        self.assertGreater(self.data()['total_explore_c'], 987)
        self.assertEqual([q['area_id'] for q in self.db.get_admin_cat_queues(UID)], [10002])

    def test_same_day_two_queues_and_failed_dispatch_do_not_add_days(self):
        self.assertEqual(self.dispatch()[0]['result'], 0)
        before = self.state()
        self.assertNotEqual(self.dispatch()[0]['result'], 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.dispatch(10002, 10002)[0]['result'], 0)
        self.assertEqual(self.data()['weekly_time'], 1)

    def test_next_game_day_count_is_capped_at_seven(self):
        self.seed(weekly_time=7, daily_time=self.now - 86400, total_explore_c=987)
        self.assertEqual(self.dispatch()[0]['result'], 0)
        self.assertEqual((self.data()['weekly_time'], self.data()['daily_time'], self.data()['total_explore_c']), (7, self.now, 987))

    def test_failed_queue_write_or_required_snapshot_rolls_back_day_and_can_retry(self):
        for target in ('queue', 'snapshot'):
            with self.subTest(target=target):
                before = self.state()
                if target == 'queue':
                    real = self.db.save_admin_cat_queue
                    def fail(*args, **kwargs):
                        real(*args, **kwargs)
                        raise RuntimeError('after memory queue write')
                    fault = patch.object(self.db, 'save_admin_cat_queue', side_effect=fail)
                else:
                    ctx = self.conn.to_handler_ctx(); real = ctx.codec_encode
                    fault = patch.object(ctx, 'codec_encode', side_effect=lambda schema, *a, **kw:
                                         None if schema == 'sc_67001' else real(schema, *a, **kw))
                with fault:
                    packet, response = self.dispatch()
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual(response.scs, [67005])
                self.assertEqual(self.state(), before)
        self.assertEqual(self.dispatch()[0]['result'], 0)
        self.assertEqual(self.data()['weekly_time'], 1)

    def test_weekdays_and_saturday_before_five_reject_without_mutation(self):
        for value in ('2026-11-04T12:00:00', '2026-11-06T04:59:59', '2026-11-07T04:59:59', '2026-11-09T05:00:00'):
            with self.subTest(value=value):
                self.now = stamp(value)
                self.seed(weekly_time=1, weekly_reward_state=0)
                before = self.state(); packet, response = self.claim()
                self.assertNotEqual(packet['result'], 0)
                self.assertEqual(response.scs, [67009])
                self.assertEqual(self.state(), before)

    def test_game_weekend_including_monday_before_five_accepts_once(self):
        for value in ('2026-11-07T05:00:00', '2026-11-08T12:00:00', '2026-11-09T04:59:59'):
            with self.subTest(value=value):
                self.now = stamp(value)
                self.seed(weekly_time=5, weekly_reward_state=0)
                before = {iid: self.balance(iid) for iid in (54, 55)}
                packet, response = self.claim()
                self.assertEqual(packet['result'], 0)
                # Existing level2 rewards and custom five-day multiplier stay intact.
                self.assertEqual({r['id']: r['num'] for r in packet['reward_list']}, {54: 3600, 55: 1800})
                self.assertEqual(response.scs, [17023, 67009, 67001])
                self.assertEqual({iid: self.balance(iid)-before[iid] for iid in before}, {54: 3600, 55: 1800})
                state = self.state()
                self.assertNotEqual(self.claim()[0]['result'], 0)
                self.assertEqual(self.state(), state)

    def test_monday_cold_reset_preserves_queue_daily_and_permanent_total(self):
        self.now = stamp('2026-11-08T12:00:00')
        self.assertEqual(self.dispatch()[0]['result'], 0)
        self.seed(weekly_reward_state=1, weekly_scene_open=1, total_explore_c=987)
        before_queue = self.db.get_admin_cat_queues(UID)
        last_daily = self.data()['daily_time']
        self.now = stamp('2026-11-09T05:00:00')
        for _ in range(2):
            state = codec.decode(generator.gen_payload(67001, uid=UID, db=self.db), 'sc_67001')
            self.assertEqual((state['weekly_time'], state['weekly_reward_state'], state['weekly_scene_open']), (0, 0, 0))
            self.assertEqual((state['daily_time'], state['total_explore_c']), (last_daily, 987))
        self.assertEqual(self.db.get_admin_cat_queues(UID), before_queue)

    def test_weekend_with_no_current_week_days_stays_locked(self):
        self.now = stamp('2026-11-07T12:00:00'); self.seed(weekly_time=0)
        before = self.state()
        self.assertNotEqual(self.claim()[0]['result'], 0)
        self.assertEqual(self.state(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
