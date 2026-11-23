"""A162: memory-only real sweep/Core/team/combination/growth and protocol checks."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec
import middleware as mw
import battle_payload
from event_bus import bus, Events
from hero_service import HeroService
from team_server import TeamServer
from cooperation_skill_server import CooperationSkillServer

STAGE = 1030101
COMBO = 109504


class SweepTests(MemoryTests):
    def setUp(self):
        super().setUp()
        handlers = patch.dict(bus._handlers, {event: list(items) for event, items in bus._handlers.items()}, clear=True)
        handlers.start()
        self.addCleanup(handlers.stop)
        # The shared fixture imports entry helpers that already activate these
        # singletons. Install one current test-owned consumer for each domain.
        for event, callbacks in bus._handlers.items():
            bus._handlers[event] = [callback for callback in callbacks
                                    if callback.__module__ not in ('team_server', 'cooperation_skill_server', 'hero_service')]
        self.db.execute('UPDATE users SET level=70 WHERE uid=?', (UID,))
        self.db.execute('INSERT INTO hero(uid,id,unlock,clear_times,battle_win_times,exp,level) VALUES(?,1284,1,0,0,0,1)', (UID,))
        self.db.set_currency(UID, 4, 1000)
        self.events = []
        bus.subscribe(Events.STAGE_PASS, lambda ctx, uid, **kw: self.events.append((uid, kw)))
        self.team = TeamServer()
        owner = patch.object(TeamServer, '_instance', self.team)
        owner.start()
        self.addCleanup(owner.stop)
        self.team._init_event_listeners()
        self.combo = CooperationSkillServer()
        owner = patch.object(CooperationSkillServer, '_instance', self.combo)
        owner.start()
        self.addCleanup(owner.stop)
        self.combo._init_event_listeners()
        owner = patch.object(HeroService, '_instance', HeroService(self.db))
        owner.start()
        self.addCleanup(owner.stop)
        HeroService.get_instance(self.db)

    def sweep(self, *, combo=COMBO, times=3, mimir=True):
        common = dict(type=1, dest=STAGE, activity_id=1234, battle_times=times,
                      hero_list=[dict(hero_type=1, hero_id=1284)],
                      cooperate_unique_skill_id=combo)
        if mimir:
            common['mimir_info'] = dict(mimir_id=1, chip_list=[1, 2])
        return self.call(54038, dict(common_info=common), 54039)

    def test_legacy_sweep_retains_context_without_creating_a_challenge_team(self):
        answer, response = self.sweep()
        self.assertEqual(answer['result'], 0)
        self.assertTrue(self.events)
        uid, event = self.events[-1]
        self.assertEqual((uid, event['stage_type_id'], event['cooperate_skill'], event['mimir_id'], event['mimir_chips']),
                         (UID, 1, COMBO, 1, '1,2'))
        self.assertTrue(event['sweep'])
        self.assertEqual(event['times'], 3)
        team = self.team.get_stage_team(self.db, UID, 1, STAGE)
        self.assertIsNone(team)
        self.assertFalse(self.db.query('SELECT * FROM stage_team WHERE uid=? AND stage_type=0', (UID,)))
        self.assertEqual(self.combo.get_counter(self.db, UID, COMBO)['normal_times'], 3)
        hero = self.db.get('hero', UID, 'AND id=1284')
        self.assertEqual((hero['clear_times'], hero['battle_win_times']), (3, 3))
        self.assertEqual(self.db.get('hero_archive', UID, 'AND archive_id=1084')['exp'], 15)
        self.assertIn(54039, response.scs)

    def challenge(self, hero, result, *, kind=1, stage=STAGE, activity=0, options=(), mimir=None):
        common = dict(type=kind, dest=stage, activity_id=activity, battle_times=1,
                      hero_list=[dict(hero_type=1, hero_id=hero)], opts=list(options))
        if mimir:
            common["mimir_info"] = [mimir]
        answer, _ = self.call(54030, dict(common_info=common), 54031)
        self.assertEqual(answer["result"], 0)
        if result is not None:
            bid = self.conn.to_handler_ctx().battle_id[0]
            mw.get_battle_session(bid)["result_data"] = dict(result=result, info=dict(battle_time=45000))
            self.assertEqual(self.call(54032, dict(battle_id=bid), 54033)[0]["result"], 0)

    def test_sweep_uses_last_winning_team_after_failure_and_quit(self):
        from hero_codec import initial_hero_row
        self.db.upsert("hero", UID, initial_hero_row(1011, 1), keys=("uid", "id"))
        self.challenge(1011, 1)
        for result in (2, 3):
            self.challenge(1284, result)
            self.assertEqual(self.team.get_last_clear_heroes(self.db, UID, STAGE), [1011])
            self.assertEqual(self.team.get_stage_heroes(self.db, UID, 1, STAGE), [1284])
        self.assertEqual(self.sweep()[0]["result"], 0)
        self.assertEqual(self.events[-1][1]["heroes"], [1011])
        self.assertEqual(self.team.get_stage_heroes(self.db, UID, 1, STAGE), [1284])

    def test_mode4_team_slots_and_chips_round_trip(self):
        from core_special_challenge import ANCHOR
        from reserve_codec import build_sc_63005

        self.conn.resource_version = "229"
        with patch("time.time", return_value=ANCHOR + 60):
            for index, chip in ((1, 102), (2, 103)):
                self.challenge(1284, None, kind=93, stage=3093111, activity=4343301,
                               options=[dict(key=2, value=[index])],
                               mimir=dict(mimir_id=2, chip_list=[chip], hero_id=109451,
                                          hero_chip_list=[109452, 109453]))
        data = codec.decode(build_sc_63005(self.db, UID), "sc_63005")
        group = next(row for row in data["teams_info_list"] if row["team_type"] == 431)
        teams = next(row["teams"] for row in group["cont_teams"] if row["cont_id"] == 4)
        self.assertEqual([team["team_index"] for team in teams], [1, 2])
        for team, chip in zip(teams, (102, 103)):
            self.assertEqual(team["hero_list"][0]["hero_id"], 1284)
            self.assertEqual(team["mimir_info"], dict(mimir_id=2, chip_list=[chip],
                                                    hero_id=109451, hero_chip_list=[109452, 109453]))

    def test_sweep_passes_activity_and_does_not_read_unrelated_session(self):
        original = mw._settle_frames
        with patch.object(mw, '_settle_frames', wraps=original) as settle, \
             patch.object(mw, 'get_battle_session', return_value=dict(cooperate_skill=99999, mimir_info=dict(mimir_id=2))) as session:
            self.assertEqual(self.sweep(combo=0, mimir=False, times=1)[0]['result'], 0)
        self.assertEqual(settle.call_args.kwargs.get('stage_type'), 1)
        self.assertEqual(settle.call_args.kwargs.get('activity_id'), 1234)
        session.assert_not_called()
        self.assertEqual(self.events[-1][1]['cooperate_skill'], 0)
        self.assertEqual(self.events[-1][1]['mimir_id'], 0)
        self.assertEqual(self.db.query('SELECT * FROM combo_skill_counter WHERE uid=?', (UID,)), [])

    def test_existing_session_based_settlement_remains_supported(self):
        ctx = self.conn.to_handler_ctx()
        with patch.object(mw, 'get_battle_session', return_value=dict(cooperate_skill=COMBO, mimir_info=dict(mimir_id=1, chip_list=[2]))) as session:
            mw._settle_frames(ctx, UID, 123456, 1, STAGE, times=1, heroes=[1284], sweep=True, stage_type=1)
        session.assert_called_once_with(123456)
        self.assertEqual(self.events[-1][1]['cooperate_skill'], COMBO)
        self.assertEqual(self.events[-1][1]['mimir_chips'], '2')

    def test_ack_failure_rolls_back_team_counters_and_growth(self):
        tables = ('hero', 'hero_archive', 'stage_team', 'combo_skill_counter', 'currency', 'material')
        before = {t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid') for t in tables}
        with patch.object(battle_payload, 'build_sc_54039_dynamic', return_value=None):
            answer, response = self.sweep()
        self.assertNotEqual(answer['result'], 0)
        self.assertEqual({t: self.db.query('SELECT * FROM ' + t + ' ORDER BY rowid') for t in tables}, before)
        self.assertEqual(response.scs, [54039])
        self.assertEqual(self.sweep()[0]['result'], 0)
        self.assertEqual(self.combo.get_counter(self.db, UID, COMBO)['total_times'], 3)


if __name__ == '__main__':
    unittest.main()
