"""Citong 84/85 and position integration through memory Core/native UDP codec."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
from verify_minigame_point_rewards import ITEM_TYPES
import battle_server as udp
import codec
import generator
import hero_codec
import middleware as mw
import quanzhou_collection as collection
import quanzhou_quest as quest
import quanzhou_service as runtime


class RuntimeTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now=NOW
        self.visible=[dict(activity_id=i,state=1,start_time=1,stop_time=NOW+86400) for i in (323641,323841)]
        for fault in (patch('time.time',side_effect=lambda:self.now),
                      patch.object(generator,'get_activity_list',side_effect=lambda *a,**k:deepcopy(self.visible)),
                      patch.dict(mw._BATTLE_SESSIONS,{},clear=True)):
            fault.start();self.addCleanup(fault.stop)
        self.ctx=self.conn.to_handler_ctx()
        self.db.execute('UPDATE users SET level=70 WHERE uid=?',(UID,))
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        for iid in (1,2,4,12,77,40103,22003):
            kind=1 if iid<100 else ITEM_TYPES[iid][0]
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))
        self.db.set_currency(UID,4,200)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('ops_common','users','currency','material','hero','chapter','stage_sub','history_cmd',
                 'qworld_quest','qworld_quest_task','claim_ledger','quanzhou_sail_progress')}

    def task(self,tid,status=0):
        row=quest.TASKS[tid]
        self.db.execute('INSERT INTO qworld_quest(uid,main_quest_id,status) VALUES(?,?,3) '
                        'ON CONFLICT(uid,main_quest_id) DO UPDATE SET status=3',(UID,row['main_task_id']))
        self.db.execute('INSERT INTO qworld_quest_task(uid,main_quest_id,task_id,status,progress) VALUES(?,?,?,?,?) '
                        'ON CONFLICT(uid,main_quest_id,task_id) DO UPDATE SET status=excluded.status,progress=excluded.progress',
                        (UID,row['main_task_id'],tid,status,int(status==1)))

    def position(self,**changes):
        row=dict(stage_id=1110101,map_status=0,position_x=1.25,position_y=-2.5,position_z=3.75,
                 rotation_x=0.,rotation_y=0.,rotation_z=0.,rotation_w=1.)
        row.update(changes);return row

    def save_position(self,row=None,activity=323641):
        return self.call(79602,dict(activity_id=activity,stage_list=[row or self.position()]),79603)

    def enter(self,dest=400101,kind=84,activity=323841,team=None,times=1,index=0,cmd=54030):
        if team is None:
            team=([dict(hero_id=trial,hero_type=2) for hero,trial in runtime.SAND[dest]['hero_list'] if hero and trial]
                  if kind==85 and dest in runtime.SAND else [dict(hero_id=1084,hero_type=1)])
        packet,response=self.call(cmd,dict(common_info=dict(type=kind,dest=dest,activity_id=activity,
            hero_list=team,battle_times=times,index=index)),54031 if cmd==54030 else 54039)
        if packet['result']==0 and cmd==54030:self.bid=self.conn.battle_id[0]
        return packet,response

    def finish(self,result=1,bid=None):
        bid=bid or self.bid
        info=udp._f_varint2(1,int(result==1))+udp._f_varint2(2,43210)
        wire=udp._f_varint2(1,bid)+udp._f_varint2(2,UID)+udp._f_varint2(3,result)+udp._f_bytes(4,info)
        got,uid,out,report=udp.parse_battle_result(wire)
        self.assertEqual((got,uid,out),(bid,UID,result))
        mw.get_battle_session(bid)['result_data']=dict(result=out,info=report)
        return self.call(54032,dict(battle_id=bid),54033)

    def packets(self,response,sc):
        return [codec.decode(f['payload'],'sc_'+str(sc)) for f in response.frames if f['sc']==sc]

    def test_position_valid_native_roundtrip_and_cold_recovery(self):
        packet,response=self.save_position();self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs,[79603])
        cold=codec.decode(generator.gen_payload(79601,uid=UID,db=self.db),'sc_79601')
        self.assertEqual(cold['activity_id'],323641);self.assertEqual(cold['stage_list'],[self.position()])

    def test_position_invalid_map_status_activity_nan_and_range_preserve_state(self):
        self.save_position()
        for changes in (dict(stage_id=123),dict(map_status=1),dict(position_x=float('nan')),
                        dict(position_y=float('inf')),dict(position_z=10000001)):
            before=self.state();packet,response=self.save_position(self.position(**changes))
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        before=self.state();self.assertNotEqual(self.save_position(activity=323841)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_position_ack_failure_rolls_back(self):
        before=self.state();encode=self.ctx.codec_encode
        with patch.object(self.ctx,'codec_encode',side_effect=lambda n,*a,**kw:b'' if n=='sc_79603' else encode(n,*a,**kw)):
            packet,response=self.save_position()
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[79603]);self.assertEqual(self.state(),before)

    def test_sea_logic_to_resource_identity_and_activity_zero(self):
        self.task(11103901)
        for activity in (323841,0):
            packet,response=self.enter(activity=activity);self.assertEqual(packet['result'],0)
            session=mw.get_battle_session(self.bid)
            self.assertEqual(session['dest'],400101);self.assertEqual(session['quanzhou_context']['stage'],5240202)
            self.assertEqual(session['quanzhou_context']['activity'],activity)
            self.assertIn(54003,response.scs);self.assertIn(54007,response.scs)

    def test_sand_both_configured_trial_teams_and_wrong_team_rejected(self):
        for dest,tid in ((5240221,11105003),(5240222,11107007)):
            self.task(tid)
            for team in ([],[dict(hero_id=1084,hero_type=1)],
                         [dict(hero_id=trial,hero_type=1) for hero,trial in runtime.SAND[dest]['hero_list'] if hero and trial]):
                before=self.state();packet,response=self.enter(dest=dest,kind=85,team=team)
                self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
            packet,response=self.enter(dest=dest,kind=85);self.assertEqual(packet['result'],0)
            expected=[trial for hero,trial in runtime.SAND[dest]['hero_list'] if hero and trial]
            self.assertEqual(mw.get_battle_session(self.bid)['heroes'],expected)
            self.assertIn(54003,response.scs)

    def test_wrong_type_resource_dest_activity_multi_and_locked_entry_rejected(self):
        self.task(11103901)
        for args in (dict(kind=1),dict(kind=85),dict(dest=5240202),dict(activity=323641),dict(times=2),dict(dest=400102)):
            before=self.state();packet,response=self.enter(**args)
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)

    def test_sweep_cannot_bypass_type_or_resource_identity(self):
        self.task(11103901)
        for dest,kind in ((400101,84),(400101,1),(5240202,1),(5240221,85),(5240221,1)):
            before=self.state();packet,response=self.enter(dest=dest,kind=kind,cmd=54038)
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)

    def test_sea_win_real_inventory_quest_delta_and_immutable_replay(self):
        self.task(11103901);self.assertEqual(self.enter()[0]['result'],0)
        exp=self.balance(12);fatigue=self.balance(4);hero=self.db.query('SELECT * FROM hero')
        packet,response=self.finish();self.assertEqual(packet['result'],0)
        self.assertEqual(collection.read(self.db,UID)['completed'],[400101])
        self.assertEqual((self.balance(1),self.balance(40103),self.balance(22003)),(20,10,2))
        self.assertEqual((self.balance(12),self.balance(4)),(exp,fatigue));self.assertEqual(self.db.query('SELECT * FROM hero'),hero)
        self.assertEqual(quest.read(self.db,UID)[1][11103901]['progress'],1)
        self.assertIn(86035,response.scs);self.assertIn(28803,response.scs);self.assertIn(17023,response.scs)
        self.assertNotIn(24009,response.scs);self.assertFalse(packet['battle_result'].get('star_list'))
        drops=packet['battle_result']['all_drop_list'][0]['gain_list']
        self.assertEqual({r['id']:r['num'] for r in drops},{1:20,40103:10,22003:2})
        before=self.state();packet,response=self.finish()
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54033]);self.assertEqual(self.state(),before)

    def test_sand_win_advances_only_own_quest_without_generic_growth(self):
        self.task(11105003);self.assertEqual(self.enter(dest=5240221,kind=85)[0]['result'],0)
        growth={table:self.db.query('SELECT * FROM '+table) for table in ('users','hero','currency','chapter','stage_sub')}
        packet,response=self.finish();self.assertEqual(packet['result'],0)
        self.assertEqual(quest.read(self.db,UID)[1][11105003]['progress'],1)
        for table,rows in growth.items():self.assertEqual(self.db.query('SELECT * FROM '+table),rows)
        self.assertFalse(packet['battle_result'].get('all_drop_list'));self.assertIn(28803,response.scs)

    def test_loss_quit_clear_identity_without_quest_reward_or_generic_growth(self):
        self.task(11103901)
        for result in (2,3):
            self.assertEqual(self.enter()[0]['result'],0)
            before={t:self.db.query('SELECT * FROM '+t) for t in ('currency','material','hero','chapter','stage_sub','qworld_quest_task')}
            packet,response=self.finish(result);self.assertEqual(packet['result'],0)
            self.assertIsNone(runtime.read(self.db,UID)['latest']);self.assertEqual(response.scs,[54033])
            for table,rows in before.items():self.assertEqual(self.db.query('SELECT * FROM '+table),rows)

    def test_new_attempt_replaces_old_without_consuming_current_identity(self):
        self.task(11103901);self.assertEqual(self.enter()[0]['result'],0);old=self.bid
        self.assertEqual(self.enter()[0]['result'],0);new=self.bid
        before=self.state();packet,response=self.finish(bid=old)
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertNotIn('br',mw.get_battle_session(old));self.assertEqual(runtime.read(self.db,UID)['latest']['bid'],new)
        self.assertEqual(self.finish(bid=new)[0]['result'],0)

    def test_entry_required_packet_failure_does_not_publish_attempt(self):
        self.task(11103901)
        for name in ('sc_54007',):
            before=self.state();sessions=deepcopy(mw._BATTLE_SESSIONS);encode=self.ctx.codec_encode
            with patch.object(self.ctx,'codec_encode',side_effect=lambda n,*a,**kw:b'' if n==name else encode(n,*a,**kw)):
                packet,response=self.enter()
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before);self.assertEqual(mw._BATTLE_SESSIONS,sessions)
        before=self.state()
        with patch.object(mw.battle_payload,'build_sc_54003_dynamic',return_value=None):
            packet,response=self.enter()
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before);self.assertEqual(mw._BATTLE_SESSIONS,{})

    def test_required_settlement_frames_rollback_sql_notifications_and_br(self):
        self.task(11103901)
        for name in ('sc_86035','sc_28803','sc_54033'):
            self.assertEqual(self.enter()[0]['result'],0);before=self.state();encode=self.ctx.codec_encode
            with patch.object(self.ctx,'codec_encode',side_effect=lambda n,*a,**kw:b'' if n==name else encode(n,*a,**kw)):
                packet,response=self.finish()
            self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
            self.assertNotIn('br',mw.get_battle_session(self.bid));self.assertEqual(response.scs,[54033])
            # Do not accept a false failure caused by an earlier unrelated exception.
            self.assertEqual(self.finish()[0]['result'],0)
            state=collection.read(self.db,UID);state['completed']=[];collection.save(self.db,UID,state)
            self.task(11103901)

    def test_asset_delta_failure_rolls_back_first_clear_and_can_retry(self):
        self.task(11103901);self.assertEqual(self.enter()[0]['result'],0)
        before=self.state();real=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else real(cmd,*a,**kw)):
            packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54033])
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
        self.assertEqual(self.finish()[0]['result'],0)

    def test_sand_defeat_and_activity_closed_settlement_no_progress(self):
        self.task(11105003);self.assertEqual(self.enter(dest=5240221,kind=85)[0]['result'],0)
        packet,response=self.finish(result=2);self.assertEqual(packet['result'],0)
        self.assertEqual(quest.read(self.db,UID)[1][11105003]['progress'],0)
        self.assertEqual(self.enter(dest=5240221,kind=85)[0]['result'],0)
        self.visible=[];before=self.state();packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertEqual(self.state(),before)
        self.assertNotIn('br',mw.get_battle_session(self.bid))


if __name__=='__main__':
    unittest.main(verbosity=2)
