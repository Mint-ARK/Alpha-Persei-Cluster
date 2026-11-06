"""Memory-only native AutoChess clear rewards, medals and task packet checks."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from activity_content_service import catalog
import autochess_progress as progress
import autochess_service as ac
import codec,core,generator
from event_bus import bus, Events
from operations import _refresh_frames
from middleware import DownFrame


class CompletionProbe(core.Operation):
    cmd,sc=89126,89127
    prepare_response_in_transaction=True
    stage_id=101
    def apply(self,data,ctx):return progress.settle_completed(ctx,self.uid,self.stage_id)
    def respond(self,result,data,ctx):
        frames=_refresh_frames(ctx,self.uid,strict=True)
        frames.append(DownFrame(89201,ac.AutoChessService.get_instance(ctx.db).build_89201_payload(ctx.db,self.uid)))
        frames.append(DownFrame(89127,codec.encode('sc_89127',dict(result=0))))
        return frames


class ProgressTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.probe=CompletionProbe();binding=patch.dict(core.OPERATIONS,{89126:self.probe});binding.start();self.addCleanup(binding.stop)
        self.tasks={int(i):v for i,v in catalog()['tasks'].items() if v['activity_id'] in progress.OWNERS and v['condition'] in (300,390001,390008)}
        for i,v in self.tasks.items():
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter,reward_json) VALUES(?,?,?,?,?,?,?)',
                (i,v['type'],v['condition'],v['need'],v['activity_id'],json.dumps(v['additional_parameter']),json.dumps(v['reward'])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,i,v['type']))
        for i,k in [(1,1),(2,1),(39,1),(80,1),(40301,6),(2200105,9)]:
            self.db.execute('INSERT INTO item_catalog(id,type,category) VALUES(?,?,?)',(i,k,'currency' if k==1 else 'material'))

    def finish(self,stage):
        self.probe.stage_id=stage
        return self.call(89126,dict(game_type=0),89127)
    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']
    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('autochess_stage','autochess_medal','task','claim_ledger','currency','material')}

    def test_six_different_opponents_award_six_tokens_only_once(self):
        for n,stage in enumerate([101,102,201,202,301,302],1):
            packet,response=self.finish(stage);self.assertEqual(packet['result'],0)
            self.assertEqual(self.balance(80),n);self.assertEqual(self.value(40501011),n)
            self.assertIn(17023,response.scs);self.assertIn(28007,response.scs)
            self.assertEqual(self.finish(stage)[0]['result'],0);self.assertEqual(self.balance(80),n)
        for stage in (103,104,203,204,303,304):self.assertEqual(self.finish(stage)[0]['result'],0)
        self.assertEqual(self.balance(80),6)

    def test_first_clear_broadcast_occurs_once_ordinary_pass_each_time(self):
        emit=bus.emit_required
        with patch.object(bus,'emit_required',wraps=emit) as spy:
            self.finish(101);self.finish(101)
        self.assertEqual(sum(c.args[0]==Events.STAGE_FIRST_CLEAR for c in spy.call_args_list),1)
        self.assertEqual(sum(c.args[0]==Events.STAGE_PASS for c in spy.call_args_list),2)
        row=self.db.query('SELECT clear_time FROM autochess_stage WHERE uid=? AND stage_id=101',(UID,))[0]
        self.assertGreater(row['clear_time'],0)

    def test_medal_1004_requires_all_three_hard_bosses(self):
        self.finish(204);self.finish(104)
        self.assertFalse(self.db.query('SELECT * FROM autochess_medal WHERE medal_id=1004'))
        self.finish(304)
        rows=self.db.query('SELECT * FROM autochess_medal WHERE medal_id=1004')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['level'],1)
        self.assertEqual([self.value(i) for i in (40501015,40501016,40501017)],[1,2,3])
        self.finish(304);self.assertEqual(self.value(40501017),3)

    def test_old_higher_medal_and_timestamps_never_downgrade(self):
        self.db.execute('INSERT INTO autochess_medal(uid,medal_id,level,unlock_time,upgrade_time) VALUES(?,1001,2,100,200)',(UID,))
        self.finish(103);self.finish(103)
        row=self.db.query('SELECT * FROM autochess_medal WHERE medal_id=1001')[0]
        self.assertEqual((row['level'],row['unlock_time'],row['upgrade_time']),(2,100,200))

    def test_native_390008_three_gym_tasks_progress(self):
        for n,s in enumerate([103,203,303],1):
            self.finish(s);self.assertEqual(self.value(40501014),n);self.assertEqual(self.value(40502009),n)
        self.finish(104);self.finish(204);self.finish(304)
        self.assertEqual(self.value(40501014),3)

    def test_late_task_activation_restores_from_clear_facts_not_owned_balance(self):
        self.db.execute('DELETE FROM task WHERE task_id=40501017')
        for s in (104,204,304):self.finish(s)
        self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,40501017,3002)',(UID,))
        changed=progress.project_tasks(self.db,UID)
        self.assertEqual(self.value(40501017),3);self.assertEqual(changed,[dict(id=40501017,progress=3,complete_flag=0)])
        self.finish(101);self.db.set_currency(UID,80,0)
        self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=40502001',(UID,))
        progress.project_tasks(self.db,UID);self.assertEqual(self.value(40502001),1)

    def test_unknown_multiplayer_and_unrecorded_rows_do_not_advance(self):
        before=self.state();self.assertNotEqual(self.finish(401)[0]['result'],0);self.assertEqual(self.state(),before)
        self.db.execute('INSERT INTO autochess_stage(uid,stage_id,clear_time) VALUES(?,401,100)',(UID,))
        self.db.execute('INSERT INTO autochess_stage(uid,stage_id,clear_time) VALUES(?,104,0)',(UID,))
        self.assertEqual(progress.project_tasks(self.db,UID),[])
        self.assertEqual(self.value(40501015),0)

    def test_claimed_expired_and_other_activity_tasks_are_untouched(self):
        self.db.execute('UPDATE task SET complete_flag=1,claimed_ts=100 WHERE task_id=40501015')
        self.db.execute('UPDATE task SET expired_ts=1 WHERE task_id=40501016')
        self.db.execute('UPDATE task_cfg SET activity_id=999 WHERE task_id=40501017')
        for s in (104,204,304):self.finish(s)
        self.assertEqual([self.value(i) for i in (40501015,40501016,40501017)],[0,0,0])

    def test_token_task_reward_claim_and_repeat_do_not_double_assets(self):
        self.finish(101)
        packet,response=self.call(28010,dict(id=40502001),28011)
        self.assertEqual(packet['result'],0);self.assertIn(17023,response.scs)
        self.assertEqual(self.balance(1),60)
        again,_=self.call(28010,dict(id=40502001),28011)
        self.assertEqual(self.balance(1),60)

    def test_real_core_stage_victory_sends_inventory_and_task_delta_once(self):
        with patch.dict(core.OPERATIONS,{89126:ac.AutoChessSettleBattleOp()}):
            self.assertEqual(self.call(89116,dict(game_type=0,other_param=101),89117)[0]['result'],0)
            svc=ac.AutoChessService.get_instance(self.db)
            state=svc.get_session(UID)
            state['board_chess']=[dict(unique_id=100500,chess_id=101,index=1,star=1,exp=1,atk=10000,hp=10000)]
            svc.save_session(UID)
            for i in range(2):
                self.assertEqual(self.call(89144,dict(game_type=0),89145)[0]['result'],0)
                self.assertEqual(self.call(89108,dict(game_type=0),89109)[0]['result'],0)
                packet,response=self.call(89126,dict(game_type=0),89127)
                self.assertEqual(packet['result'],0)
            self.assertIn(17023,response.scs);self.assertIn(28007,response.scs)
            self.assertEqual(response.scs.count(17023),1)
            changed=[codec.decode(f['payload'],'sc_28007') for f in response.frames if f['sc']==28007]
            self.assertTrue(any(any(t['id']==40502001 and t['progress']==1 for t in p['progress_list']) for p in changed))
            before=self.state();self.assertNotEqual(self.call(89126,dict(game_type=0),89127)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertEqual(self.balance(80),1)

    def test_failure_after_grant_rolls_back_stage_token_and_tasks(self):
        from inventory_service import InventoryService
        real=InventoryService.grant_items;before=self.state()
        def fail(*a,**kw):real(*a,**kw);raise RuntimeError('after-grant fault')
        with patch.object(InventoryService,'grant_items',side_effect=fail):self.assertNotEqual(self.finish(101)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.finish(101)[0]['result'],0);self.assertEqual(self.balance(80),1)

    def test_required_task_or_inventory_delta_failure_rolls_back(self):
        for target in (17023,28007):
            before=self.state()
            if target==17023:
                real=generator.gen_payload
                scope=patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else real(cmd,**kw))
            else:
                ctx=self.conn.to_handler_ctx();real=ctx.codec_encode
                scope=patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name=='sc_28007' else real(name,data))
            with scope:self.assertNotEqual(self.finish(101)[0]['result'],0)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.finish(101)[0]['result'],0)


if __name__=='__main__':unittest.main(verbosity=2)
