"""Issue 8 integration: current Core/codec/Inventory, SQLite memory only."""
import copy
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
import account_db
import codec
import core
import generator
import hero_codec
import middleware as mw
import operations
import rogueteam_service as rogue
import rogueteam_flow as flow


class Issue8Tests(MemoryTests):
    def setUp(self):
        super().setUp()
        frozen=patch('time.time',return_value=NOW);frozen.start();self.addCleanup(frozen.stop)
        self.svc=rogue.RogueTeamService.get(db=self.db)
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        for iid in (1,2,4):self.db.set_currency(UID,iid,1000)
        self.db.upsert('rogueteam_user',UID,dict(template_id=100001,max_difficult=4),keys=('uid','template_id'))
        for iid in (70,71):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,));self.db.set_currency(UID,iid,0)
        self.start()

    def start(self):
        result,_=self.svc.start_run(UID,100001,1,[dict(hero_id=1084)],[])
        self.assertEqual(result,0)

    def saved(self):return self.db.get('rogueteam_session',UID,'AND template_id=?',(100001,))
    def set(self,**kw):self.db.upsert('rogueteam_session',UID,dict(template_id=100001,**kw),keys=('uid','template_id'))
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('rogueteam_user','rogueteam_session','rogueteam_history','rogueteam_illustrated','currency','material')}
    def terminal(self,ending=6):
        self.set(floor_num=3,floor_state=1,pending_ending=ending,ending_won=1,
                 map_info_json=json.dumps([dict(node_id=307,col=7,row=1,node_type=4,state=3,param=4103304,next_id_list=[])]))
        meta=flow.flow(self.saved());meta['phase']='ending_plot';flow.save_flow(self.svc,UID,100001,meta)
    def simple_map(self,node_type=1):
        nodes=[dict(node_id=101,col=1,row=1,node_type=node_type,state=0,param=4101101 if node_type==1 else 30010,next_id_list=[201]),
               dict(node_id=201,col=2,row=1,node_type=1,state=2,param=4101102,next_id_list=[])]
        self.set(map_info_json=json.dumps(nodes),select_node_id=0,other_info_json='{}',shop_info_json='{}')
        return nodes

    def test_real_ending_ids_and_direct_settlement_report_after_plot(self):
        self.assertEqual(rogue.RogueTeamConst.ENDING_OPTION_TO_ID[600104],6)
        self.terminal();packet,response=self.call(88030,dict(type=0),88031)
        story=next(codec.decode(f['payload'],'sc_88217') for f in response.frames if f['sc']==88217)
        self.assertEqual(story,dict(id=6,state=0))
        self.assertEqual(self.call(88218,{},88219)[0]['result'],0)
        packet,response=self.call(88306,dict(template_id=0),88307)
        self.assertEqual(packet['result'],0);self.assertIn(88307,response.scs)
        self.assertEqual(flow.flow(self.saved())['report']['ending_id'],6)
        self.assertNotIn(88217,response.scs)

    def test_no_award_before_plot_confirm_and_repeat_report_is_idempotent(self):
        self.terminal();before=self.state()
        self.assertNotEqual(self.call(88306,{},88307)[0]['result'],0);self.assertEqual(self.state(),before)
        self.call(88218,{},88219);packet,response=self.call(88306,{},88307)
        self.assertEqual(packet['result'],0);before=self.state();report=packet
        packet,response=self.call(88306,{},88307)
        self.assertEqual(packet,report);self.assertEqual(self.state(),before);self.assertNotIn(17023,response.scs)

    def test_reward_coefficients_follow_lua_without_minimum_gift(self):
        self.assertEqual(self.svc.config.cfg['reward_coefficients'],dict(survey=6,research=1))
        self.terminal();self.call(88218,{},88219);self.call(88306,{},88307)
        report=flow.flow(self.saved())['report'];score=next(v['value'] for v in report['end_info_list'] if v['key']==101)
        self.assertEqual(report['survey_item_gain'],score*6//1000);self.assertEqual(report['research_item_gain'],score//1000)
        self.assertEqual(self.balance(70),score*6//1000);self.assertEqual(self.balance(71),score//1000)

    def test_required_report_or_currency_failure_rolls_back_and_retry(self):
        self.terminal();self.call(88218,{},88219);before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_88307' else encode(n,d)):
            self.assertNotEqual(self.call(88306,{},88307)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(88306,{},88307)[0]['result'],0)

    def test_same_uid_separate_databases_and_services_are_isolated(self):
        second=account_db.AccountDB.new_save_workspace();self.addCleanup(second.close)
        other=rogue.RogueTeamService.get(db=second)
        self.assertIsNot(other,self.svc);self.assertIs(self.svc._db(),self.db);self.assertIs(other._db(),second)
        self.terminal();self.call(88218,{},88219);self.call(88306,{},88307)
        self.assertEqual(other.settle_run(UID)[0],2)
        self.assertFalse(second.query('SELECT * FROM rogueteam_session'))

    def test_shop_price_uses_rarity_rate_and_two_ceilings(self):
        cfg=self.svc.config.get_item(1410101)
        self.assertEqual(flow.shop_price(cfg,dict(rare=3,discount=100),[dict(attr_id=11,value=1500)]),450)
        self.assertEqual(flow.shop_price(cfg,dict(rare=3,discount=67),[dict(attr_id=11,value=1001)]),202)

    def test_invalid_event_reward_and_floor_requests_do_not_mutate(self):
        self.simple_map(7);before=self.state()
        self.assertNotEqual(self.svc.choose_event_option(UID,500111)[0],0)
        self.assertNotEqual(self.svc.choose_event_option(UID,0)[0],0)
        self.assertNotEqual(self.svc.next_floor(UID,1)[0],0)
        self.assertNotEqual(self.svc.next_floor(UID,2)[0],0);self.assertEqual(self.state(),before)
        self.set(other_info_json=json.dumps(dict(event_type=11,param_list=[dict(index=1,param=1210001)])))
        before=self.state();self.assertNotEqual(self.svc.commit_event_selection(UID,1,1)[0],0);self.assertEqual(self.state(),before)

    def test_restore_event_uses_event_packet_and_ending_state_persists(self):
        event=dict(event_id=30010,opt_list=[dict(opt_id=300101)],trigger_type=1)
        self.set(other_info_json=json.dumps(event));packet,response=self.call(88030,dict(type=0),88031)
        restored=next(codec.decode(f['payload'],'sc_88023') for f in response.frames if f['sc']==88023)
        self.assertEqual(restored['event_id'],30010)
        self.terminal();self.set(other_info_json='{}');self.call(88218,{},88219)
        packet,response=self.call(88030,dict(type=0),88031)
        restored=next(codec.decode(f['payload'],'sc_88217') for f in response.frames if f['sc']==88217)
        self.assertEqual(restored['state'],1)

    def test_double_start_and_off_path_selection_are_rejected(self):
        before=self.state();self.assertNotEqual(self.svc.start_run(UID,100001,1,[dict(hero_id=1084)],[])[0],0);self.assertEqual(self.state(),before)
        self.simple_map();before=self.state();self.assertNotEqual(self.svc.select_node(UID,201)[0],0);self.assertEqual(self.state(),before)

    def test_author_fields_exist_in_new_generator_workspace(self):
        columns={r['name'] for r in self.db.query('PRAGMA table_info(rogueteam_session)')}
        self.assertTrue({'pending_ending','flow_json','mech_total','run_start_ts','ending_won','expired_items_json'}<=columns)
        self.assertNotIn('hero_maxhp_json',{r['name'] for r in self.db.query('PRAGMA table_info(rogueteam_user)')})

    def test_mechanism_overflow_dead_hero_and_correct_revive_attribute(self):
        self.simple_map();self.svc.select_node(UID,101)
        attrs={a['attr_id']:a['value'] for a in json.loads(self.saved()['attr_list_json'])}
        attrs.update({1:90,22:100,4:0,5:1,7:2})
        self.set(attr_list_json=json.dumps([dict(attr_id=k,value=v) for k,v in attrs.items()]),
                 other_item_list_json='[1310015]',hero_list_json='[{"hero_id":1084,"temp_id":0,"hp_ratio":0}]')
        with patch('random.randint',return_value=13):
            self.assertEqual(self.svc.settle_battle_node(UID,101,win=True,battle_info=dict(injured_num=100,resurrect_times=0))[0],0)
        after={a['attr_id']:a['value'] for a in json.loads(self.saved()['attr_list_json'])}
        self.assertEqual(after[1],3);self.assertEqual(after[4],1);self.assertEqual(after[7],2)
        self.assertEqual(json.loads(self.saved()['hero_list_json'])[0]['hp_ratio'],0)

    def test_real_battle_pipeline_carries_run_identity_and_rewards_once(self):
        self.simple_map();self.svc.select_node(UID,101)
        packet,response=self.call(54030,dict(common_info=dict(type=78,dest=101,activity_id=1100,
             hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        self.assertEqual(packet['result'],0)
        bid=self.conn.battle_id[0];session=mw.get_battle_session(bid)
        self.assertEqual(session.get('rogue_run_id'),flow.flow(self.saved())['run_id'])
        session['result_data']=dict(result=1,info=dict(battle_time=45000))
        packet,response=self.call(54032,dict(battle_id=bid),54033)
        self.assertEqual(packet['result'],0);self.assertIn(88033,response.scs);self.assertIn(88001,response.scs)
        before=self.state();self.call(54032,dict(battle_id=bid),54033);self.assertEqual(self.state(),before)

    def test_assignment_phase_first_incomplete_instead_of_later_completion_count(self):
        rows=[dict(task_id=i,complete_flag=1) for i in (30602001,30602009,30602013,30602017)]
        self.assertEqual(flow.assignment_phase(rows),2)

    def test_terminal_battle_then_floor_plot_and_report_native_order(self):
        self.set(floor_num=3,pending_ending=6,map_info_json=json.dumps([
            dict(node_id=307,col=7,row=1,node_type=4,state=0,param=4103304,next_id_list=[])]))
        self.assertEqual(self.svc.select_node(UID,307)[0],0)
        self.assertEqual(self.call(54030,dict(common_info=dict(type=78,dest=307,activity_id=1100,
            hero_list=[dict(hero_id=1084,hero_type=1)])),54031)[0]['result'],0)
        bid=self.conn.battle_id[0]
        mw.get_battle_session(bid)['result_data']=dict(result=1,info=dict(battle_time=45000))
        packet,response=self.call(54032,dict(battle_id=bid),54033)
        self.assertEqual(packet['result'],0);self.assertIn(88221,response.scs)
        packet,response=self.call(88008,dict(sign=1),88009)
        self.assertEqual(packet['result'],0);self.assertIn(88217,response.scs)
        self.assertEqual(self.call(88218,{},88219)[0]['result'],0)
        packet,response=self.call(88306,{},88307)
        self.assertEqual(packet['result'],0);self.assertIn(88305,response.scs)
        outside=next(codec.decode(f['payload'],'sc_88305') for f in response.frames if f['sc']==88305)
        self.assertEqual(outside['his_avg'],[dict(key=6,value=1)])

    def test_currency_grant_write_failure_rolls_back_settlement_and_retries(self):
        self.terminal();self.call(88218,{},88219);before=self.state();add=operations._item_add
        def fail_after_first(ctx,uid,item,amount,**kw):
            result=add(ctx,uid,item,amount,**kw)
            if item==70:raise RuntimeError('isolated grant write failure')
            return result
        with patch.object(operations,'_item_add',side_effect=fail_after_first):
            self.assertNotEqual(self.call(88306,{},88307)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.call(88306,{},88307)[0]['result'],0)

    def test_shop_purchase_deducts_display_price_and_keeps_offer_rarity(self):
        self.simple_map(5);self.svc.select_node(UID,101)
        attrs={a['attr_id']:a['value'] for a in json.loads(self.saved()['attr_list_json'])}
        attrs.update({6:1000,11:1500})
        self.set(attr_list_json=json.dumps([dict(attr_id=k,value=v) for k,v in attrs.items()]),
            shop_info_json=json.dumps(dict(event_type=7,param_list=[dict(index=1,param=1410101,rare=3,discount=100,buy_times=1)])))
        self.assertEqual(self.call(88222,dict(index=1),88223)[0]['result'],0)
        after={a['attr_id']:a['value'] for a in json.loads(self.saved()['attr_list_json'])}
        self.assertEqual(after[6],550)
        self.assertIn(dict(id=1410101,rare=3),json.loads(self.saved()['treasure_list_json']))
        before=self.state();self.assertNotEqual(self.call(88222,dict(index=1),88223)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_old_battle_cannot_settle_into_restarted_run(self):
        self.simple_map();self.svc.select_node(UID,101)
        self.call(54030,dict(common_info=dict(type=78,dest=101,activity_id=1100,
            hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        bid=self.conn.battle_id[0]
        self.svc.settle_run(UID);self.start();before=self.state()
        mw.get_battle_session(bid)['result_data']=dict(result=1,info=dict(battle_time=45000))
        packet,response=self.call(54032,dict(battle_id=bid),54033)
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main(verbosity=2)
