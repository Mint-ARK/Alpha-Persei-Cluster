"""Rogue battle boundary: real memory Core/codec and native UDP body parsing."""
import json
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests,UID,NOW
import battle_server as udp
import codec
import hero_codec
import middleware as mw
import rogueteam_battle as battle
import rogueteam_flow as flow
import rogueteam_service as rogue


class RogueBattleTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for change in (patch('time.time',return_value=NOW),patch.dict(mw._BATTLE_SESSIONS,{},clear=True)):
            change.start();self.addCleanup(change.stop)
        self.svc=rogue.RogueTeamService.get(db=self.db)
        self.ctx=self.conn.to_handler_ctx()
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        for iid in (1,2,4):self.db.set_currency(UID,iid,1000)
        self.db.upsert('rogueteam_user',UID,dict(template_id=100001,max_difficult=4),keys=('uid','template_id'))
        for iid in (70,71):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))
        self.assertEqual(self.svc.start_run(UID,100001,1,[dict(hero_id=1084)],[])[0],0)
        self.simple()

    def saved(self):return self.db.get('rogueteam_session',UID,'AND template_id=?',(100001,))
    def set(self,**kw):self.db.upsert('rogueteam_session',UID,dict(template_id=100001,**kw),keys=('uid','template_id'))
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('rogueteam_user','rogueteam_session','rogueteam_history','rogueteam_illustrated',
                 'users','currency','material','hero','chapter','stage_sub','history_cmd','task')}

    def simple(self,terminal=False):
        if terminal:
            nodes=[dict(node_id=307,col=7,row=1,node_type=4,state=0,param=4103304,next_id_list=[])]
            self.set(floor_num=3,floor_state=0,pending_ending=6,ending_won=0)
        else:
            nodes=[dict(node_id=101,col=1,row=1,node_type=1,state=0,param=4101101,next_id_list=[201]),
                   dict(node_id=201,col=2,row=1,node_type=1,state=2,param=4101102,next_id_list=[])]
            self.set(floor_num=1,floor_state=0,pending_ending=0,ending_won=0)
        meta=flow.flow(self.saved());meta.update(phase='active',hidden=False)
        flow.save_flow(self.svc,UID,100001,meta)
        self.set(map_info_json=json.dumps(nodes),select_node_id=0,other_info_json='{}',shop_info_json='{}')
        self.node=nodes[0]['node_id']
        self.assertEqual(self.svc.select_node(UID,self.node)[0],0)
        return nodes

    def enter(self,kind=78,dest=None,times=1):
        packet,response=self.call(54030,dict(common_info=dict(type=kind,dest=self.node if dest is None else dest,
            activity_id=1100,battle_times=times,hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
        if packet['result']==0:self.bid=self.conn.battle_id[0]
        return packet,response

    def finish(self,result=1,bid=None):
        bid=bid or self.bid
        raw=udp._f_varint2(1,bid)+udp._f_varint2(2,UID)+udp._f_varint2(3,result)+udp._f_bytes(4,
            udp._f_varint2(1,int(result==1))+udp._f_varint2(2,45000))
        got,uid,result,info=udp.parse_battle_result(raw);self.assertEqual((got,uid),(bid,UID))
        mw.get_battle_session(bid)['result_data']=dict(result=result,info=info)
        return self.call(54032,dict(battle_id=bid),54033)

    def packets(self,response,sc):
        return [codec.decode(f['payload'],'sc_'+str(sc)) for f in response.frames if f['sc']==sc]

    def test_entry_native_snapshot_fields_real_stage_and_initial_frame(self):
        packet,response=self.enter();self.assertEqual(packet['result'],0)
        session=mw.get_battle_session(self.bid)
        self.assertEqual(session['dest'],4101101);self.assertEqual(session['node_id'],101)
        self.assertEqual(session['rogue_run_id'],flow.flow(self.saved())['run_id'])
        self.assertEqual(session['rogue_floor_num'],1);self.assertFalse(session['rogue_hidden'])
        self.assertEqual(session['rogue_real_stage'],4101101)
        self.assertIn(88001,response.scs);self.assertTrue(self.packets(response,88001)[0]['map_info'])

    def test_initial_88001_encoding_failure_publishes_no_session_or_state(self):
        before=self.state();encode=self.ctx.codec_encode
        with patch.object(self.ctx,'codec_encode',side_effect=lambda n,*a,**kw:None if n=='sc_88001' else encode(n,*a,**kw)):
            packet,response=self.enter()
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(mw._BATTLE_SESSIONS,{});self.assertEqual(response.scs,[54031])

    def test_entry_rejects_wrong_type_stage_or_multi_without_publication(self):
        for kwargs in (dict(kind=1),dict(dest=201),dict(dest=4101101),dict(times=2)):
            before=self.state();packet,response=self.enter(**kwargs)
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(mw._BATTLE_SESSIONS,{})

    def test_failure_and_quit_never_advance_run_or_generic_world(self):
        for result in (2,3):
            self.assertEqual(self.enter()[0]['result'],0);before=self.state()
            packet,response=self.finish(result);self.assertEqual(packet['result'],0)
            self.assertEqual(self.state(),before);self.assertEqual(response.scs,[54033])
            self.assertEqual(self.saved()['select_node_id'],101)

    def test_success_only_updates_run_and_external_rogue_packets(self):
        self.assertEqual(self.enter()[0]['result'],0)
        world={t:self.db.query('SELECT * FROM '+t) for t in ('users','hero','currency','material','chapter','stage_sub')}
        packet,response=self.finish();self.assertEqual(packet['result'],0)
        self.assertTrue({88033,88015,88001,88013,88019,54033}<=set(response.scs))
        self.assertLess(response.scs.index(88033),response.scs.index(88019))
        hp=self.packets(response,88019)[0]['hero_list']
        persisted=json.loads(self.saved()['hero_list_json'])
        self.assertEqual({r['hero_id']:r['hp_ratio'] for r in hp},{r['hero_id']:r['hp_ratio'] for r in persisted})
        self.assertEqual(self.saved()['battle_passed_count'],1);self.assertEqual(self.saved()['select_node_id'],0)
        for t,rows in world.items():self.assertEqual(self.db.query('SELECT * FROM '+t),rows)
        self.assertNotIn(24009,response.scs);self.assertNotIn(17023,response.scs)
        self.assertFalse(packet['battle_result'].get('all_drop_list'));self.assertFalse(packet['battle_result'].get('star_list'))
        summary=self.packets(response,88033)[0]
        self.assertEqual(summary['treasure_num'],1);self.assertGreater(summary['coin_num'],0)
        candidates=self.packets(response,88015)[0]['other_info']['param_list']
        self.assertEqual(len(candidates),3)

    def test_same_bid_replays_only_immutable_receipt(self):
        self.assertEqual(self.enter()[0]['result'],0);first,_=self.finish();before=self.state()
        packet,response=self.finish();self.assertEqual(packet,first)
        self.assertEqual(response.scs,[54033]);self.assertEqual(self.state(),before)

    def test_session_uid_mismatch_rejects_before_new_or_cached_receipt(self):
        self.assertEqual(self.enter()[0]['result'],0)
        session=mw.get_battle_session(self.bid)
        before=self.state();session['uid']=UID+1
        packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertNotIn('br',session);self.assertEqual(response.scs,[54033])
        session['uid']=UID
        self.assertEqual(self.finish()[0]['result'],0)
        self.assertIn('br',session)
        before=self.state();session['uid']=UID+1
        packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(response.scs,[54033]);session['uid']=UID
        self.assertEqual(self.finish()[0]['result'],0);self.assertEqual(self.state(),before)

    def test_each_required_battle_packet_failure_rolls_back_and_retry(self):
        for name in ('sc_88033','sc_88015','sc_88001','sc_88013','sc_88019','sc_54033'):
            self.simple();self.assertEqual(self.enter()[0]['result'],0)
            before=self.state();encode=self.ctx.codec_encode
            with patch.object(self.ctx,'codec_encode',side_effect=lambda n,*a,**kw:None if n==name else encode(n,*a,**kw)):
                packet,response=self.finish()
            self.assertNotEqual(packet['result'],0,name);self.assertEqual(self.state(),before,name)
            self.assertNotIn('br',mw.get_battle_session(self.bid),name);self.assertEqual(response.scs,[54033],name)
            self.assertEqual(self.finish()[0]['result'],0,name)

    def test_old_run_floor_hidden_selected_state_and_real_stage_reports_rejected(self):
        for target in ('run','floor','hidden','node','state','stage'):
            self.simple();self.assertEqual(self.enter()[0]['result'],0)
            if target in ('run','hidden'):
                meta=flow.flow(self.saved());meta.update({'run_id':'replacement'} if target=='run' else {'hidden':True})
                flow.save_flow(self.svc,UID,100001,meta)
            elif target=='floor':self.set(floor_num=2)
            elif target=='node':self.set(select_node_id=201)
            else:
                nodes=json.loads(self.saved()['map_info_json'])
                nodes[0].update({'state':3} if target=='state' else {'param':4101102})
                self.set(map_info_json=json.dumps(nodes))
            before=self.state();packet,response=self.finish()
            self.assertNotEqual(packet['result'],0,target);self.assertEqual(self.state(),before,target)
            self.assertNotIn('br',mw.get_battle_session(self.bid),target);self.assertNotIn(88033,response.scs,target)

    def test_sweep_rejects_rogue_without_mutating_progress(self):
        for dest,kind in ((101,78),(101,0),(4101101,78),(4101101,1)):
            before=self.state()
            packet,response=self.call(54038,dict(common_info=dict(type=kind,dest=dest,activity_id=1100,
                hero_list=[dict(hero_id=1084,hero_type=1)])),54039)
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)

    def test_terminal_battle_plot_then_report_once_and_no_selection_popup(self):
        self.simple(terminal=True);self.assertEqual(self.enter()[0]['result'],0)
        packet,response=self.finish();self.assertEqual(packet['result'],0)
        self.assertIn(88221,response.scs);self.assertNotIn(88015,response.scs)
        self.assertEqual(flow.flow(self.saved())['phase'],'ending_plot')
        before=self.state();self.assertNotEqual(self.call(88306,{},88307)[0]['result'],0);self.assertEqual(self.state(),before)
        packet,response=self.call(88008,dict(sign=1),88009);self.assertEqual(packet['result'],0);self.assertIn(88217,response.scs)
        self.assertEqual(self.call(88218,{},88219)[0]['result'],0)
        packet,response=self.call(88306,{},88307);self.assertEqual(packet['result'],0);self.assertIn(88305,response.scs)
        outside=self.packets(response,88305)[0];self.assertEqual(outside['his_avg'],[dict(key=6,value=1)])
        before=self.state();again,response=self.call(88306,{},88307)
        self.assertEqual(again,packet);self.assertEqual(self.state(),before);self.assertNotIn(17023,response.scs)


if __name__=='__main__':
    unittest.main(verbosity=2)
