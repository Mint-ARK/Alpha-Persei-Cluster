"""A83: real Core/SQL/codec and bounded Lua reset/restore projection, memory only."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
from verify_boss_reward_guard import BossRewardTests
import test_commit_recovery as commit_checks
from test_commit_recovery import deny_once
import account_db
import codec
import core
import operations
import weekly_challenge_service as weekly
from boss_challenge_reset import BossResetError

ROOT=Path(__file__).resolve().parents[2]


class BossResetTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now=NOW
        frozen=patch('time.time',side_effect=lambda:self.now);frozen.start();self.addCleanup(frozen.stop)
        self.db.check_and_lazy_refresh_boss_challenge(UID,self.now)
        self.db.set_boss_challenge_mode(UID,101)
        data=self.snapshot()
        self.targets={45106:data[2]['boss_list'][0]['id'],45006:data[1]['boss_challenge_list'][0]['group_id']}
        self.other=data[2]['boss_list'][1]['id']
        self.db.record_boss_challenge_advance_clear(UID,self.targets[45106],9000,[1084])
        self.db.record_boss_challenge_advance_clear(UID,self.other,11000,[1011])
        self.db.save_boss_challenge_hero_team(UID,2,self.targets[45106],[1084,0,0])
        self.db.save_boss_challenge_affixes(UID,self.targets[45106],[99400],[1],2)
        gid=self.targets[45006];stage=weekly.get_boss_catalog()['stage_groups'][str(gid)]['stages'][0]
        self.db.record_boss_challenge_normal_clear(UID,gid,stage,[1,1,1],[1075])

    def snapshot(self):return weekly.get_boss_challenge_data(UID,self.db,strict=True)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('users','currency','material','boss_challenge_progress','boss_challenge_advance',
                 'boss_challenge_scores','boss_challenge_normal','boss_challenge_affixes','boss_challenge_claimed_rewards')}

    def request(self,cmd,target=None):
        if target is None:target=self.targets.get(cmd)
        data={} if cmd==45204 else {45006:'group_id',45106:'boss_id'}[cmd]
        if cmd!=45204:data={data:target}
        return self.call(cmd,data,{45006:45007,45106:45107,45204:45205}[cmd])

    def view(self,snapshot):
        overview,normal,advance=snapshot
        return dict(mode=overview['mode'],claimed=set(advance['receive_point_list']),
            advance={b['id']:dict(score=b['max_point'],locked=list(b['used_heroes'])) for b in advance['boss_list']},
            normal={b['group_id']:dict(stars=copy.deepcopy(b['star_info']),locked=list(b['used_heroes']),finish=b['finish_stage']) for b in normal['boss_challenge_list']})

    def project(self,cmd,response,before):
        client=self.view(before);sc={45006:45007,45106:45107,45204:45205}[cmd]
        for frame in response.frames:
            number=frame['sc']
            if number==sc:
                # Native callbacks clear first, regardless of the ACK result.
                if cmd==45106:client['advance'][self.targets[cmd]]=dict(score=0,locked=[])
                elif cmd==45006:client['normal'][self.targets[cmd]]=dict(stars=[],locked=[],finish=0)
                else:client.update(mode=0,advance={},claimed=set())
            elif number==45201:client['mode']=codec.decode(frame['payload'],'sc_45201')['mode']
            elif number==45101:
                data=codec.decode(frame['payload'],'sc_45101')
                client['advance']={b['id']:dict(score=b['max_point'],locked=list(b.get('used_heroes',[]))) for b in data['boss_list']}
                client['claimed']=set(data.get('receive_point_list',[]))
            elif number==45001:
                data=codec.decode(frame['payload'],'sc_45001')
                client['normal']={b['group_id']:dict(stars=b.get('star_info',[]),locked=list(b.get('used_heroes',[])),finish=b['finish_stage']) for b in data['boss_challenge_list']}
        authoritative=self.view(self.snapshot())
        self.assertEqual(client['mode'],authoritative['mode'])
        if client['mode']==2:
            self.assertEqual(client['advance'],authoritative['advance']);self.assertEqual(client['claimed'],authoritative['claimed'])
        if client['mode']==1:
            # Lua represents reset stages by three zero stars; server uses [].
            for gid,row in client['normal'].items():
                expected=authoritative['normal'][gid]
                self.assertEqual((row['locked'],row['finish']),(expected['locked'],expected['finish']))
                self.assertEqual(sum(sum(v==1 for v in s['star_list']) for s in row['stars']),
                                 sum(sum(v==1 for v in s['star_list']) for s in expected['stars']))

    def assert_failure(self,cmd,packet,response,before,code=None,snapshot=None):
        self.assertNotEqual(packet['result'],0)
        if code is not None:self.assertEqual(packet['result'],code)
        self.assertEqual(response.scs,[{45006:45007,45106:45107,45204:45205}[cmd],45201,45001,45101])
        self.assertEqual(self.state(),before)
        if snapshot:self.project(cmd,response,snapshot)

    def test_claimed_advance_single_and_whole_reject_and_restore_after_ack(self):
        self.db.claim_boss_challenge_reward(UID,2,7000)
        for cmd in (45106,45204):
            with self.subTest(cmd=cmd):
                before=self.state();snap=self.snapshot()
                self.assert_failure(cmd,*self.request(cmd),before,2013,snap)

    def test_claimed_normal_single_and_shared_mode_button_are_also_protected(self):
        self.db.set_boss_challenge_mode(UID,4);self.db.claim_boss_challenge_reward(UID,1,3)
        for cmd in (45006,45204):
            with self.subTest(cmd=cmd):
                before=self.state();snap=self.snapshot()
                self.assert_failure(cmd,*self.request(cmd),before,2013,snap)

    def test_unclaimed_single_resets_only_target_and_keeps_presets_affixes(self):
        for cmd in (45106,45006):
            with self.subTest(cmd=cmd):
                self.db.set_boss_challenge_mode(UID,101 if cmd==45106 else 4)
                snap=self.snapshot();before=self.state();packet,response=self.request(cmd)
                self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[45107 if cmd==45106 else 45007])
                self.project(cmd,response,snap)
                self.assertEqual(self.state()['boss_challenge_affixes'],before['boss_challenge_affixes'])
                if cmd==45106:
                    row=self.db.get('boss_challenge_advance',UID,'AND boss_id=?',(self.targets[cmd],))
                    self.assertEqual((row['score'],json.loads(row['used_heroes'])),(0,[]))
                    self.assertEqual(json.loads(row['last_heroes_cfg']),[1084,0,0])
                    self.assertEqual(self.db.get('boss_challenge_advance',UID,'AND boss_id=?',(self.other,))['score'],11000)
                self.assertFalse(self.db.query('SELECT * FROM boss_challenge_claimed_rewards'))

    def test_unclaimed_whole_reset_sets_mode_zero_and_repeat_is_idempotent(self):
        snap=self.snapshot();packet,response=self.request(45204)
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[45205]);self.project(45204,response,snap)
        self.assertTrue(all(r['score']==0 and r['used_heroes']=='[]' for r in self.db.query('SELECT * FROM boss_challenge_advance')))
        self.assertTrue(all(r['score']==0 for r in self.db.query('SELECT * FROM boss_challenge_scores')))
        before=self.state();self.assertEqual(self.request(45204)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_unknown_inactive_or_wrong_mode_target_is_rejected(self):
        for cmd,target in ((45106,999999),(45006,self.targets[45006])):
            with self.subTest(cmd=cmd,target=target):
                before=self.state();snap=self.snapshot()
                self.assert_failure(cmd,*self.request(cmd,target),before,2,snap)
        self.db.set_boss_challenge_mode(UID,4)
        before=self.state();self.assert_failure(45106,*self.request(45106),before,2,self.snapshot())

    def test_claim_query_failure_is_not_empty_and_recovery_rereads_after_rollback(self):
        query=self.db.query;before=self.state();snap=self.snapshot();calls=[]
        def once(sql,*args,**kwargs):
            if 'FROM boss_challenge_claimed_rewards' in sql and not calls:
                calls.append(sql);raise RuntimeError('isolated claim read')
            return query(sql,*args,**kwargs)
        with patch.object(self.db,'query',side_effect=once):packet,response=self.request(45106)
        self.assert_failure(45106,packet,response,before,9,snap)

    def test_each_sql_failure_rolls_back_both_score_tables_and_mode(self):
        execute=self.db.execute
        for cmd in (45106,45204):
            for table in ('boss_challenge_advance','boss_challenge_scores','boss_challenge_progress'):
                if cmd==45106 and table=='boss_challenge_progress':continue
                with self.subTest(cmd=cmd,table=table):
                    before=self.state();snap=self.snapshot()
                    def fail(sql,*args,**kwargs):
                        result=execute(sql,*args,**kwargs)
                        if (sql.startswith('UPDATE '+table) or (table=='boss_challenge_progress' and sql.startswith('INSERT INTO '+table))):
                            raise RuntimeError('isolated write after '+table)
                        return result
                    with patch.object(self.db,'execute',side_effect=fail):packet,response=self.request(cmd)
                    self.assert_failure(cmd,packet,response,before,9,snap)

    def test_empty_or_raised_ack_rolls_back_all_three_resets_and_restores(self):
        for cmd in (45006,45106,45204):
            for raised in (False,True):
                with self.subTest(cmd=cmd,raised=raised):
                    self.db.set_boss_challenge_mode(UID,4 if cmd==45006 else 101)
                    before=self.state();snap=self.snapshot();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
                    name='sc_'+str({45006:45007,45106:45107,45204:45205}[cmd])
                    def fail(msg,data):
                        if msg==name:
                            if raised:raise RuntimeError('isolated reset ACK error')
                            return None
                        return encode(msg,data)
                    with patch.object(ctx,'codec_encode',side_effect=fail):packet,response=self.request(cmd)
                    self.assert_failure(cmd,packet,response,before,1,snap)

    def test_commit_refusal_restores_database_and_projects_after_failure_ack(self):
        before=self.state();snap=self.snapshot();connection=self.db._conn()
        deny_once(connection,'COMMIT')
        try:packet,response=self.request(45204)
        finally:connection.set_authorizer(None)
        self.assert_failure(45204,packet,response,before,9,snap)

    def test_database_methods_also_guard_claims_and_nested_reset_failure(self):
        self.db.claim_boss_challenge_reward(UID,2,7000);before=self.state()
        for call in (lambda:self.db.reset_boss_challenge_advance_boss(UID,self.targets[45106]),
                     lambda:self.db.reset_boss_challenge_advance_all(UID)):
            with self.assertRaises(BossResetError):call()
            self.assertEqual(self.state(),before)
        self.db.execute('DELETE FROM boss_challenge_claimed_rewards')
        execute=self.db.execute;before=self.state()
        def fail(sql,*args,**kwargs):
            result=execute(sql,*args,**kwargs)
            if sql.startswith('UPDATE boss_challenge_scores'):raise RuntimeError('isolated nested reset')
            return result
        with self.db.transaction():
            self.db.execute("UPDATE users SET nick='outer-kept' WHERE uid=?",(UID,))
            with patch.object(self.db,'execute',side_effect=fail):
                with self.assertRaises(RuntimeError):self.db.reset_boss_challenge_advance_boss(UID,self.targets[45106])
        for table in ('boss_challenge_advance','boss_challenge_scores'):self.assertEqual(self.state()[table],before[table])
        self.assertEqual(self.db.get('users',UID)['nick'],'outer-kept')

    def test_old_claims_do_not_block_new_cycle_reset(self):
        self.db.claim_boss_challenge_reward(UID,2,7000)
        self.now+=604800
        packet,response=self.request(45204)
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[45205])
        self.assertFalse(self.db.query('SELECT * FROM boss_challenge_claimed_rewards'))

    def test_persistent_recovery_read_or_encoding_fault_keeps_error_ack_no_partial_state(self):
        self.db.claim_boss_challenge_reward(UID,2,7000)
        for kind in ('read','encode'):
            before=self.state()
            if kind=='read':
                query=self.db.query
                def fail(sql,*a,**kw):
                    if 'FROM boss_challenge_claimed_rewards' in sql:raise RuntimeError('isolated persistent read')
                    return query(sql,*a,**kw)
                fault=patch.object(self.db,'query',side_effect=fail)
            else:
                ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
                fault=patch.object(ctx,'codec_encode',side_effect=lambda name,data:None if name=='sc_45101' else encode(name,data))
            with fault:packet,response=self.request(45106)
            self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[45107]);self.assertEqual(self.state(),before)


if __name__=='__main__':
    if '--before' in sys.argv:
        base=ROOT/'archive/boss_reset_guard_20261104/before/v5_server'
        path=base/'account_db.py';tree=ast.parse(path.read_text('utf-8-sig'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AccountDB')
        for name in ('reset_boss_challenge_normal_boss','reset_boss_challenge_advance_boss','reset_boss_challenge_advance_all'):
            node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            ns=vars(account_db);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
            setattr(account_db.AccountDB,name,ns[name])
        path=base/'operations.py';tree=ast.parse(path.read_text('utf-8-sig'))
        for name in ('BossChallengeResetHardModeOp','BossChallengeResetNormalBossOp','BossChallengeResetHardBossOp'):
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
            for method in ('apply','respond'):
                node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method)
                ns=vars(operations);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
                setattr(getattr(operations,name),method,ns[method])
            getattr(operations,name).prepare_response_in_transaction=False
            getattr(operations,name).error_recovery_frames=None
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BossResetTests)
    if '--before' not in sys.argv:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(BossRewardTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(commit_checks))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
