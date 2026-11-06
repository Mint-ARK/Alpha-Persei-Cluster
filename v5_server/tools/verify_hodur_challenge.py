"""Hodur challenge/native UDP character tests; no live save or socket writes."""
import copy
import json
import unittest
from unittest.mock import patch
from verify_hodur_mainline import HodurTests, UID, NOW, codec, core, generator, mw
import battle_server as wire
import hero_codec
import hodur_service as normal
import hodur_challenge as challenge
from event_bus import bus,Events


class ChallengeTests(HodurTests):
    def setUp(self):
        super().setUp()
        for hid in (1048,1039):self.db.upsert('hero',UID,hero_codec.initial_hero_row(hid,100),keys=('uid','id'))
        for chapter,row in normal.read(self.db,UID).items():
            row['clears']={s:1 for s in normal.CHAPTERS[chapter]['stages']};normal.save(self.db,UID,row)

    def open(self,stage=5310203,team=None,affixes=None):
        team=[1084,1048] if team is None else team;affixes=[] if affixes is None else affixes
        packet,response=self.call(54030,dict(common_info=dict(type=92,dest=stage,activity_id=3941101,
            hero_list=[dict(hero_id=i,hero_type=2 if str(i) in challenge.trials() else 1) for i in team],
            opts=[dict(key=1,value=affixes)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def raw_result(self,chars,result=1,milliseconds=120000,hits=2):
        body=wire._f_varint2(1,int(result==1))+wire._f_varint2(2,milliseconds)+wire._f_varint2(4,hits)
        for hid,hp,maximum in chars:
            character=wire._f_varint2(1,hid)+wire._f_varint2(3,maximum)
            if hp:character+=wire._f_varint2(2,hp & 0xffffffffffffffff)  # signed long; proto3 omits zero
            body+=wire._f_bytes(10,character)
        packet=wire._f_varint2(1,self.bid)+wire._f_varint2(2,UID)+wire._f_varint2(3,result)+wire._f_bytes(4,body)
        bid,uid,outcome,report=wire.parse_battle_result(packet)
        self.assertEqual((bid,uid,outcome),(self.bid,UID,result))
        return outcome,report

    def finish(self,hps=None,result=1,milliseconds=120000,hits=2,report_ids=None):
        session=mw.get_battle_session(self.bid);team=session['hodur_context']['team']
        hps={i:75 for i in team} if hps is None else hps
        chars=[((report_ids or {}).get(i,i),hps[i],100) for i in hps]
        outcome,report=self.raw_result(chars,result,milliseconds,hits)
        session['result_data']=dict(result=outcome,info=report)
        return self.call(54032,dict(battle_id=self.bid),54033)

    def settle_run(self):return self.call(89424,dict(activity_id=3941101),89425)
    def run_all(self,affixes=None):
        for stage in challenge.STAGES:
            self.assertEqual(self.open(stage,affixes=affixes)[0]['result'],0)
            self.assertEqual(self.finish(milliseconds=600000,hits=99)[0]['result'],0)

    def test_native_udp_keeps_every_character_zero_hp_and_signed_long_bounds(self):
        self.open();_,report=self.raw_result([(1084,0,100),(1048,90,100)])
        self.assertEqual(report['characters'],[dict(hero_id=1084,hero_blood=0,hero_max_blood=100),dict(hero_id=1048,hero_blood=90,hero_max_blood=100)])
        _,report=self.raw_result([(1084,2**40,2**41)])
        self.assertEqual(report['characters'][0]['hero_blood'],2**40)
        _,report=self.raw_result([(1084,-1,100)])
        self.assertEqual(report['characters'][0]['hero_blood'],-1)
        out=challenge.report_hp(report,dict(team=[1084],aliases={'1084':[1084]}))
        self.assertEqual(out[1084]['hero_blood'],0)

    def test_native_udp_malformed_character_never_becomes_an_empty_full_health_team(self):
        malformed=wire._f_bytes(10,b'\x08\x80')
        for body in [malformed,wire._f_bytes(10,wire._f_varint2(1,1084)),wire._f_bytes(10,wire._f_varint2(1,1084)+wire._f_varint2(1,1048))]:
            with self.assertRaises((ValueError,IndexError)):wire.parse_battle_characters(body)

    def test_initial_challenge_snapshot_and_start_publication_order(self):
        before=self.state();initial=normal.payload(self.db,UID)['challenge_stage_info'][0]
        self.assertEqual(initial['score_list'],[0]*4);self.assertEqual(len(initial['challenge_boss_list']),4);self.assertEqual(self.state(),before)
        packet,response=self.open(affixes=[80478,80486])
        self.assertEqual(packet['result'],0);self.assertLess(response.scs.index(89421),response.scs.index(54003))
        s=challenge.read(self.db,UID);self.assertTrue(s['active']);self.assertEqual(s['affixes'],[80478,80486])
        self.assertEqual([h['hero_id'] for h in s['heroes']],[1084,1048])

    def test_start_requires_normal_route_known_unique_team_and_native_affixes(self):
        before=self.state()
        for stage,team,affixes in [(5310204,[1084],[]),(5310203,[],[]),(5310203,[999999],[]),(5310203,[1084,1084],[]),
            (5310203,[236,113703],[]),(5310203,[1084],[999999]),(5310203,[1084],[80478,80478])]:
            self.assertNotEqual(self.open(stage,team,affixes)[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.execute('UPDATE hodur_progress SET stage_clear_json=? WHERE uid=? AND chapter_id=3',(json.dumps([]),UID))
        before=self.state();self.assertNotEqual(self.open()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_start_packet_and_session_publication_failures_leave_no_run_or_orphan(self):
        before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_89421' else encode(n,d)):
            self.assertNotEqual(self.open()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(mw._BATTLE_SESSIONS,{})
        register=mw.register_battle_session
        def fail(*a,**k):register(*a,**k);raise RuntimeError('isolated post-register failure')
        with patch.object(mw,'register_battle_session',side_effect=fail):self.assertNotEqual(self.open()[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(mw._BATTLE_SESSIONS,{})

    def test_hp_carry_death_filters_next_team_and_keeps_dead_roster_entry(self):
        self.open();self.assertEqual(self.finish({1084:40,1048:0})[0]['result'],0)
        self.assertNotEqual(self.open(5310204)[0]['result'],0)
        self.assertEqual(self.open(5310204,team=[1084])[0]['result'],0)
        self.assertEqual(self.finish({1084:20})[0]['result'],0)
        s=challenge.read(self.db,UID)
        self.assertEqual([(h['hero_id'],h['hero_blood']) for h in s['heroes']],[(1084,20),(1048,0)])
        self.assertEqual(s['last_status'][0]['hero_blood'],40)

    def test_trial_and_skin_reports_map_back_to_original_hero_reference(self):
        self.assertEqual(self.open(team=[236])[0]['result'],0)
        self.assertEqual(self.finish({236:60},report_ids={236:1137})[0]['result'],0)
        self.assertEqual(challenge.read(self.db,UID)['heroes'][0]['hero_id'],236)
        self.settle_run()
        self.db.execute('UPDATE hero SET using_skin=108401,battle_using_skin=108401 WHERE uid=? AND id=1084',(UID,))
        self.open(team=[1084]);self.assertEqual(self.finish({1084:50},report_ids={1084:108401})[0]['result'],0)
        self.assertEqual(challenge.read(self.db,UID)['heroes'][0]['hero_id'],1084)

    def test_native_uint32_hp_output_normalizes_large_hp_without_healing_zero(self):
        token=dict(team=[1084],aliases={'1084':[1084]})
        out=challenge.report_hp(dict(characters=[dict(hero_id=1084,hero_blood=2**40,hero_max_blood=2**41)]),token)
        self.assertEqual(out[1084]['hero_blood'],500000)
        out=challenge.report_hp(dict(characters=[dict(hero_id=1084,hero_blood=0,hero_max_blood=2**41)]),token)
        self.assertEqual(out[1084]['hero_blood'],0)

    def test_bad_or_incomplete_hp_report_rolls_back_and_never_caches_br(self):
        self.open();before=self.state()
        for hps,ids in [({1084:50},None),({1084:50,1048:50},{1048:999999}),({1084:50,1048:50},{1048:1084})]:
            self.assertNotEqual(self.finish(hps,report_ids=ids)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.finish()[0]['result'],0)

    def test_challenge_boss_order_and_mid_run_affix_or_roster_changes_reject(self):
        self.open(affixes=[80478]);self.finish()
        before=self.state()
        for stage,team,affixes in [(5310205,[1084,1048],[80478]),(5310204,[1084,1039],[80478]),(5310204,[1084,1048],[80479])]:
            self.assertNotEqual(self.open(stage,team,affixes)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.open(5310204,[1048,1084],[80478])[0]['result'],0)
        self.assertEqual([h['hero_id'] for h in challenge.read(self.db,UID)['heroes']],[1048,1084])

    def test_latest_success_can_restart_from_pre_fight_hp_and_replaces_current_score(self):
        self.open();self.finish({1084:50,1048:40})
        first=challenge.read(self.db,UID)['scores']['5310203']
        self.open();s=challenge.read(self.db,UID)
        self.assertEqual(s['scores'],{});self.assertEqual(s['heroes'][0]['hero_blood'],1000000)
        self.finish({1084:70,1048:60},milliseconds=600000,hits=99)
        s=challenge.read(self.db,UID);self.assertEqual(len(s['scores']),1)
        self.assertEqual(s['stage_best']['5310203'],first);self.assertEqual(s['scores']['5310203'],200000)

    def test_failed_boss_preserves_partial_score_and_can_restart_with_last_status(self):
        self.open();self.finish();score=challenge.read(self.db,UID)['scores'].copy()
        self.open(5310204);self.finish({1084:0,1048:0},result=2)
        s=challenge.read(self.db,UID);self.assertEqual(s['scores'],score)
        self.assertEqual(self.open(5310204)[0]['result'],0)
        self.assertGreater(challenge.read(self.db,UID)['heroes'][0]['hero_blood'],0)
        self.assertEqual(self.finish()[0]['result'],0)

    def test_same_battle_replay_is_ack_only_and_does_not_add_score_or_heal(self):
        self.open();self.finish();before=self.state()
        packet,response=self.call(54032,dict(battle_id=self.bid),54033)
        self.assertEqual(packet['result'],0);self.assertNotIn(89421,response.scs);self.assertEqual(self.state(),before)

    def test_full_four_boss_clear_floor_exceeds_every_configured_score_reward(self):
        self.run_all();s=challenge.read(self.db,UID)
        self.assertEqual(sum(s['scores'].values()),800000)
        self.assertGreaterEqual(sum(s['scores'].values()),max(normal.CFG['scoring']['reward_thresholds']))
        packet,response=self.settle_run();self.assertEqual(packet['result'],0);self.assertNotIn(89421,response.scs)
        s=challenge.read(self.db,UID);self.assertEqual(s['best_score'],800000);self.assertEqual(s['scores'],{})
        for tid in (40700015,40700016,150477):self.assertEqual(self.value(tid),1)

    def test_native_efficiency_and_four_component_private_scoring_are_bounded(self):
        a,parts=challenge.score(dict(battle_time=150000,total_hitted_num=10),[])
        b,_=challenge.score(dict(battle_time=150000,total_hitted_num=10),[80478,80486])
        self.assertEqual(parts,[75000,75000,50000,50000]);self.assertEqual(a,250000);self.assertEqual(b,425000)
        self.assertEqual(challenge.score({},[]),(200000,[50000]*4))
        self.assertEqual(challenge.score(dict(battle_time=1,total_hitted_num=0),[])[0],299999)

    def test_module_count_rewards_require_all_four_bosses_and_survive_giveup_reset(self):
        affixes=list(challenge.AFFIXES)[:6]
        self.open(affixes=affixes);self.finish();self.settle_run()
        self.assertEqual(challenge.read(self.db,UID)['best_affixes'],0);self.assertEqual(self.value(40700017),0)
        self.run_all(affixes);self.assertEqual(challenge.read(self.db,UID)['best_affixes'],6)
        for tid in (40700017,40700018,150476):self.assertEqual(self.value(tid),1)
        self.settle_run();self.assertEqual(challenge.read(self.db,UID)['best_affixes'],6)

    def test_settlement_preserves_records_clears_run_and_rejects_stale_battle(self):
        self.open();self.finish();self.open(5310204);old=self.bid
        self.settle_run();s=challenge.read(self.db,UID)
        self.assertFalse(s['active']);self.assertEqual(s['heroes'],[]);self.assertEqual(s['affixes'],[])
        self.assertGreater(s['best_score'],0);self.assertGreater(s['stage_best']['5310203'],0)
        before=self.state();self.bid=old;self.assertNotEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertNotEqual(self.settle_run()[0]['result'],0)
        self.assertEqual(self.open()[0]['result'],0)

    def test_state_task_battle_receipt_and_settlement_ack_failures_roll_back(self):
        self.open();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for name in ['sc_89421','sc_54033']:
            def fail(n,d):
                if n==name and (n!='sc_54033' or d.get('result')==0):raise RuntimeError('isolated challenge packet')
                return encode(n,d)
            with patch.object(ctx,'codec_encode',side_effect=fail):self.assertNotEqual(self.finish()[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.finish();before=self.state()
        for name in ['sc_28007','sc_89425']:
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==name and (n!='sc_89425' or d.get('result')==0) else encode(n,d)):
                self.assertNotEqual(self.settle_run()[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.settle_run()[0]['result'],0)

    def test_late_score_and_affix_tasks_restore_on_cold_login_without_reopening_claimed(self):
        self.run_all(list(challenge.AFFIXES)[:6]);self.settle_run()
        self.db.execute('UPDATE task SET progress=0 WHERE task_id IN (40700015,40700017,150477)')
        self.db.execute('UPDATE task SET complete_flag=1 WHERE task_id=150477')
        normal.restore_for_login(self.db,UID)
        self.assertEqual(self.value(40700015),1);self.assertEqual(self.value(40700017),1);self.assertEqual(self.value(150477),0)

    def test_normal_battle_also_saves_report_hp_and_preserves_trial_identity(self):
        packet,response=self.call(54030,dict(common_info=dict(type=92,dest=5310220,activity_id=3941101,
            hero_list=[dict(hero_id=236,hero_type=2)])),54031)
        self.assertEqual(packet['result'],0);self.bid=self.conn.battle_id[0]
        self.assertEqual(self.finish({236:0},report_ids={236:1137})[0]['result'],0)
        self.assertEqual(normal.read(self.db,UID)[1]['heroes'],[dict(hero_id=236,hero_blood=0,hero_max_blood=100)])
        self.reset();self.assertEqual(normal.read(self.db,UID)[1]['heroes'],[])


def load_tests(loader,tests,pattern):
    # The inherited ordinary scenarios are run separately once as regression.
    return unittest.TestSuite(ChallengeTests(n) for n in ChallengeTests.__dict__ if n.startswith('test_'))


if __name__=='__main__':unittest.main(verbosity=2)
