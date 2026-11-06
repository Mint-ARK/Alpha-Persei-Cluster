"""Observed Mimir dispatch bugs: memory Core, native codec and task recovery."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests,UID,NOW
import admin_cat_dispatch_progress as progress
import admin_cat_explore_service as cat
import codec
import generator
import new_player_service
from event_bus import bus,Events


class DispatchTests(MemoryTests):
    def setUp(self):
        super().setUp();self.now=NOW
        clock=patch('time.time',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)
        self.svc=cat.AdminCatExploreService.get_instance();self.svc.get_or_create_explore_data(self.db,UID)
        for tid,kind,param in ((6014,6,'[0]'),(160145,603,'')):
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,condition,need,additional_parameter,reward_json) VALUES(?,?,9,1,?,?)',
                            (tid,kind,param,json.dumps([[1,10]])))
            self.db.execute('INSERT INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,tid,kind))

    def dispatch(self,area=10001,mimir=10001,hour=4):
        return self.call(67004,dict(area_id=area,mimir_id=mimir,hour_time=hour),67005)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('admin_cat_list','admin_cat_explore_data','admin_cat_explore_queue','ops_common','task','currency','material')}

    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']

    def test_all_served_native_condition9_parameters_are_exact_dispatch_tasks(self):
        from extract_autochess_cfg import parse_lua_file
        root=Path(__file__).resolve().parents[2]
        for path in (root/'decompiled_v2/x64/game/config/assignmentcfg.lua',root/'v5_server/tools/diff_lua_decompiled_311/AssignmentCfg.lua'):
            rows=parse_lua_file(path)
            actual={tid:(r['type'],r['need'],r['additional_parameter']) for tid,r in rows.items()
                    if type(tid)is int and r.get('condition')==9}
            self.assertEqual(actual,{6014:(6,1,[0]),160145:(603,1,'')})

    def test_state_precedes_ack_so_native_refresh_reads_new_queue(self):
        packet,response=self.dispatch();self.assertEqual(packet['result'],0)
        self.assertLess(response.scs.index(67001),response.scs.index(67005))
        state=next(codec.decode(f['payload'],'sc_67001') for f in response.frames if f['sc']==67001)
        queue=state['explore_queue'];self.assertEqual(len(queue),1)
        self.assertEqual((queue[0]['area_id'],queue[0]['mimir_id'],queue[0]['target_explore_hour']),(10001,10001,4))
        self.assertEqual(queue[0]['stop_time']-queue[0]['start_time'],14400)
        cold=codec.decode(generator.gen_payload(67001,uid=UID,db=self.db),'sc_67001')
        self.assertEqual(cold['explore_queue'],queue)

    def test_success_event_once_updates_daily_and_onboarding_and_repeat_no_double_count(self):
        with patch.object(bus,'emit_required',wraps=bus.emit_required) as emitted:
            packet,response=self.dispatch();self.assertEqual(packet['result'],0)
            self.assertEqual(emitted.call_count,1);self.assertEqual(emitted.call_args.args[0],Events.ADMIN_CAT_EXPLORE_START)
            self.assertEqual((self.value(6014),self.value(160145)),(1,1))
            delta=next(codec.decode(f['payload'],'sc_28007') for f in response.frames if f['sc']==28007)
            self.assertEqual({r['id']:r['progress'] for r in delta['progress_list']},{6014:1,160145:1})
            before=self.state();packet,response=self.dispatch()
            self.assertNotEqual(packet['result'],0);self.assertEqual(emitted.call_count,1)
            self.assertEqual(self.state(),before);self.assertEqual(progress.read(self.db,UID)['total'],1)

    def test_invalid_dispatch_never_counts_or_broadcasts(self):
        with patch.object(bus,'emit_required',wraps=bus.emit_required) as emitted:
            before=self.state()
            for kwargs in (dict(hour=0),dict(hour=5),dict(area=10002),dict(mimir=999)):
                self.assertNotEqual(self.dispatch(**kwargs)[0]['result'],0)
                self.assertEqual(self.state(),before)
            self.assertEqual(emitted.call_count,0)

    def test_two_distinct_legal_dispatches_count_once_each_without_extra_task_rewards(self):
        self.db.set_currency(UID,53,2400)
        self.db.unlock_admin_cat(UID,10002,skill_level=1)
        self.assertEqual(self.dispatch()[0]['result'],0)
        packet,response=self.dispatch(area=10002,mimir=10002,hour=8)
        self.assertEqual(packet['result'],0);self.assertEqual(progress.read(self.db,UID)['total'],2)
        self.assertEqual(progress.read(self.db,UID)['daily'],2)
        self.assertEqual((self.value(6014),self.value(160145)),(1,1));self.assertNotIn(28007,response.scs)

    def test_equipment_reforge_no_longer_completes_dispatch_tasks(self):
        ctx=self.conn.to_handler_ctx();before=self.state()
        bus.emit_required(Events.EQUIP_REFORGE,ctx,UID,times=1)
        self.assertEqual((self.value(6014),self.value(160145)),(0,0));self.assertEqual(self.state(),before)

    def test_fresh_and_regular_task_recovery_uses_saved_fact_without_online_deltas(self):
        self.dispatch();self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id IN (6014,160145)',(UID,))
        with patch.object(new_player_service,'enabled',return_value=False):
            raw=generator.gen_payload(28001,uid=UID,db=self.db)
        packet=codec.decode(raw,'sc_28001');values={r['id']:r['progress'] for r in packet['assignment_list']}
        self.assertEqual((values[6014],values[160145]),(1,1))
        self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id IN (6014,160145)',(UID,))
        # Execute the real fresh-save28001 branch and its complete projections.
        self.db.execute('INSERT INTO newbie_activity(uid,trigger_time) VALUES(?,?)',(UID,self.now))
        new_player_service.payload(self.db,UID,28001)
        self.assertEqual((self.value(6014),self.value(160145)),(1,1))
        before=self.state();progress.project_tasks(self.db,UID);self.assertEqual(self.state(),before)

    def test_old_queue_and_daily_timestamp_recover_only_proven_minimum(self):
        self.db.save_admin_cat_queue(UID,10001,10001,self.now,self.now+14400,4,[])
        facts=progress.read(self.db,UID);self.assertEqual((facts['total'],facts['daily']),(1,1))
        progress.project_tasks(self.db,UID);self.assertEqual((self.value(6014),self.value(160145)),(1,1))
        self.assertEqual(self.db.query('SELECT * FROM ops_common WHERE kind=?',(progress.KIND,)),[])

    def test_old_history_from_prior_game_day_does_not_award_today_daily_task(self):
        old=self.svc.get_or_create_explore_data(self.db,UID);old['daily_time']=progress.get_daily_5am_ts(self.now)-1
        self.db.save_admin_cat_explore_data(UID,old)
        progress.project_tasks(self.db,UID);self.assertEqual((self.value(6014),self.value(160145)),(0,1))

    def test_no_legacy_evidence_does_not_invent_past_dispatch(self):
        before=self.state();self.assertEqual(progress.project_tasks(self.db,UID),[])
        self.assertEqual((self.value(6014),self.value(160145)),(0,0));self.assertEqual(self.state(),before)

    def test_current_game_day_counter_resets_only_at_5am(self):
        boundary=progress.get_daily_5am_ts(self.now)+86400
        self.now=boundary-60;self.dispatch()
        self.now=boundary-1;self.assertEqual(progress.read(self.db,UID)['daily'],1)
        self.now=boundary;self.assertEqual(progress.read(self.db,UID)['daily'],0)
        self.assertEqual(progress.read(self.db,UID)['total'],1)

    def test_claimed_permanent_or_daily_tasks_are_not_reopened(self):
        self.db.execute('UPDATE task SET complete_flag=1,claimed_ts=? WHERE uid=?',(self.now,UID))
        before=self.db.query('SELECT * FROM task');packet,response=self.dispatch()
        self.assertEqual(packet['result'],0);self.assertNotIn(28007,response.scs)
        self.assertEqual(self.db.query('SELECT * FROM task'),before)

    def test_each_required_frame_or_ledger_failure_rolls_back_dispatch_and_tasks(self):
        for target in ('sc_67001','sc_67005','sc_28007','record'):
            before=self.state();ctx=self.conn.to_handler_ctx()
            if target=='record':
                real=progress.record
                def fail(*a,**kw):real(*a,**kw);raise RuntimeError('after dispatch record')
                change=patch.object(progress,'record',side_effect=fail)
            else:
                real=ctx.codec_encode
                change=patch.object(ctx,'codec_encode',side_effect=lambda name,*a,**kw:None if name==target else real(name,*a,**kw))
            with change:packet,response=self.dispatch()
            self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[67005]);self.assertEqual(self.state(),before)
        packet,response=self.dispatch();self.assertEqual(packet['result'],0)
        self.assertEqual(progress.read(self.db,UID)['total'],1)


if __name__=='__main__':unittest.main(verbosity=2)
