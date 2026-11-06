"""RogueCard task statistics through native RPCs; all databases are in memory."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import verify_rogue_card_lifecycle as lifecycle
from verify_report_small_fixes import UID,NOW
import account_db
import codec
import core
import generator
from event_bus import bus,Events
import minigame_service as mini
import new_player_service
import rogue_card_progress as progress
import rogue_card_service as rogue
import task_listener

CATALOG=json.loads(Path(__file__).resolve().parents[1].joinpath('data/activity_content_cfg.json').read_text('utf-8'))['versions']['229']['tasks']


class RogueCardTaskTests(lifecycle.RogueCardTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        for row in CATALOG.values():
            if 411009<=row['condition']<=411018:self.add_task(row)

    def add_task(self,row,db=None):
        db=db or self.db
        db.execute('INSERT OR REPLACE INTO task_cfg(task_id,task_type,condition,need,activity_id,additional_parameter) VALUES(?,?,?,?,?,?)',
            (row['id'],row.get('type',3002),row['condition'],row['need'],row.get('activity_id',4100411),json.dumps(row.get('additional_parameter',[]))))
        db.execute('INSERT OR IGNORE INTO task(uid,task_id,task_type) VALUES(?,?,?)',(UID,row['id'],row.get('type',3002)))
    def value(self,tid):return self.db.query('SELECT progress FROM task WHERE uid=? AND task_id=?',(UID,tid))[0]['progress']
    def info(self,**overrides):
        return dict(dict(use_card_num=20,discard_num=7,use_gold_num=11,use_enhance_num=2,gain_joker_num=4,
            use_a_num=3,hand_type_contain_num=[1,2,3,4,5,6]),**overrides)
    def win(self,info=None):return self.call_rc(89616,dict(deck=1,diff=1,info=self.info() if info is None else info))
    def stats(self):
        rows=self.db.query('SELECT value FROM server_global_config WHERE key=?',(progress.progress_key(UID),))
        return json.loads(rows[0]['value'])['statistics'] if rows else {}

    def test_all_scalar_and_reverse_hand_containment_counters_have_task_deltas(self):
        self.challenge();packet,response=self.win()
        self.assertEqual(packet['result'],0);self.assertIn(28007,response.scs)
        self.assertEqual([self.value(i) for i in range(50005120,50005128)],[20,7,11,2,4,3,4,3])
        changed={r['id']:r['progress'] for frame in response.frames if frame['sc']==28007
                 for r in codec.decode(frame['payload'],'sc_28007')['progress_list']}
        self.assertEqual(changed[50005126],4);self.assertEqual(changed[50005127],3)
        self.assertLess(response.scs.index(28007),response.scs.index(89617))
        before=self.state();_,response=self.win()
        self.assertEqual(self.state(),before);self.assertNotIn(28007,response.scs)

    def test_deck_and_minimum_difficulty_are_conjunctive_with_zero_wildcards(self):
        self.add_task(dict(id=91001,condition=411009,need=10,additional_parameter=[1,2]))
        self.add_task(dict(id=91002,condition=411009,need=10,additional_parameter=[2,1]))
        self.challenge();self.win({})
        self.assertEqual(self.value(50005128),1);self.assertEqual(self.value(50005129),1)
        self.assertEqual(self.value(91001),0);self.assertEqual(self.value(91002),0)
        self.challenge(diff=2)
        self.call_rc(89616,dict(deck=1,diff=2,info={}))
        self.assertEqual(self.value(91001),1);self.assertEqual(self.value(91002),0)
        rec=self.rec();rec['finish_thread_list']=list(range(101,107));self.svc.save_rogue_card(self.db,UID,rec)
        self.challenge(deck=2)
        self.call_rc(89616,dict(deck=2,diff=1,info={}))
        self.assertEqual(self.value(91002),1);self.assertEqual(self.value(150512),1)
        self.assertEqual(self.value(150513),0);self.assertEqual(self.value(50005129),3)

    def test_only_distinct_miscellaneous_posts_count_not_story_hot_or_repeats(self):
        rec=self.rec();rec['finish_thread_list']=list(range(101,108));self.svc.save_rogue_card(self.db,UID,rec)
        misc=[p['id'] for p in rogue.CFG['posts'].values() if p['type']==3]
        other=[p['id'] for p in rogue.CFG['posts'].values() if p['type']!=3]
        self.assertEqual(len(misc),13)
        for tid in other:self.assertEqual(self.call_rc(89610,dict(thread_id=tid))[0]['result'],0)
        self.assertEqual(self.value(50005119),0)
        for index,tid in enumerate(misc,1):
            self.assertEqual(self.call_rc(89610,dict(thread_id=tid))[0]['result'],0)
            self.assertEqual(self.value(50005119),index)
        before=self.state();_,response=self.call_rc(89610,dict(thread_id=misc[0]))
        self.assertNotIn(28007,response.scs);self.assertEqual(self.state(),before)

    def test_saves_and_rollback_do_not_count_and_interrupt_counts_last_snapshot_once(self):
        bid=self.begin()
        first=self.saved();first['settle_data']=self.info(use_card_num=5)
        later=self.saved();later['settle_data']=self.info(use_card_num=9)
        self.save(bid,1,first);self.save(bid,1,later)
        self.call_rc(89626,dict(battle_id=bid,battle_type=1,save_rollback=first))
        self.assertEqual(self.value(50005120),0)
        self.assertEqual(self.call_rc(89624,dict(battle_id=bid,battle_type=1))[0]['result'],0)
        self.assertEqual(self.value(50005120),9);self.assertEqual(self.value(50005129),0)
        before=self.state();self.call_rc(89624,dict(battle_id=bid,battle_type=1))
        self.assertEqual(self.state(),before)

    def test_replaying_completed_post_in_a_new_run_counts_new_actions(self):
        for number in (1,2):
            self.begin()
            self.assertEqual(self.call_rc(89612,dict(thread_id=101,joker_result=1,info=self.info()))[0]['result'],0)
            self.assertEqual(self.value(50005120),20*number)
        self.assertEqual(self.rec()['finish_thread_list'],[101])

    def test_legacy_task_progress_is_adopted_then_new_actions_count_immediately(self):
        self.db.execute('UPDATE task SET progress=100 WHERE uid=? AND task_id=50005120',(UID,))
        self.db.execute('UPDATE task SET progress=1 WHERE uid=? AND task_id=50005129',(UID,))
        self.challenge();self.win()
        self.assertEqual(self.value(50005120),120);self.assertEqual(self.value(50005129),2)
        self.challenge();self.win()
        self.assertEqual(self.value(50005120),140);self.assertEqual(self.value(50005129),3)

    def test_late_created_tasks_recover_from_recorded_totals_without_guessing_old_wins(self):
        self.db.execute('DELETE FROM task WHERE uid=? AND task_id IN (50005120,50005129)',(UID,))
        self.challenge();self.win()
        for tid in (50005120,50005129):self.add_task(CATALOG[str(tid)])
        cold=codec.decode(generator.gen_payload(28001,uid=UID,db=self.db),'sc_28001')
        tasks={r['id']:r['progress'] for r in cold['assignment_list']}
        self.assertEqual(tasks[50005120],20);self.assertEqual(tasks[50005129],1)
        before=self.state();self.assertFalse(progress.restore_for_login(self.db,UID));self.assertEqual(self.state(),before)

    def test_fresh_task_snapshot_recovers_the_same_authoritative_totals(self):
        self.challenge();self.win()
        self.db.execute('UPDATE task SET progress=0 WHERE uid=? AND task_id=50005120',(UID,))
        self.db.execute('INSERT INTO newbie_activity(uid,trigger_time,completed_time) VALUES(?,?,?)',(UID,NOW-1,NOW+86400*60))
        data=new_player_service.payload(self.db,UID,28001,res_version='321')
        rows={r['id']:r['progress'] for r in data['assignment_list']}
        self.assertEqual(rows[50005120],20)

    def test_claimed_expired_and_malformed_parameter_tasks_do_not_change(self):
        self.db.execute('UPDATE task SET complete_flag=1 WHERE uid=? AND task_id=50005120',(UID,))
        self.db.execute('UPDATE task SET expired_ts=? WHERE uid=? AND task_id=50005121',(NOW-1,UID))
        for tid,raw in ((91011,[1]),(91012,[1,2,3]),(91013,[-1,1]),(91014,[[1],2])):
            self.add_task(dict(id=tid,condition=411009,need=2,additional_parameter=raw))
        self.challenge();self.win()
        self.assertEqual(self.value(50005120),0);self.assertEqual(self.value(50005121),0)
        self.assertEqual([self.value(i) for i in range(91011,91015)],[0]*4)

    def test_task_write_and_difference_packet_failures_roll_back_run_and_statistics(self):
        self.challenge();before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_28007' else encode(n,d)):
            packet,response=self.win();self.assertNotEqual(packet['result'],0);self.assertNotIn(28007,response.scs)
        self.assertEqual(self.state(),before)
        execute=self.db.execute
        def fail_after_write(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if sql.startswith('UPDATE task SET progress='):raise RuntimeError('isolated task write')
            return result
        with patch.object(self.db,'execute',side_effect=fail_after_write):
            self.assertNotEqual(self.win()[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.win()[0]['result'],0);self.assertEqual(self.value(50005120),20)

    def test_required_listener_body_typeerror_runs_once_and_rolls_back(self):
        self.challenge();before=self.state();called=[]
        def failing(ctx,uid,**kwargs):
            called.append(1);task_listener.on_rogue_card_progress(ctx,uid,**kwargs)
            raise TypeError('subscriber body failure')
        with patch.dict(bus._handlers,{Events.ROGUE_CARD_PROGRESS:[failing]}):
            self.assertNotEqual(self.win()[0]['result'],0)
        self.assertEqual(called,[1]);self.assertEqual(self.state(),before)

    def test_invalid_negative_or_truncated_containment_stats_are_rejected(self):
        self.challenge();before=self.state()
        for info in (self.info(use_card_num=-1),self.info(hand_type_contain_num=[1,2]),self.info(hand_type_contain_num=[-1]*6)):
            self.assertNotEqual(self.win(info)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_same_uid_other_database_does_not_share_statistics(self):
        self.challenge();self.win()
        other=account_db.AccountDB.new_save_workspace();self.addCleanup(other.close)
        other.execute('INSERT INTO users(uid,nick) VALUES(?,?)',(UID,'另一档'))
        for row in CATALOG.values():
            if 411009<=row['condition']<=411018:self.add_task(row,other)
        self.assertFalse(progress.restore_for_login(other,UID))
        self.assertEqual(other.query('SELECT progress FROM task WHERE uid=? AND task_id=50005120',(UID,))[0]['progress'],0)
        self.assertEqual(self.value(50005120),20)


if __name__=='__main__':
    # Inherited lifecycle cases are meaningful regression coverage for this edit.
    unittest.main(verbosity=2)
