"""A161: real battle/sweep and timer mood delivery, memory SQL and Lua finite timing projection."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import verify_sweep_context as sweep_fixture
import codec
import generator
import middleware as mw
import battle_payload
from event_bus import bus, Events
from trust_service import TrustService
from hero_service import HeroService


class MoodTests(MemoryTests):
    sweep = sweep_fixture.SweepTests.sweep

    def setUp(self):
        super().setUp()
        handlers = patch.dict(bus._handlers, {event: list(items) for event, items in bus._handlers.items()}, clear=True)
        handlers.start()
        self.addCleanup(handlers.stop)
        for event, handlers in bus._handlers.items():
            bus._handlers[event] = [h for h in handlers if h.__module__ not in ('trust_service', 'hero_service')]
        self.db.execute('UPDATE users SET level=70 WHERE uid=?', (UID,))
        self.db.execute('INSERT INTO hero(uid,id,unlock,clear_times,battle_win_times,exp,level) VALUES(?,1284,1,0,0,0,1)', (UID,))
        self.db.set_currency(UID, 4, 1000)
        owner = patch.object(HeroService, '_instance', HeroService(self.db))
        owner.start()
        self.addCleanup(owner.stop)
        HeroService.get_instance(self.db)
        self.trust = TrustService()
        self.trust._init_config()
        owner = patch.object(TrustService, '_instance', self.trust)
        owner.start()
        self.addCleanup(owner.stop)
        self.trust._init_event_listeners()
        self.set_state(4, 1)

    def set_state(self, wins, mood, *, trust=1):
        self.db.execute('UPDATE hero SET clear_times=?,trust_level=?,trust_mood=? WHERE uid=? AND id=1284', (wins, trust, mood, UID))
        self.db.execute("INSERT OR REPLACE INTO user_behavior_stat(uid,stat_key,hero_id,value) VALUES(?,'trust_battle_wins',1284,?)", (UID, wins))

    def battle(self, times=1, result=1, session=None):
        self.conn.battle_id[0] = 123456
        session = session if session is not None else dict(dest=1030101, heroes=[1284], battle_times=times,
                                                          stage_type=1, result_data=dict(result=result, info={}))
        with patch.object(mw, 'get_battle_session', return_value=session):
            return self.call(54032, {}, 54033)

    def heroes(self, response):
        return [codec.decode(f['payload'], 'sc_14007')['hero'] for f in response.frames if f['sc'] == 14007]

    def state(self):
        return self.db.get('hero', UID, 'AND id=1284')

    def test_battle_thresholds_push_final_snapshot_after_ack(self):
        for wins, mood, times, expected in ((4, 1, 1, 2), (9, 2, 1, 3), (4, 1, 3, 2)):
            with self.subTest(wins=wins, times=times):
                self.set_state(wins, mood)
                answer, response = self.battle(times)
                self.assertEqual(answer['result'], 0)
                heroes = self.heroes(response)
                self.assertEqual(len(heroes), 1)
                self.assertEqual(heroes[0]['trust']['mood'], expected)
                self.assertEqual(heroes[0]['clear_times'], wins + times)
                self.assertEqual(self.state()['trust_mood'], expected)
                self.assertLess(response.scs.index(54033), response.scs.index(14007))
                # Native callback adds proficiency; the later absolute snapshot
                # must leave it at the server value rather than adding twice.
                client = wins
                for frame in response.frames:
                    if frame['sc'] == 54033:
                        client += times
                    elif frame['sc'] == 14007:
                        client = codec.decode(frame['payload'], 'sc_14007')['hero']['clear_times']
                self.assertEqual(client, wins + times)

    def test_no_change_unopened_and_quit_do_not_push_mood(self):
        for wins, trust, result in ((0, 1, 1), (4, 0, 1), (4, 1, 3)):
            with self.subTest(trust=trust, result=result):
                self.set_state(wins, 1, trust=trust)
                _, response = self.battle(result=result)
                self.assertEqual(self.heroes(response), [])
                self.assertEqual(self.state()['trust_mood'], 1)

    def test_daily_reset_only_pushes_changed_owned_mood(self):
        ctx = self.conn.to_handler_ctx()
        with patch('trust_service.random.choice', return_value=2):
            bus.emit(Events.DAILY_RESET_5AM, ctx, UID)
        frames = ctx.pop_pending_frames()
        heroes = [codec.decode(f.payload, 'sc_14007')['hero'] for f in frames if f.cmd == 14007]
        self.assertEqual(len(heroes), 1)
        self.assertEqual(heroes[0]['trust']['mood'], 2)
        with patch('trust_service.random.choice', return_value=2):
            bus.emit(Events.DAILY_RESET_5AM, ctx, UID)
        self.assertFalse(any(f.cmd == 14007 for f in ctx.pop_pending_frames()))

    def test_sweep_pushes_after_callback_and_matches_cold_state(self):
        answer, response = self.sweep(times=3)
        self.assertEqual(answer['result'], 0)
        heroes = self.heroes(response)
        self.assertEqual(len(heroes), 1)
        self.assertEqual((heroes[0]['trust']['mood'], heroes[0]['clear_times']), (2, 7))
        self.assertLess(response.scs.index(54039), response.scs.index(14007))
        cold = codec.decode(generator.gen_payload(14009, uid=UID, db=self.db), 'sc_14009')
        current = next(h for h in cold['hero_info_list'] if h['hero_base_info']['id'] == 1284)
        self.assertEqual(current['trust'], heroes[0]['trust'])
        self.assertEqual(current['clear_times'], 7)

    def test_snapshot_uses_final_growth_even_if_trust_listener_runs_first(self):
        callbacks = bus._handlers[Events.STAGE_PASS]
        callbacks.sort(key=lambda h: 0 if h.__module__ == 'trust_service' else 1)
        answer, response = self.battle(times=3)
        self.assertEqual(answer['result'], 0)
        self.assertEqual(self.heroes(response)[0]['clear_times'], 7)

    def test_replayed_receipt_does_not_resend_stale_mood_snapshot(self):
        session = dict(dest=1030101, heroes=[1284], battle_times=1, stage_type=1, result_data=dict(result=1, info={}))
        self.assertEqual(len(self.heroes(self.battle(session=session)[1])), 1)
        self.db.execute('UPDATE hero SET trust_mood=3 WHERE uid=? AND id=1284', (UID,))
        self.assertEqual(self.heroes(self.battle(session=session)[1]), [])
        self.assertEqual(self.state()['trust_mood'], 3)

    def test_sweep_failure_rolls_back_mood_and_deferred_notification(self):
        before = self.state()
        with patch.object(battle_payload, 'build_sc_54039_dynamic', return_value=None):
            answer, response = self.sweep()
        self.assertNotEqual(answer['result'], 0)
        self.assertEqual(response.scs, [54039])
        self.assertEqual(self.state(), before)
        self.assertFalse(getattr(self.conn.to_handler_ctx(), 'pending_trust_mood_heroes', set()))
        self.assertEqual(len(self.heroes(self.sweep()[1])), 1)


if __name__ == '__main__':
    unittest.main()
