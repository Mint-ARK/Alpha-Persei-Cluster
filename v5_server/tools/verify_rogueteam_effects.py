"""Five native timed items: actual memory AccountDB, Core and codec."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from verify_rogueteam_transition import TransitionTests
from verify_rogueteam_battle import RogueBattleTests
from verify_rogueteam_login import RogueLoginTests
import codec
import hero_codec
from rogueteam_service import RogueTeamService
import rogueteam_flow as flow
import rogueteam_effects as effects


class EffectsTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = RogueTeamService.get(db=self.db)
        for hid in (1084,1050,1039):
            self.db.upsert('hero',UID,hero_codec.initial_hero_row(hid,100),keys=('uid','id'))
        self.assertEqual(self.svc.start_run(UID,100001,1,[dict(hero_id=1084)],[])[0],0)
    saved = TransitionTests.saved
    set = TransitionTests.set
    attrs = TransitionTests.attrs
    state = TransitionTests.state
    op = TransitionTests.op
    ok = TransitionTests.ok
    rejected = TransitionTests.rejected

    def place(self, nid, kind=1, state=0, selected=0, pending=None):
        self.set(map_info_json=json.dumps([
            dict(node_id=nid, col=1, row=1, node_type=kind, state=state,
                 param=4101101 if kind == 1 else 50012, next_id_list=[nid+1]),
            dict(node_id=nid+1, col=2, row=1, node_type=1, state=2, param=4101102, next_id_list=[])]),
            select_node_id=selected, other_info_json=json.dumps(pending or {}), shop_info_json='{}')
        meta=flow.flow(self.saved())
        for key in ('pending_owner','shop_owner'):
            meta.pop(key,None)
        flow.save_flow(self.svc,UID,100001,meta)

    def get_item(self,iid,nid=501):
        self.place(nid,state=1,selected=nid,pending=dict(event_type=2,param_list=[dict(index=1,param=iid,rare=1)],drop_type=1))
        return self.ok(88200,event_id=2,param_arg=1)[1]

    def finish_event(self,nid,kind=7):
        self.place(nid,kind=kind,state=1,selected=nid,pending=dict(event_id=50012,opt_list=[],trigger_type=1))
        return self.ok(88024,opt_id=0)[1]

    def test_contract_retains_native_moments_and_hashes(self):
        from export_rogueteam_effects_contract import export
        self.assertEqual(effects.CONTRACT,export())
        for relative,sha in effects.CONTRACT['source'].items():
            self.assertEqual(hashlib.sha256((Path(__file__).resolve().parents[2]/relative).read_bytes()).hexdigest(),sha)
        self.assertEqual([effects.CONTRACT['effects'][str(e)]['moment'] for e in (13100021,13100031,13100151,13100152,13100161,13100222)], [20,21,1,4,11,10])
        self.assertEqual(effects.CONTRACT['effects']['13100161']['gap'],3)

    def test_tree_acquisition_no_gold_then_entry_once_and_cold_read(self):
        self.attrs(**{'6':100,'20':99999});response=self.get_item(1310002)
        self.assertEqual(effects.attrs(self.saved())[6],100)
        self.assertNotIn(88017,response.scs) # 88001 already contains the snapshot
        self.place(601);_,response=self.ok(88004,node_id=601)
        self.assertEqual(effects.attrs(self.saved())[6],115)
        self.assertIn(88017,response.scs)
        self.ok(88004,node_id=601)
        self.assertEqual(effects.attrs(self.saved())[6],115)
        before=self.state();_,response=self.ok(88030,type=0)
        self.assertEqual(self.state(),before)
        snapshot=next(codec.decode(f['payload'],'sc_88001') for f in response.frames if f['sc']==88001)
        effect=next(x for x in snapshot['effect_list'] if x['effect_id']==13100021)
        self.assertEqual((effect['moment_time'],effect['calc_value']),(1,15))

    def test_shop_two_entry_sources_then_paid_refresh_expires_tree(self):
        self.attrs(**{'6':1000,'20':99999,'30':1});self.get_item(1310002);self.get_item(1310003,511)
        self.assertEqual(effects.attrs(self.saved())[6],1000)
        self.place(701,kind=5);self.ok(88004,node_id=701)
        self.assertEqual(effects.attrs(self.saved())[6],1075)
        _,response=self.ok(88224)
        self.assertEqual(effects.attrs(self.saved())[6],1055)
        self.assertIn(1310002,json.loads(self.saved()['expired_items_json']))
        effect=next(x for f in response.frames if f['sc']==88017 for x in codec.decode(f['payload'],'sc_88017')['effect_list'] if x['effect_id']==13100021)
        self.assertEqual(effect['left_time'],0)
        self.ok(88222,index=0);self.place(711);self.ok(88004,node_id=711)
        self.assertEqual(effects.attrs(self.saved())[6],1055)

    def test_energy_cap_is_assignment_and_win_refills_once(self):
        self.attrs(**{'4':3,'5':3,'20':99999});self.get_item(1310015)
        self.assertEqual((effects.attrs(self.saved())[4],effects.attrs(self.saved())[5]),(1,1))
        self.attrs(**{'4':0});self.place(801);self.ok(88004,node_id=801)
        self.assertEqual(effects.attrs(self.saved())[4],0)
        result=self.svc.settle_battle_node(UID,801,win=True,battle_info={'resurrect_times':1})
        self.assertEqual(result[0],0);self.assertEqual(effects.attrs(self.saved())[4],1)
        self.svc.settle_battle_node(UID,801,win=True)
        self.assertEqual(flow.flow(self.saved())['room_effects_v1']['items']['1310015']['moment_time'],1)

    def test_energy_exempt_victory_also_refills(self):
        self.attrs(**{'4':0,'5':3,'20':99999});self.get_item(1310015)
        self.place(811);nodes=json.loads(self.saved()['map_info_json']);nodes[0]['param']=0
        self.set(map_info_json=json.dumps(nodes));self.attrs(**{'28':1})
        self.ok(88004,node_id=811)
        self.assertEqual(effects.attrs(self.saved())[4],1)

    def test_photo_uses_three_completed_decisions_not_history_or_safe_room(self):
        self.attrs(**{'20':99999});self.set(used_events_json='[10010,10020,10040]')
        self.get_item(1310016);self.finish_event(901,6)
        self.assertEqual(flow.flow(self.saved())['room_effects_v1']['items']['1310016']['moment_time'],0)
        with patch.object(effects,'roll_relic_gift',return_value=1310003):
            self.finish_event(911);self.finish_event(921)
            self.assertNotIn(1310003,effects.owned(self.saved()))
            response=self.finish_event(931)
        self.assertIn(1310003,effects.owned(self.saved()));self.assertIn(88021,response.scs)
        if 88017 in response.scs:
            self.assertLess(response.scs.index(88021),response.scs.index(88017))
        else:
            self.assertIn(88001,response.scs)
            snapshot=next(codec.decode(f['payload'],'sc_88001') for f in response.frames if f['sc']==88001)
            self.assertTrue(any(x['source_id']==1310003 for x in snapshot['effect_list']))
        self.rejected(88024,opt_id=0)
        self.assertEqual(flow.flow(self.saved())['room_effects_v1']['items']['1310016']['moment_time'],3)

    def test_ascetic_starts_on_acquisition_and_upgrades_after_three_finishes(self):
        self.attrs(**{'6':100,'20':99999});self.set(room_passed_count=11,treasure_list_json=json.dumps([dict(id=1410101,rare=2)]))
        self.get_item(1310022)
        self.assertEqual(effects.attrs(self.saved())[26],1)
        for nid in (1001,1011):self.finish_event(nid)
        self.assertEqual(json.loads(self.saved()['treasure_list_json'])[0]['rare'],2)
        response=self.finish_event(1021)
        self.assertEqual(json.loads(self.saved()['treasure_list_json'])[0]['rare'],3)
        self.assertIn(88021,response.scs);self.assertIn(88205,response.scs)
        self.set(other_item_list_json=json.dumps([dict(id=i,value=1) for i in (1310022,1310002,1310003)]))
        self.place(1031,kind=5);self.ok(88004,node_id=1031)
        self.assertEqual(effects.attrs(self.saved())[6],100)

    def test_effect_encoding_failure_rolls_back_gold_and_receipt(self):
        self.get_item(1310002);self.place(1101);before=self.state()
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_88017' else encode(n,d)):
            self.rejected(88004,node_id=1101)
        self.assertEqual(self.state(),before)
        self.ok(88004,node_id=1101)


    def test_pool301_is_normal_unowned_and_empty_pool_gives_no_duplicate(self):
        normal={int(i) for i in effects.CONTRACT['acquisition_items']}
        self.assertTrue(normal)
        for iid in normal:
            item=self.svc.config.get_item(iid)
            self.assertEqual((item['type'],item['sub_type']),(3,1))
        last=1310006
        state=dict(other_item_list_json=json.dumps([dict(id=i,value=1) for i in normal-{last}]))
        self.assertEqual(effects.roll_relic_gift(self.svc,state),last)
        state['other_item_list_json']=json.dumps([dict(id=i,value=1) for i in normal])
        self.assertIsNone(effects.roll_relic_gift(self.svc,state))
        self.assertFalse(any(str(i).startswith(('132','133')) for i in normal))

    def test_photo_gift_immediate_program_chain_has_all_native_output_deltas(self):
        self.attrs(**{'20':99999});self.get_item(1310016)
        self.finish_event(1201);self.finish_event(1211)
        with patch.object(effects,'roll_relic_gift',return_value=1310006):
            response=self.finish_event(1221)
        programs=json.loads(self.saved()['treasure_list_json'])
        self.assertEqual(len(programs),2);self.assertEqual(len({t['id'] for t in programs}),2)
        changes=[u for f in response.frames if f['sc']==88205 for u in codec.decode(f['payload'],'sc_88205')['update_list']]
        self.assertEqual({u['id'] for u in changes},{1310006}|{t['id'] for t in programs})
        self.assertEqual({u['source_type'] for u in changes},{1})
        self.assertEqual({u['source_item_id'] for u in changes if u['id']!=1310006},{1310006})
        if 88001 in response.scs:self.assertLess(response.scs.index(88021),response.scs.index(88001))

    def test_duplicate_managed_acquisition_does_not_reset_clock_or_stack(self):
        self.attrs(**{'20':99999});self.get_item(1310016);self.finish_event(1301)
        self.get_item(1310016,1311)
        state=self.saved();rt=flow.flow(state)['room_effects_v1']
        self.assertEqual(rt['items']['1310016']['moment_time'],1)
        self.assertEqual(sum(o['id']==1310016 for o in json.loads(state['other_item_list_json'])),1)
        self.assertEqual(sum(e['effect_id']==13100161 for e in json.loads(state['effect_list_json'])),1)

    def test_ascetic_no_fake_upgrade_at_native_level_cap_or_other_subtype(self):
        self.attrs(**{'20':99999});self.get_item(1310022)
        programs=[dict(id=1410101,rare=3),dict(id=1420101,rare=2)]
        self.set(treasure_list_json=json.dumps(programs))
        for nid in (1401,1411):self.finish_event(nid)
        response=self.finish_event(1421)
        self.assertEqual(json.loads(self.saved()['treasure_list_json']),programs)
        self.assertNotIn(88021,response.scs);self.assertNotIn(88205,response.scs)
        counter=flow.flow(self.saved())['room_effects_v1']['items']['1310022']
        self.assertEqual((counter['moment_time'],counter['calc_value']),(3,0))

    def test_tree_bought_after_prior_spend_free_buy_and_positive_buy(self):
        self.attrs(**{'6':1000,'20':99999});self.place(1501,kind=5);self.ok(88004,node_id=1501)
        def offer(iid,discount=100):
            self.set(shop_info_json=json.dumps(dict(event_type=7,param_list=[dict(index=1,param=iid,rare=1,discount=discount,buy_times=1,is_new=1)])))
            return self.ok(88222,index=1)[1]
        offer(1310007);offer(1310002)
        self.assertNotIn(1310002,json.loads(self.saved()['expired_items_json']))
        gold=effects.attrs(self.saved())[6]
        offer(1310014,0)
        self.assertEqual(effects.attrs(self.saved())[6],gold)
        self.assertNotIn(1310002,json.loads(self.saved()['expired_items_json']))
        response=offer(1310001)
        self.assertIn(1310002,json.loads(self.saved()['expired_items_json']))
        self.assertFalse(any(e['effect_id']==13100021 for e in json.loads(self.saved()['effect_list_json'])))
        self.assertIn(88001,response.scs)

    def test_no_gold_event_positive_blocked_negative_spend_preserved(self):
        self.attrs(**{'6':100,'20':99999});self.get_item(1310022)
        state=self.saved();attr=effects.attrs(state)
        config=self.svc.config
        with patch.object(config,'get_event_effect',side_effect=lambda eid:dict(action=1001,params=[1,6,0,50 if eid==1 else -20])):
            self.svc._execute_event_effects(UID,100001,state,dict(id=0,effect_ids=[1,2]),attr,[],[],[])
        self.assertEqual(attr[6],80)

    def test_reward_acquisition_persists_immediate_attribute_effect(self):
        self.attrs(**{'7':0,'20':99999});response=self.get_item(1310007)
        self.assertEqual(effects.attrs(self.saved())[7],3)
        packet=next(codec.decode(f['payload'],'sc_88013') for f in response.frames if f['sc']==88013)
        self.assertEqual(next(a['value'] for a in packet['attr_list'] if a['attr_id']==7),3)


    def test_gift_native_percent_all_effects_and_future_moment_filter(self):
        state=self.saved();attributes=effects.attrs(state);attributes[6]=200
        heroes=[dict(hero_id=1084,hp_ratio=4000)];treasures=[];others=[];extra={}
        apply=lambda iid:effects.apply_gift_on_get(self.svc,UID,100001,state,iid,attributes,treasures,others,heroes,extra)
        apply(1310005);self.assertEqual(attributes[6],300) # 50% of current, not +500
        before=dict(attributes);apply(1310017) # a future room moment, not an immediate random relic
        self.assertEqual(attributes,before);self.assertEqual(extra,{})
        previous_max_hp=attributes.get(23,0)
        apply(1310013) # both immediate effect IDs, not only item_id*10+1
        self.assertEqual(heroes[0]['hp_ratio'],9000);self.assertEqual(attributes[23],previous_max_hp+250)

    def test_photo_chain_encoding_failure_rolls_back_collection_and_programs(self):
        self.attrs(**{'20':99999});self.get_item(1310016)
        self.finish_event(1601);self.finish_event(1611)
        self.place(1621,kind=7,state=1,selected=1621,pending=dict(event_id=50012,opt_list=[],trigger_type=1))
        before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(effects,'roll_relic_gift',return_value=1310006),patch.object(ctx,'codec_encode',
                side_effect=lambda schema,data:None if schema=='sc_88205' else encode(schema,data)):
            self.rejected(88024,opt_id=0)
        self.assertEqual(self.state(),before)
        with patch.object(effects,'roll_relic_gift',return_value=1310006):self.ok(88024,opt_id=0)
        self.assertEqual(len(json.loads(self.saved()['treasure_list_json'])),2)


class FreshRogueLoginTests(MemoryTests):
    flood = RogueLoginTests.flood
    assert_sequence = RogueLoginTests.assert_sequence

    def test_empty_rogue_user_real_88315_serialized_login_initializes_valid_row(self):
        self.svc=RogueTeamService.get(db=self.db);self.raw=[]
        self.assertEqual(self.db.query('SELECT * FROM rogueteam_user'),[])
        before={table:self.db.query('SELECT * FROM '+table+' ORDER BY rowid') for table in
                ('currency','material','rogueteam_session','rogueteam_history','rogueteam_tree')}
        packets=self.flood(fresh=True,captured=False)
        self.assert_sequence(packets,active=False)
        rows=self.db.query('SELECT * FROM rogueteam_user')
        self.assertEqual(len(rows),1);self.assertNotIn('tech_point',rows[0])
        self.assertEqual((rows[0]['uid'],rows[0]['template_id'],rows[0]['max_difficult'],rows[0]['point']),(UID,100001,1,0))
        self.assertGreater(packets[0][1]['next_timestamps'],0)
        for table,rows in before.items():self.assertEqual(self.db.query('SELECT * FROM '+table+' ORDER BY rowid'),rows)


class EffectBattleTests(RogueBattleTests):
    def prepare_effect_boss(self):
        self.simple(terminal=True)
        self.set(other_item_list_json=json.dumps([dict(id=1310022,value=1)]),
                 treasure_list_json=json.dumps([dict(id=1410101,rare=2)]))
        meta=flow.flow(self.saved());meta.pop('room_effects_v1',None)
        effects.counter(effects.runtime(meta),1310022)['moment_time']=2
        flow.save_flow(self.svc,UID,100001,meta)
        self.assertEqual(self.enter()[0]['result'],0)

    def test_final_boss_upgrade_native_delta_before_snapshot_and_receipt(self):
        self.prepare_effect_boss();packet,response=self.finish();self.assertEqual(packet['result'],0)
        self.assertEqual(json.loads(self.saved()['treasure_list_json'])[0]['rare'],3)
        self.assertLess(response.scs.index(88033),response.scs.index(88021))
        self.assertLess(response.scs.index(88021),response.scs.index(88205))
        self.assertLess(response.scs.index(88205),response.scs.index(88001))
        self.assertNotIn(88017,response.scs)
        self.assertEqual(self.packets(response,88021)[0]['item_list'][0]['opt'],3)
        update=self.packets(response,88205)[0]['update_list'][0]
        self.assertEqual((update['operate'],update['rare'],update['source_rare']),(3,3,2))
        before=self.state();self.assertEqual(self.finish()[1].scs,[54033]);self.assertEqual(self.state(),before)

    def test_final_boss_delta_encoding_failure_rolls_back_then_retries(self):
        for schema in ('sc_88021','sc_88205'):
            self.prepare_effect_boss();before=self.state();encode=self.ctx.codec_encode
            with patch.object(self.ctx,'codec_encode',side_effect=lambda name,*a,**kw:None if name==schema else encode(name,*a,**kw)):
                packet,response=self.finish()
            self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54033]);self.assertEqual(self.state(),before)
            self.assertEqual(self.finish()[0]['result'],0)

    def test_normal_battle_counter_waits_for_last_reward_claim(self):
        self.set(other_item_list_json=json.dumps([dict(id=1310022,value=1)]),
                 treasure_list_json=json.dumps([dict(id=1410101,rare=2)]))
        meta=flow.flow(self.saved());effects.counter(effects.runtime(meta),1310022)['moment_time']=2
        flow.save_flow(self.svc,UID,100001,meta)
        self.assertEqual(self.enter()[0]['result'],0);self.assertEqual(self.finish()[0]['result'],0)
        self.assertEqual(flow.flow(self.saved())['room_effects_v1']['items']['1310022']['moment_time'],2)
        pending=json.loads(self.saved()['other_info_json'])
        while pending.get('param_list'):
            packet,response=self.call(88200,dict(event_id=pending['event_type'],param_arg=pending['param_list'][0]['index']),88201)
            self.assertEqual(packet['result'],0);pending=json.loads(self.saved()['other_info_json'])
        self.assertEqual(flow.flow(self.saved())['room_effects_v1']['items']['1310022']['moment_time'],3)
        self.assertIn(88205,response.scs)


if __name__ == '__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(EffectsTests)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(FreshRogueLoginTests))
    suite.addTests(EffectBattleTests(name) for name in (
        'test_final_boss_upgrade_native_delta_before_snapshot_and_receipt',
        'test_final_boss_delta_encoding_failure_rolls_back_then_retries',
        'test_normal_battle_counter_waits_for_last_reward_claim'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
