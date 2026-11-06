"""Native codec + memory AccountDB/Core checks; never load a real save."""
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID
import hero_codec
import middleware as mw
import codec
import generator
from types import SimpleNamespace
from polyhedron_service import PolyhedronRunManager as M

class PolyhedronTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        self.db.upsert('polyhedron_hero',UID,dict(hero_id=1084),keys=('uid','hero_id'))
        self.assertEqual(self.call(18010,dict(hero_id_list=[1084],difficulty=1),18011)[0]['result'],0)
    def saved(self):return M.load_run(UID,self.db)
    def state(self):return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('polyhedron_run','polyhedron_terminal','polyhedron_meta','polyhedron_artifact','polyhedron_beacon','polyhedron_hero','polyhedron_policy_claimed','currency','material','ops_common','task','hero','claim_ledger')}
    def enter(self):
        return self.call(54030,dict(common_info=dict(type=52,dest=self.saved()['current_stage']['stage_id'],activity_id=4307301,battle_times=1)),54031)
    def finish(self,result=1):
        bid=self.conn.battle_id[0];mw.get_battle_session(bid)['result_data']=dict(result=result,info=dict(battle_time=10000,enemy_dead_num=1))
        return self.call(54032,dict(battle_id=bid),54033)
    def test_native_path_and_ack_after_snapshot(self):
        self.assertEqual(self.enter()[0]['result'],0)
        self.assertEqual(self.finish()[0]['result'],0)
        before=self.saved()['coins']
        packet,response=self.call(18012,dict(index=1),18013)
        self.assertEqual(packet['result'],0);self.assertLess(response.scs.index(18001),response.scs.index(18013))
        self.assertEqual(self.saved()['coins'],before)
        selected=self.saved()['current_stage']['gate_list'][0]['event']['stage_id']
        self.assertEqual(self.call(18014,dict(index=1),18015)[0]['result'],0)
        self.assertEqual(self.saved()['current_stage']['stage_id'],selected)
    def test_empty_or_invalid_reward_never_adds_coin(self):
        before=self.state()
        for i in (0,1,99):self.assertNotEqual(self.call(18012,dict(index=i),18013)[0]['result'],0)
        self.assertEqual(self.state(),before)
    def test_invalid_gate_and_shop_do_not_mutate(self):
        before=self.state()
        for cmd,data in ((18014,dict(index=99)),(18022,dict(index=1)),(18024,{}),(18026,{})):
            self.assertNotEqual(self.call(cmd,data,cmd+1)[0]['result'],0)
        self.assertEqual(self.state(),before)
    def test_terminal_empty_is_authoritative(self):
        from weekly_challenge_service import get_polyhedron_data
        self.assertEqual(get_polyhedron_data(UID,self.db)['terminal']['upgrade_id_list'],[])
    def test_settlement_no_run_rejected_and_repeat_no_asset_gain(self):
        packet,_=self.call(18018,{},18019);self.assertEqual(packet['result'],0)
        before=self.state();self.assertEqual(self.call(18018,{},18019)[0]['result'],0);self.assertEqual(self.state(),before)
        self.call(18016,{},18017);before=self.state()
        self.assertNotEqual(self.call(18018,{},18019)[0]['result'],0);self.assertEqual(self.state(),before)
    def test_start_snapshot_or_ack_failure_rolls_back(self):
        self.call(18016,{},18017);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for name in ('sc_18001','sc_18011'):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n==name else encode(n,*a,**k)):
                self.assertNotEqual(self.call(18010,dict(hero_id_list=[1084],difficulty=1),18011)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def alter(self,**values):
        run=self.saved();run.update(values);M.save_run(UID,self.db,run);return run

    def stage(self,kind,point=1,params=None,coins=2000):
        stage_id={2:3035401,5:3035601,1:3035105}[kind]
        run=self.saved();run.update(coins=coins,latest=None,receipt=None)
        run['current_event']=dict(id=kind*1000+1,stage_id=stage_id,reward_type=kind)
        run['current_stage']=dict(stage_id=stage_id,save_point=point,reward=dict(round=0,item_list=[]),
            params=params or [],gate_list=[dict(index=7,event=dict(id=1001,stage_id=3035106,reward_type=3001))],
            shop=M._generate_shop_data(run) if kind==5 else None,attribute_modify_list=[])
        M.save_run(UID,self.db,run)

    def test_unowned_recruit_uses_native_standard_without_account_grant(self):
        self.stage(2,params=[1011,2]);self.enter()
        self.assertEqual(self.call(18020,dict(hero_id=1011),18021)[0]['result'],0)
        self.assertEqual(self.call(18014,dict(index=7),18015)[0]['result'],0)
        packet,response=self.enter();self.assertEqual(packet['result'],0)
        # The shared codec aliases player_info to a skin struct; validate the
        # actual p54 field contract instead of that unrelated schema alias.
        from battle_server import _character_fields as fields
        raw=next(f['payload'] for f in response.frames if f['sc']==54003)
        player=next(v for f,w,v in fields(raw) if f==1)
        battle=next(v for f,w,v in fields(player) if f==2)
        heroes=[dict((f,v) for f,w,v in fields(h)) for key,wire,h in fields(battle) if key==4]
        self.assertEqual({dict((f,v) for f,w,v in fields(h[1]))[1] for h in heroes},{3051084,3051011})
        self.assertTrue(all(h[2]==2 for h in heroes))
        self.assertEqual(self.db.query('SELECT 1 FROM hero WHERE uid=? AND id=1011',(UID,)),[])

    def test_recruit_funds_candidate_consumption_and_ack_rollback(self):
        self.stage(2,params=[1011,2],coins=799);self.enter();before=self.state()
        self.assertNotEqual(self.call(18020,dict(hero_id=1011),18021)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.alter(coins=800);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n=='sc_18021' else encode(n,*a,**k)):
            self.assertNotEqual(self.call(18020,dict(hero_id=1011),18021)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(18020,dict(hero_id=1011),18021)[0]['result'],0)
        self.assertEqual(self.saved()['coins'],0);before=self.state()
        self.assertNotEqual(self.call(18020,dict(hero_id=1011),18021)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_shop_report_is_not_a_kill_and_gate_uses_wire_index(self):
        self.stage(5,3);self.enter();before=self.saved()
        self.assertEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.saved()['coins'],before['coins'])
        self.assertEqual(self.saved()['stats']['battle_count'],0)
        self.assertNotEqual(self.call(18014,dict(index=1),18015)[0]['result'],0)
        self.assertEqual(self.call(18014,dict(index=7),18015)[0]['result'],0)
        self.assertEqual(self.saved()['current_stage']['stage_id'],3035106)

    def test_scene_restore_reward_and_option_do_not_repeat_combat(self):
        self.enter();self.finish();coins=self.saved()['coins'];items=self.saved()['current_stage']['reward']
        self.enter();self.assertEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.saved()['coins'],coins);self.assertEqual(self.saved()['current_stage']['reward'],items)
        self.enter();self.assertEqual(self.call(18012,dict(index=1),18013)[0]['result'],0)
        self.enter();self.assertEqual(self.finish()[0]['result'],0);self.assertEqual(self.saved()['coins'],coins)

    def test_failed_or_quit_battle_does_not_advance(self):
        for outcome in (2,3):
            self.enter();before=self.saved();self.assertEqual(self.finish(outcome)[0]['result'],0)
            after=self.saved()
            self.assertEqual(after['coins'],before['coins']);self.assertEqual(after['current_stage'],before['current_stage'])
            self.assertEqual(after['stats']['battle_count'],0)

    def test_repeated_bid_and_replaced_run_are_isolated(self):
        self.enter();old=self.conn.battle_id[0];self.finish();before=self.state()
        self.assertEqual(self.call(54032,dict(battle_id=old),54033)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.call(18016,{},18017);self.call(18010,dict(hero_id_list=[1084],difficulty=1),18011)
        self.assertNotEqual(self.call(54032,dict(battle_id=old),54033)[0]['result'],0)
        self.enter();unsettled=self.conn.battle_id[0]
        self.call(18016,{},18017);self.call(18010,dict(hero_id_list=[1084],difficulty=1),18011);before=self.state()
        self.assertNotEqual(self.call(54032,dict(battle_id=unsettled),54033)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_same_uid_other_database_cannot_consume_battle(self):
        from account_db import AccountDB
        import core
        self.enter();bid=self.conn.battle_id[0]
        other=AccountDB.new_save_workspace();self.addCleanup(other.close)
        conn=core.Connection(uid=UID,db=other,generator=generator,codec_encode=codec.encode,codec_decode=codec.decode,log=lambda *a:None)
        engine=core.Core(db=other,generator=generator,codec_encode=codec.encode,codec_decode=codec.decode,log=lambda *a:None)
        response=engine.dispatch(core.CoreRequest(54032,codec.encode('cs_54032',dict(battle_id=bid)),uid=UID),conn)
        packet=codec.decode(next(f['payload'] for f in response.frames if f['sc']==54033),'sc_54033')
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.saved()['stats']['battle_count'],0)

    def test_battle_snapshot_and_ack_failure_roll_back_without_cached_success(self):
        self.enter();bid=self.conn.battle_id[0];before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for name in ('sc_18001','sc_54033'):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n==name else encode(n,*a,**k)):
                self.assertNotEqual(self.finish()[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(bid))
        self.assertEqual(self.finish()[0]['result'],0)

    def test_shop_stock_and_funds(self):
        self.stage(5,3,coins=0);before=self.state()
        self.assertNotEqual(self.call(18022,dict(index=1),18023)[0]['result'],0);self.assertEqual(self.state(),before)
        self.alter(coins=2000);self.assertEqual(self.call(18022,dict(index=1),18023)[0]['result'],0);before=self.state()
        self.assertNotEqual(self.call(18022,dict(index=1),18023)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_final_reward_atomic_receipt_and_cold_recovery(self):
        self.enter();self.finish();run=self.alter(tier_id=3010,cleared_tiers=[3010]);before=self.state()
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        for name in ('sc_18005','sc_18013'):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n==name else encode(n,*a,**k)):
                self.assertNotEqual(self.call(18012,dict(index=1),18013)[0]['result'],0)
            self.assertEqual(self.state(),before)
        packet,response=self.call(18012,dict(index=1),18013);self.assertEqual(packet['result'],0)
        self.assertTrue(all(response.scs.index(cmd)<response.scs.index(18013) for cmd in (18005,17023,18001)))
        self.assertEqual(self.db.query('SELECT difficulty_id FROM polyhedron_difficulty WHERE uid=?',(UID,)),[dict(difficulty_id=1)])
        self.assert_cold_receipt(self.saved()['end_info'])

    def assert_cold_receipt(self,expected):
        from polyhedron_login import prepare_login_frames
        rows=[(0,cmd,b'',b'captured',0,None) for cmd in (18001,18003,18005,18001,10201)]
        live,payloads=prepare_login_frames(rows,self.db,UID,generator,SimpleNamespace(allows=lambda c:True))
        self.assertEqual([r[1] for r in live],[18001,18005,10201])
        self.assertEqual(codec.decode(payloads[18005],'sc_18005')['end_info'],expected)

    def test_direct_final_battle_sends_receipt_before_ack(self):
        self.stage(1);self.alter(tier_id=3010);self.enter();packet,response=self.finish()
        self.assertEqual(packet['result'],0)
        self.assertTrue(all(response.scs.index(cmd)<response.scs.index(54033) for cmd in (18005,17023,18001)))

    def test_legacy_settlement_does_not_regrant_unknown_history(self):
        run=self.saved();run.pop('settlement_version');run.pop('run_id');run['state']=3;M.save_run(UID,self.db,run)
        balances=[self.balance(i) for i in (45,46,29)]
        self.assertEqual(self.call(18018,{},18019)[0]['result'],0)
        self.assertEqual([self.balance(i) for i in (45,46,29)],balances)
        self.assert_cold_receipt(dict(point=0,decision_exp=0,terminal_exp=0))

    def test_old_active_reward_migrates_before_becoming_terminal(self):
        self.enter();self.finish();run=self.saved();run.pop('settlement_version');run.pop('run_id')
        run.update(tier_id=3010,cleared_tiers=[3010]);M.save_run(UID,self.db,run)
        self.assertEqual(self.call(18012,dict(index=1),18013)[0]['result'],0)
        self.assertTrue(self.saved()['settled']);self.assertGreater(self.balance(45),0)
        self.assertFalse(self.saved().get('legacy_settlement_unknown',False))

    def test_abandon_disconnected_scene_invalidates_pending_battle(self):
        self.enter();bid=self.conn.battle_id[0];self.assertEqual(self.call(18018,{},18019)[0]['result'],0);before=self.state()
        self.assertNotEqual(self.call(54032,dict(battle_id=bid),54033)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_native_difficulty_and_terminal_connectivity(self):
        self.call(18016,{},18017);before=self.state()
        self.assertNotEqual(self.call(18010,dict(hero_id_list=[1084],difficulty=6),18011)[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.set_currency(UID,46,1000000)
        self.assertNotEqual(self.call(18032,dict(upgrade_id_list=[1002,1004]),18033)[0]['result'],0)
        self.assertEqual(self.call(18032,dict(upgrade_id_list=[1001,1002]),18033)[0]['result'],0)
        self.assertNotEqual(self.call(18032,dict(upgrade_id_list=[]),18033)[0]['result'],0)
        self.assertEqual(self.call(66002,{},66003)[0]['result'],0)
        self.assertEqual(self.db.query('SELECT terminal_id FROM polyhedron_terminal WHERE uid=?',(UID,)),[])
        self.assertNotEqual(self.call(66002,{},66003)[0]['result'],0)

    def test_measured_hp_only_and_no_synthetic_damage(self):
        self.enter();bid=self.conn.battle_id[0]
        mw.get_battle_session(bid)['result_data']=dict(result=1,info=dict(characters=[dict(hero_id=1084,hero_blood=4000,hero_max_blood=10000)]))
        self.assertEqual(self.call(54032,dict(battle_id=bid),54033)[0]['result'],0)
        hero=self.saved()['hero_list'][0]
        self.assertEqual((hero['health'],hero['max_health'],hero['damage'],hero['heal'],hero['injured']),(4000,10000,0,0,0))

    def test_account_unlock_and_astrolabe_guards(self):
        self.db.set_currency(UID,44,2);before=self.state()
        for cmd,data in ((66036,dict(hero_id=9999)),(66036,dict(hero_id=1011)),
                         (66004,dict(hero_id=1011,astrolabe_id_list=[])),
                         (66004,dict(hero_id=1084,astrolabe_id_list=[10110011])),
                         (66004,dict(hero_id=1084,astrolabe_id_list=[10840012]))):
            self.assertNotEqual(self.call(cmd,data,cmd+1)[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1011,100),keys=('uid','id'))
        self.assertEqual(self.call(66036,dict(hero_id=1011),66037)[0]['result'],0)
        self.assertEqual(self.balance(44),1)
        self.assertEqual(self.call(66036,dict(hero_id=1011),66037)[0]['result'],0)
        self.assertEqual(self.balance(44),1)
        self.assertEqual(self.call(66004,dict(hero_id=1084,astrolabe_id_list=[10840011,10840012]),66005)[0]['result'],0)

    def test_unlock_ack_failure_refunds_mapping_item(self):
        self.db.set_currency(UID,44,1)
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1011,100),keys=('uid','id'))
        before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n=='sc_66037' else encode(n,*a,**k)):
            self.assertNotEqual(self.call(66036,dict(hero_id=1011),66037)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_beacon_native_conditions_and_ack_rollback(self):
        before=self.state()
        for beacon in (2,9999):
            self.assertNotEqual(self.call(66034,dict(beacon_id=beacon),66035)[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.set_currency(UID,46,2400)
        self.assertEqual(self.call(66034,dict(beacon_id=2),66035)[0]['result'],0)
        from polyhedron_account import add_progress
        add_progress(self.db,UID,kills=50)
        self.assertEqual(self.call(66034,dict(beacon_id=11),66035)[0]['result'],0)

    def test_combo_is_persisted_and_checks_current_team(self):
        self.assertNotEqual(self.call(66038,dict(cooperate_unique_skill_id=2),66039)[0]['result'],0)
        self.stage(2,params=[1011,3]);self.call(18020,dict(hero_id=1011),18021)
        self.assertEqual(self.call(66038,dict(cooperate_unique_skill_id=2),66039)[0]['result'],0)
        self.assertEqual(self.saved()['cooperate_unique_skill_id'],2)
        self.assertNotEqual(self.call(66038,dict(cooperate_unique_skill_id=999999),66039)[0]['result'],0)

    def test_policy_reward_ack_rollback_and_cross_activity_claim_ledger(self):
        self.db.set_currency(UID,45,60);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n=='sc_66001' else encode(n,*a,**k)):
            self.assertNotEqual(self.call(66000,dict(type=2,level=1),66001)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(66000,dict(type=2,level=1),66001)[0]['result'],0);balance=self.balance(28)
        self.db.execute('UPDATE polyhedron_policy_claimed SET activity_id=4407301 WHERE uid=?',(UID,))
        self.assertEqual(self.call(66000,dict(type=2,level=1),66001)[0]['result'],0)
        self.assertEqual(self.balance(28),balance)

    def test_required_asset_frame_failure_rolls_back_settlement(self):
        before=self.state();real=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else real(cmd,*a,**kw)):
            self.assertNotEqual(self.call(18018,{},18019)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_entry_frame_failure_does_not_publish_attempt(self):
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;before=self.state()
        for name in ('sc_18001','sc_54007'):
            with patch.object(ctx,'codec_encode',side_effect=lambda n,*a,**k:None if n==name else encode(n,*a,**k)):
                self.assertNotEqual(self.enter()[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_fresh_save_uses_polyhedron_eligibility_not_story_stage_gate(self):
        with patch('new_player_service.enabled',return_value=True),patch('new_player_service.stage_available',return_value=False):
            self.assertEqual(self.enter()[0]['result'],0)

    def test_invalid_hp_and_wrong_stage_or_type_are_rejected(self):
        for kind,stage in ((1,self.saved()['current_stage']['stage_id']),(52,3037301)):
            self.assertNotEqual(self.call(54030,dict(common_info=dict(type=kind,dest=stage,battle_times=1)),54031)[0]['result'],0)
        self.enter();bid=self.conn.battle_id[0];before=self.state()
        for characters in (None,[dict(hero_id=1011,hero_blood=1,hero_max_blood=10000)]):
            mw.get_battle_session(bid)['result_data']=dict(result=1,info=dict(characters=characters))
            self.assertNotEqual(self.call(54032,dict(battle_id=bid),54033)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(bid))

    def test_reroll_limit_and_world_battle_reward_isolation(self):
        world={t:self.db.query('SELECT * FROM '+t) for t in ('currency','hero','chapter','material')}
        self.enter();self.finish()
        self.assertEqual({t:self.db.query('SELECT * FROM '+t) for t in world},world)
        self.alter(rolls=1)
        self.assertEqual(self.call(18030,{},18031)[0]['result'],0)
        self.assertEqual(self.saved()['rolls'],0);before=self.state()
        self.assertNotEqual(self.call(18030,{},18031)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_policy_ineligible_single_claim_is_not_acknowledged(self):
        before=self.state()
        for data in (dict(type=0),dict(type=2,level=1),dict(type=2,level=99)):
            self.assertNotEqual(self.call(66000,data,66001)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_terminal_reset_count_follows_week_without_activity_bypass(self):
        self.db.set_currency(UID,46,600)
        self.assertEqual(self.call(18032,dict(upgrade_id_list=[1001]),18033)[0]['result'],0)
        self.assertEqual(self.call(66002,{},66003)[0]['result'],0)
        baseline=M.get_policy_cycle_id()
        with patch('polyhedron_service.time.time',return_value=baseline+1):
            M.check_and_refresh_policy_cycle(UID,self.db,activity_id=987654)
        self.assertTrue(all(r['reset_times']==0 for r in self.db.query('SELECT reset_times FROM polyhedron_meta WHERE uid=?',(UID,))))
        with patch('polyhedron_service.time.time',return_value=baseline+604800):
            M.check_and_refresh_policy_cycle(UID,self.db,activity_id=987654)
        self.assertTrue(all(r['reset_times']==1 for r in self.db.query('SELECT reset_times FROM polyhedron_meta WHERE uid=?',(UID,))))

if __name__=='__main__':unittest.main()
