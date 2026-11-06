"""Isolated p49 Core + native map route and battle continuation checks."""
from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID
import codec
import generator
import hero_codec
import middleware as mw
import warchess_service as w
import warchess_battle as battle


class WarChessTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for iid,kind in ((1,1),(2,1),(40801,3),(40802,3),(40803,3)):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))

    def enter(self,cid=4040101):return self.call(49002,dict(chapter_id=cid),49003)
    def move(self,path):return self.call(49004,dict(path=[dict(x=x,z=z) for x,z in path]),49005)
    def touch(self,pos,param=0,kind=1):return self.call(49006,dict(pos=dict(x=pos[0],z=pos[1]),param=param,type=kind),49007)
    def state(self):return w.read(self.db,UID)
    def engine(self):return w.Engine(self.db,UID,self.state())
    def full(self):return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('ops_common','warchess_map','warchess_session','warchess_box','warchess_box_record','currency','material','task','claim_ledger','chapter','stage_sub')}

    def path_to(self,target):
        e=self.engine();origin=w.position(e.m['pos']);queue=deque([(origin,[])]);seen={origin}
        while queue:
            pos,path=queue.popleft()
            if pos==target or (target[0]-pos[0],target[1]-pos[1]) in w.DIRECTIONS:return path
            for dx,dz in w.DIRECTIONS:
                nxt=(pos[0]+dx,pos[1]+dz)
                if nxt in seen or f'{nxt[0]},{nxt[1]}' not in e.native['tiles']:continue
                g=e.grid(nxt);h=e.hex(g)
                if h['move']<=0:continue
                seen.add(nxt);queue.append((nxt,path+[nxt]))
        return None

    def approach(self,target):
        for _ in range(20):
            path=self.path_to(target);self.assertIsNotNone(path,target)
            if not path:return
            self.assertEqual(self.move(path)[0]['result'],0,(target,path))
        self.fail(('Path did not reach adjacent tile',target))

    def win(self,result=1,report=None):
        s=self.state();p=s['pending'];context=battle.entry(self.conn.to_handler_ctx(),UID,dict(type=15,dest=p['stage_id'],battle_times=1,activity_id=w.LEVELS[s['chapter']]['type'],
             index=p['pos'][1]*w.CFG['maps'][str(s['chapter'])]['width']+p['pos'][0]+1,heroes=[1084]))
        ctx=self.conn.to_handler_ctx()
        with battle.publication(ctx,UID,123,context):pass
        session=dict(uid=UID,stage_type=15,dest=p['stage_id'],warchess_context=context)
        with self.db.transaction():
            frames=battle.settle(ctx,UID,session,result,report or {})
            battle.validate(self.db,UID,session,after=True)
        return session,frames

    def core_battle(self):
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        s=self.state();p=s['pending']
        return self.call(54030,dict(common_info=dict(type=15,dest=p['stage_id'],battle_times=1,activity_id=w.LEVELS[s['chapter']]['type'],
             index=p['pos'][1]*w.CFG['maps'][str(s['chapter'])]['width']+p['pos'][0]+1,hero_list=[dict(hero_id=1084,hero_type=1)])),54031)

    def core_finish(self,result=1,report=None,bid=None):
        bid=bid or self.conn.battle_id[0]
        mw.get_battle_session(bid)['result_data']=dict(result=result,info=report or {})
        return self.call(54032,dict(battle_id=bid),54033)

    def first_battle(self):
        self.enter();self.approach((18,3));self.assertEqual(self.touch((18,3))[0]['result'],0)
        self.approach((18,4));self.assertEqual(self.touch((18,4))[0]['result'],0)
        self.assertEqual(self.state()['pending']['stage_id'],4040101)

    def test_native_entry_and_resume_without_replayed_initial_globals(self):
        p,r=self.enter();self.assertEqual(r.scs,[49003]);m=p['map_info']
        self.assertEqual(m['pos'],dict(x=17,z=1));self.assertEqual(len(m['fog']),12)
        self.assertIn(1010101,[e['id'] for e in m['event_list']]);s=self.state()
        self.assertNotIn(1010101,[e['id'] for e in s['map']['event_list']]);p,r=self.enter()
        self.assertEqual(codec.encode('sc_49003',p),codec.encode('sc_49003',dict(result=0,map_info=s['map'])))

    def test_bad_ids_coordinates_remote_loot_and_wrong_param_do_not_write(self):
        before=self.full();self.assertNotEqual(self.enter(999)[0]['result'],0);self.assertEqual(self.full(),before)
        self.enter();before=self.full()
        for p,param,kind in [((999,999),10204,1),((20,7),0,1),((18,2),1001,1),((18,2),0,0)]:
            self.assertNotEqual(self.touch(p,param,kind)[0]['result'],0);self.assertEqual(self.full(),before)
        self.assertNotEqual(self.move([(20,7)])[0]['result'],0);self.assertEqual(self.full(),before)
        self.assertNotEqual(self.move([(17,0)])[0]['result'],0);self.assertEqual(self.full(),before)

    def test_native_extension_tag_and_atomic_move_replay(self):
        self.enter();self.approach((18,3));before=self.state()['map']['pos'];self.touch((18,3));g=self.engine().grid((18,3))
        self.assertEqual((g['tag'],g['status'],g['typeID']),(1,0,10101));self.assertEqual(self.state()['map']['pos'],before)
        p=[(18,3)];self.assertEqual(self.move(p)[0]['result'],0);s=self.full();self.assertEqual(self.move(p)[0]['result'],0);self.assertEqual(self.full(),s)

    def test_battle_suspends_before_grid_change_failure_does_not_clear(self):
        self.first_battle();self.assertEqual(self.engine().grid((18,4))['tag'],0)
        old=self.state()['pending']['token'];session,frames=self.win(2)
        self.assertEqual(self.engine().grid((18,4))['tag'],0);self.assertNotEqual(self.state()['pending']['token'],old)
        with self.assertRaises(Exception):battle.validate(self.db,UID,session)
        self.win();self.assertEqual(self.engine().grid((18,4))['tag'],4)
        self.assertIn([10201,18,4,0],self.state()['events'])

    def test_artifact_is_grid_driven_issued_stable_and_chosen_once(self):
        self.first_battle();_,frames=self.win();self.assertNotIn(49011,[f.cmd for f in frames])
        packet,response=self.touch((18,4));self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[49011,49007])
        pending=deepcopy(self.state()['pending']);choices=pending['choices'];self.assertEqual(len(set(choices)),3)
        self.enter();self.assertEqual(self.state()['pending'],pending)
        self.touch((18,4));self.assertEqual(self.state()['pending'],pending)
        before=self.full();wrong=next(i for i in map(int,w.CFG['items']) if i not in choices)
        self.assertNotEqual(self.touch((18,4),wrong)[0]['result'],0);self.assertEqual(self.full(),before)
        self.assertEqual(self.touch((18,4),choices[0])[0]['result'],0);self.assertEqual(self.engine().grid((18,4))['tag'],1)
        before=self.full();self.assertNotEqual(self.touch((18,4),choices[0])[0]['result'],0);self.assertEqual(self.full(),before)

    def test_entry_and_move_required_encoder_failure_rolls_back(self):
        ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        before=self.full()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_49003' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.enter()[0]['result'],0)
        self.assertEqual(self.full(),before);self.enter();before=self.full()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_49005' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.move([(18,2)])[0]['result'],0)
        self.assertEqual(self.full(),before)

    def test_artifact_push_ack_and_selection_failure_rolls_back(self):
        self.first_battle();self.win();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        for target in ('sc_49011','sc_49007'):
            before=self.full()
            with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n==target and d.get('result',0)==0 else enc(n,d)):
                self.assertNotEqual(self.touch((18,4))[0]['result'],0)
            self.assertEqual(self.full(),before)
        self.touch((18,4));choice=self.state()['pending']['choices'][0];before=self.full()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_49007' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.touch((18,4),choice)[0]['result'],0)
        self.assertEqual(self.full(),before)

    def test_old_run_old_attempt_wrong_tile_stage_or_activity_rejected(self):
        self.first_battle();ctx=self.conn.to_handler_ctx();info=dict(type=15,dest=4040101,index=18+4*26+1,battle_times=1,activity_id=0,heroes=[1084])
        for field,value in [('type',1),('dest',4040102),('index',1),('battle_times',2),('activity_id',61011)]:
            with self.assertRaises(Exception):battle.entry(ctx,UID,dict(info,**{field:value}))
        context=battle.entry(ctx,UID,info)
        with battle.publication(ctx,UID,3,context):pass
        session=dict(uid=UID,stage_type=15,dest=4040101,warchess_context=context)
        self.call(49008,{},49009);self.enter()
        with self.assertRaises(Exception):battle.validate(self.db,UID,session)

    def test_nonexistent_cannon_ship_and_no_active_move_fail(self):
        for cmd,data,sc in [(49004,dict(path=[]),49005),(49016,{},49017),(49024,dict(type=1),49025)]:
            self.assertNotEqual(self.call(cmd,data,sc)[0]['result'],0)
        self.enter()
        for cmd,data,sc in [(49016,{},49017),(49024,dict(type=1),49025)]:self.assertNotEqual(self.call(cmd,data,sc)[0]['result'],0)

    def test_real_core_battle_snapshot_result_no_mainline_awards_and_cached_identity(self):
        self.first_battle();packet,response=self.core_battle();self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54031,54003,54007])
        bid=self.conn.battle_id[0];session=mw.get_battle_session(bid);self.assertTrue(session['warchess_context'])
        before={t:self.db.query('SELECT * FROM '+t) for t in ('chapter','stage_sub','currency','material')}
        packet,response=self.core_finish(report=dict(characters=[dict(hero_id=1084,hero_blood=500,hero_max_blood=1000)]))
        self.assertEqual(packet['result'],0);self.assertNotIn(49011,response.scs);self.assertEqual(self.state()['map']['hp_list'],[dict(id=1084,hp=5000)])
        self.assertEqual({t:self.db.query('SELECT * FROM '+t) for t in before},before)
        self.assertEqual(self.call(54032,dict(battle_id=bid),54033)[0]['result'],0)
        self.call(49008,{},49009);self.enter()
        self.assertNotEqual(self.call(54032,dict(battle_id=bid),54033)[0]['result'],0)

    def test_core_battle_entry_required_frame_failure_and_settlement_ack_rollback(self):
        self.first_battle();self.db.upsert('hero',UID,hero_codec.initial_hero_row(1084,100),keys=('uid','id'))
        before=self.full();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54007' else enc(n,d)):
            self.assertNotEqual(self.core_battle()[0]['result'],0)
        self.assertEqual(self.full(),before);self.assertIsNone(self.conn.battle_id[0]);self.core_battle();bid=self.conn.battle_id[0];before=self.full()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_54033' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.core_finish()[0]['result'],0)
        self.assertEqual(self.full(),before);self.assertNotIn('br',mw.get_battle_session(bid));self.assertEqual(self.core_finish()[0]['result'],0)

    def test_battle_reentry_replaces_prior_attempt_and_health_report_rejects_bad_team(self):
        self.first_battle();self.core_battle();old=self.conn.battle_id[0];self.core_battle();new=self.conn.battle_id[0];self.assertNotEqual(old,new)
        before=self.full();self.assertNotEqual(self.core_finish(bid=old)[0]['result'],0);self.assertEqual(self.full(),before)
        self.assertNotEqual(self.core_finish(report=dict(characters=[dict(hero_id=1080,hero_blood=500,hero_max_blood=1000)]))[0]['result'],0)
        self.assertEqual(self.full(),before);self.assertNotIn('br',mw.get_battle_session(new))

    def test_generated_overview_native_groups_and_open_list(self):
        self.enter();packet=codec.decode(generator.gen_payload(49001,uid=UID,db=self.db),'sc_49001')
        groups={r['activity_id']:r for r in packet['chess_map_list']}
        self.assertEqual(groups[0]['chapter'],4040101)
        self.assertTrue(all(w.LEVELS[r['chapter_id']]['type']==g for g,v in groups.items() for r in v['chapter_info']))
        self.assertEqual(sum(len(v['chapter_info']) for v in groups.values()),40)
        times=codec.decode(generator.gen_payload(49023,uid=UID,db=self.db),'sc_49023');self.assertEqual(len(times['chess_open_info_list']),40)

    def test_task_projection_chest_claim_is_idempotent_and_encoder_failure_rolls_back(self):
        for tid,condition,params,need in ((40300,460,[1],1),(40306,460,[2],1),(40317,462,[0],1),(160038,463,[0],1)):
            self.db.execute('INSERT INTO task_cfg(task_id,condition,additional_parameter,need) VALUES(?,?,?,?)',(tid,condition,json.dumps(params),need))
            self.db.execute('INSERT INTO task(uid,task_id,progress) VALUES(?,?,0)',(UID,tid))
        self.enter()
        # An isolated near-box position avoids replaying fourteen battles in
        # fault probes; the complete native traversal is tested separately.
        s=self.state();s['map']['pos']=dict(x=20,z=8);w.save(self.db,UID,s)
        before=self.full();ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_28007' else enc(n,d)):
            self.assertNotEqual(self.touch((20,7))[0]['result'],0)
        self.assertEqual(self.full(),before)
        gen_enc=generator.encode
        with patch.object(generator,'encode',side_effect=lambda n,d:None if n=='sc_17023' else gen_enc(n,d)):
            self.assertNotEqual(self.touch((20,7))[0]['result'],0)
        self.assertEqual(self.full(),before)
        packet,response=self.touch((20,7));self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[17023,28007,49007])
        self.assertEqual(self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=40300',(UID,))[0]['progress'],1)
        self.assertFalse(self.db.record_warchess_box_opened(UID,4040101,10203,0,20,7));self.assertEqual(self.db.query('SELECT num FROM warchess_box WHERE uid=?',(UID,))[0]['num'],1)

    def test_abandon_ack_rollback_and_fresh_run_reset_keep_claim_history(self):
        self.enter();s=self.state();s['map']['pos']=dict(x=20,z=8);w.save(self.db,UID,s);self.touch((20,7));old=self.state()['run']
        ctx=self.conn.to_handler_ctx();enc=ctx.codec_encode;before=self.full()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_49009' and d.get('result')==0 else enc(n,d)):
            self.assertNotEqual(self.call(49008,{},49009)[0]['result'],0)
        self.assertEqual(self.full(),before);self.call(49008,{},49009);self.assertIsNone(self.state())
        self.enter();self.assertNotEqual(self.state()['run'],old);self.assertEqual(self.state()['map']['pos'],dict(x=17,z=1));self.assertEqual(self.engine().grid((20,7))['tag'],1)

    def test_corrupt_runtime_and_unsupported_event_do_not_replace_or_partially_award(self):
        self.enter();s=self.state();s['map']['pos']=dict(x=20,z=8);w.save(self.db,UID,s);before=self.full()
        original=w.Engine.actions
        with patch.object(w.Engine,'actions',lambda engine,pos,secondary=False:original(engine,pos,secondary)+[[999999]]):
            self.assertNotEqual(self.touch((20,7))[0]['result'],0)
        self.assertEqual(self.full(),before)
        self.db.execute("UPDATE ops_common SET value_json='{' WHERE uid=? AND kind=? AND item_id='active'",(UID,w.KIND));before=self.full()
        self.assertNotEqual(self.enter()[0]['result'],0);self.assertEqual(self.full(),before)

    def test_cached_battle_ack_is_invalid_after_a_new_grid_action(self):
        self.first_battle();self.core_battle();bid=self.conn.battle_id[0];self.core_finish();self.touch((18,4))
        before=self.full();self.assertNotEqual(self.call(54032,dict(battle_id=bid),54033)[0]['result'],0);self.assertEqual(self.full(),before)

    def test_cold_lost_battle_session_can_reopen_same_authorized_tile(self):
        self.first_battle();self.core_battle();bid=self.conn.battle_id[0];mw._BATTLE_SESSIONS.pop(bid)
        self.assertEqual(self.touch((18,4))[0]['result'],0);self.assertEqual(self.core_battle()[0]['result'],0);self.assertEqual(self.core_finish()[0]['result'],0)

    def test_native_unlock_and_advanced_global_boundary_fail_before_new_run(self):
        before=self.full()
        for cid in (4040107,4040401,4040501):
            self.assertNotEqual(self.enter(cid)[0]['result'],0);self.assertEqual(self.full(),before)

    def test_full_first_map_native_route_all_fourteen_battles_five_boxes(self):
        for tid,cond,need in ((40300,460,1),(40317,462,1),(160038,463,1)):
            self.db.execute('INSERT INTO task_cfg(task_id,condition,additional_parameter,need) VALUES(?,?,?,?)',(tid,cond,json.dumps([1] if cond==460 else [0]),need))
            self.db.execute('INSERT INTO task(uid,task_id,progress) VALUES(?,?,0)',(UID,tid))
        self.enter();processed=set();battles=0;boxes=[];transcript=[]
        for turn in range(180):
            e=self.engine();candidates=[]
            for key in e.native['tiles']:
                pos=tuple(map(int,key.split(',')));g=e.grid(pos);signature=(pos,g['tag'],g['status'])
                if signature in processed or e.hex(g)['interact']!=1:continue
                path=self.path_to(pos)
                if path is not None:candidates.append((len(path),pos,path,signature))
            if not candidates:break
            _,pos,path,signature=min(candidates);processed.add(signature)
            self.approach(pos)
            before=self.full();packet,response=self.touch(pos);self.assertEqual(packet['result'],0,pos)
            transcript.append(dict(pos=pos,tag=signature[1],frames=response.scs))
            if 17023 in response.scs:
                self.assertLess(response.scs.index(17023),response.scs.index(49007));boxes.append(pos)
            p=self.state().get('pending')
            if p and p['kind']=='battle':
                self.assertEqual(self.core_battle()[0]['result'],0);self.assertEqual(self.core_finish()[0]['result'],0);battles+=1
            elif p and p['kind']=='artifact':self.assertEqual(self.touch(pos,p['choices'][0])[0]['result'],0)
            elif p and p['kind']=='purify':
                # The native cancel leaves the fountain unchanged; moving
                # abandons that pending selection without marking it used.
                self.move([])
        self.assertEqual(battles,14,transcript);self.assertEqual(len(boxes),5,transcript)
        self.assertEqual(w.progress(self.db,UID,4040101),100)
        self.assertEqual({i:self.balance(i) for i in (1,2,40801,40802,40803)},{1:400,2:5500,40801:6,40802:12,40803:4})
        self.assertEqual(sum(r['num'] for r in self.db.query('SELECT num FROM warchess_box WHERE uid=?',(UID,))),5)
        self.assertEqual([r['progress'] for r in self.db.query('SELECT progress FROM task WHERE uid=? ORDER BY task_id',(UID,))],[1,1,1])
        before={i:self.balance(i) for i in (1,2,40801,40802,40803)};self.call(49008,{},49009);self.assertIsNone(self.state());self.enter()
        for pos in boxes:self.assertEqual(self.engine().grid(pos)['tag'],1)
        self.assertEqual({i:self.balance(i) for i in before},before)


if __name__=='__main__':unittest.main(verbosity=2)
