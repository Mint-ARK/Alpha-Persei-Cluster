"""A82: native index + current rotation, real Core entry/settlement, memory only."""
import ast
from collections import defaultdict
import copy
import random
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_boss_battle_period import BossBattlePeriodTests, THU
from verify_settlement_serial import SerialTests
from verify_battle_entry_fail_closed import BattleEntryFailureTests
from verify_report_small_fixes import UID
import codec
import middleware as mw
import weekly_challenge_service as weekly
from boss_battle_identity import BossIdentityError
from event_bus import bus, Events

ROOT=Path(__file__).resolve().parents[2]


class BossIdentityTests(BossBattlePeriodTests):
    def select(self,mode=101):
        self.db.set_boss_challenge_mode(UID,mode)
        return self.read()

    def pool(self):return weekly.get_boss_catalog().get('advance_pool') or weekly.get_boss_catalog()['advance_boss_pool']

    def start(self,stage,index=0,kind=2,activity=0,hero=1084):
        self.stage=stage;self.kind=kind
        packet,response=self.call(54030,dict(common_info=dict(type=100 if kind==2 else 10,dest=stage,index=index,
                                   activity_id=activity,battle_times=1,hero_list=[dict(hero_id=hero,hero_type=1)])),54031)
        if packet['result']==0:
            self.bid=self.conn.battle_id[0]
            mw.get_battle_session(self.bid)['result_data']=dict(result=1,info=dict(battle_time=45000))
        return packet,response

    def duplicate_rotation(self):
        self.select();pool=self.pool();ids=sorted(int(k) for k in pool)
        for seed in range(1000):
            selected=random.Random(seed*10007+45101).sample(ids,3)
            by_stage=defaultdict(list)
            for bid in selected:by_stage[pool[str(bid)]['stage_id']].append(bid)
            duplicate=next(((stage,bids) for stage,bids in by_stage.items() if len(bids)>1),None)
            if duplicate:
                self.db.upsert('boss_challenge_progress',UID,dict(cycle_id=seed or 1),keys=('uid',))
                if seed==0:continue
                self.assertEqual([b['id'] for b in self.read()[2]['boss_list']],selected)
                return duplicate
        self.fail('No native duplicate-stage rotation found')

    def reject_entry(self,stage,index=0,kind=2):
        before=self.state();sessions=set(mw._BATTLE_SESSIONS)
        sequence=(self.conn.battle_seq[0],self.conn.battle_id[0],self.conn.battle_stage[0])
        packet,response=self.start(stage,index,kind)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[54031]);self.assertEqual(self.state(),before)
        self.assertEqual(set(mw._BATTLE_SESSIONS),sessions)
        self.assertEqual((self.conn.battle_seq[0],self.conn.battle_id[0],self.conn.battle_stage[0]),sequence)

    def test_native_index_distinguishes_two_real_bosses_sharing_the_same_stage(self):
        import hero_codec
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1011,100),keys=('uid','id'))
        stage,bids=self.duplicate_rotation()
        for bid,hero in zip(bids,(1084,1011)):
            with self.subTest(boss=bid):
                packet,response=self.start(stage,bid,hero=hero)
                self.assertEqual(packet['result'],0)
                session=mw.get_battle_session(self.bid)
                self.assertEqual(session.get('index',0),bid)
                self.assertEqual(session['boss_context'].get('boss_id'),bid)
                with patch.object(bus,'emit',wraps=bus.emit) as events:
                    packet,response=self.finish()
                self.assertEqual(packet['result'],0)
                self.assertEqual(sum(c.args[0]==Events.STAGE_PASS for c in events.call_args_list),1)
                row=self.db.get('boss_challenge_advance',UID,'AND boss_id=?',(bid,))
                self.assertEqual(row['score'],session['boss_context']['score'])
        self.assertEqual({r['boss_id'] for r in self.db.query('SELECT boss_id FROM boss_challenge_advance')},set(bids))

    def test_zero_index_only_accepts_a_unique_current_stage(self):
        stage,bids=self.duplicate_rotation();self.reject_entry(stage)
        selected=self.read()[2]['boss_list'];counts=defaultdict(list)
        for row in selected:counts[self.pool()[str(row['id'])]['stage_id']].append(row['id'])
        unique,bid=next((stage,ids[0]) for stage,ids in counts.items() if len(ids)==1)
        packet,_=self.start(unique)
        self.assertEqual(packet['result'],0)
        self.assertEqual(mw.get_battle_session(self.bid)['boss_context']['boss_id'],bid)
        self.assertEqual(self.finish()[0]['result'],0)

    def test_unknown_unselected_and_conflicting_index_never_publish_session(self):
        data=self.select();current=[b['id'] for b in data[2]['boss_list']]
        pool=self.pool();bid=current[0];stage=pool[str(bid)]['stage_id']
        self.reject_entry(3012399,bid)
        self.reject_entry(stage,999999)
        missing=next(int(k) for k in pool if int(k) not in current)
        self.reject_entry(pool[str(missing)]['stage_id'],missing)
        other=next(b for b in current if pool[str(b)]['stage_id']!=stage)
        self.reject_entry(stage,other)
        for wrong_type in (10,53):
            with self.subTest(stage_type=wrong_type):
                before=self.state();sessions=set(mw._BATTLE_SESSIONS)
                packet,response=self.call(54030,dict(common_info=dict(type=wrong_type,dest=stage,index=bid,
                                            hero_list=[dict(hero_id=1084,hero_type=1)])),54031)
                self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54031])
                self.assertEqual(self.state(),before);self.assertEqual(set(mw._BATTLE_SESSIONS),sessions)

    def test_activity_id_cannot_steal_identity_from_native_index(self):
        data=self.select();current=[b['id'] for b in data[2]['boss_list']];pool=self.pool()
        other,target=next((a,b) for a in current for b in current if pool[str(a)]['stage_id']!=pool[str(b)]['stage_id'])
        packet,_=self.start(pool[str(target)]['stage_id'],target,activity=int(pool[str(other)]['boss_id']))
        self.assertEqual(packet['result'],0);self.assertEqual(self.finish()[0]['result'],0)
        self.assertTrue(self.db.get('boss_challenge_advance',UID,'AND boss_id=?',(target,)))
        self.assertFalse(self.db.get('boss_challenge_advance',UID,'AND boss_id=?',(other,)))

    def test_normal_unknown_or_inactive_stage_has_no_5024_fallback(self):
        data=self.select(4);groups=weekly.get_boss_catalog()['stage_groups']
        selected={b['group_id'] for b in data[1]['boss_challenge_list']}
        missing=next(v['stages'][0] for k,v in groups.items() if int(k) not in selected)
        self.reject_entry(missing,kind=1);self.reject_entry(3019999,kind=1)
        target=next(iter(selected));stage=groups[str(target)]['stages'][0]
        packet,_=self.start(stage,kind=1)
        self.assertEqual(packet['result'],0);self.assertEqual(self.finish()[0]['result'],0)
        self.assertEqual([r['group_id'] for r in self.db.query('SELECT * FROM boss_challenge_normal')],[target])

    def test_corrupted_session_identity_is_rejected_before_any_success_events(self):
        self.enter();session=mw.get_battle_session(self.bid)
        session['index']=session['boss_context']['boss_id']+99999
        before=self.state()
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            self.assert_rejected(*self.finish())
        self.assertEqual(events.call_count,0);self.assertEqual(self.state(),before)

    def test_legacy_ambiguous_session_has_no_first_boss_fallback(self):
        stage,_=self.duplicate_rotation();self.bid=98765
        mw.register_battle_session(self.bid,UID,dest=stage,heroes=[1084],stage_type=100)
        mw.get_battle_session(self.bid)['result_data']=dict(result=1,info={})
        before=self.state()
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            self.assert_rejected(*self.finish())
        self.assertEqual(events.call_count,0);self.assertEqual(self.state(),before)

    def test_score_is_frozen_from_selected_boss_and_actual_advance_mode(self):
        data=self.select(102);bid=data[2]['boss_list'][0]['id'];stage=self.pool()[str(bid)]['stage_id']
        self.db.upsert('boss_challenge_affixes',UID,dict(boss_id=bid,diffculty_index=2,affix_index_list='[]',time_index_list='[]'),keys=('uid','boss_id'))
        packet,_=self.start(stage,bid);self.assertEqual(packet['result'],0)
        expected=weekly.get_boss_catalog()['advance_modes']['102']['difficult_point'][1]
        self.assertEqual(mw.get_battle_session(self.bid)['boss_context'].get('score'),expected)
        self.db.execute('UPDATE boss_challenge_affixes SET diffculty_index=7 WHERE uid=? AND boss_id=?',(UID,bid))
        self.assertEqual(self.finish()[0]['result'],0)
        self.assertEqual(self.db.get('boss_challenge_advance',UID,'AND boss_id=?',(bid,))['score'],expected)


if __name__=='__main__':
    if '--before' in sys.argv:
        path=ROOT/'archive/boss_identity_20261104/before/v5_server/middleware.py';tree=ast.parse(path.read_text('utf-8-sig'))
        for name in ('_parse_battle_common','register_battle_session','h_54030','_boss_battle_context','_settle_battle_session','_settle_frames'):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            # Keep old definitions in one shared module namespace so their
            # references resolve to the other old helpers, not mixed versions.
            ns=vars(mw);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
        mw.CORE_HANDLERS[54030]=mw.h_54030
    suite=unittest.TestSuite(BossIdentityTests(n) for n in BossIdentityTests.__dict__ if n.startswith('test_'))
    if '--before' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(BossBattlePeriodTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SerialTests))
        # Avoid rerunning the historical reproduction test, which writes its old evidence.
        suite.addTests(BattleEntryFailureTests(n) for n in unittest.defaultTestLoader.getTestCaseNames(BattleEntryFailureTests)
                       if not n.startswith('test_before_source'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
