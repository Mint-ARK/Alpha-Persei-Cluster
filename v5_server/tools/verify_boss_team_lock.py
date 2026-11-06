"""A84: real memory Core/codec/SQL, native save cache and battle entry guards."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_boss_battle_period import BossBattlePeriodTests
from verify_boss_reset_guard import BossResetTests
from verify_report_small_fixes import UID
from test_commit_recovery import deny_once
import account_db
import codec
import core
import hero_codec
import middleware as mw
import operations
import weekly_challenge_service as weekly
from boss_challenge_team import BossTeamError

ROOT=Path(__file__).resolve().parents[2]


class BossTeamTests(BossBattlePeriodTests):
    def setUp(self):
        super().setUp()
        self.db.upsert('hero',UID,hero_codec.initial_hero_row(1011,100),keys=('uid','id'))
        self.configure(2)

    def configure(self,kind):
        self.kind=kind;self.db.set_boss_challenge_mode(UID,101 if kind==2 else 4)
        overview,normal,advance=self.read()
        self.rows=normal['boss_challenge_list'] if kind==1 else advance['boss_list']
        key='group_id' if kind==1 else 'id'
        self.target,self.other=[int(b[key]) for b in self.rows[:2]]
        self.cmd,self.sc,self.field=(45110,45111,'group_id') if kind==1 else (45108,45109,'id')
        self.table,self.key=('boss_challenge_normal','group_id') if kind==1 else ('boss_challenge_advance','boss_id')
        if kind==1:
            self.stage=weekly.get_boss_catalog()['stage_groups'][str(self.target)]['stages'][0]
        else:
            self.stage=weekly.get_boss_catalog()['advance_pool'][str(self.target)]['stage_id']

    def lock(self,boss,heroes):
        if self.kind==2:self.db.record_boss_challenge_advance_clear(UID,boss,9000,heroes)
        else:
            stage=weekly.get_boss_catalog()['stage_groups'].get(str(boss),{'stages':[3011001]})['stages'][0]
            self.db.record_boss_challenge_normal_clear(UID,boss,stage,[1,1,1],heroes)

    def row(self,boss=None):return self.db.get(self.table,UID,'AND '+self.key+'=?',(boss or self.target,))

    def save(self,heroes,target=None):return self.call(self.cmd,{self.field:target or self.target,'heroes_cfg':heroes},self.sc)

    def start(self,heroes):
        return self.call(54030,{'common_info':dict(type=100 if self.kind==2 else 10,dest=self.stage,
            index=self.target if self.kind==2 else 0,hero_list=[dict(hero_id=h,hero_type=1) for h in heroes])},54031)

    def full_state(self):
        state=self.state()
        state.update({t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('stage_team','boss_challenge_affixes')})
        return state

    def assert_save_rejected(self,heroes,code=180021,target=None):
        before=self.full_state();packet,response=self.save(heroes,target)
        self.assertEqual(packet['result'],code);self.assertEqual(self.full_state(),before)
        self.assertEqual(response.scs,[self.sc,45201,45001,45101])
        # Native ChangeBossHeroTeam optimistically overwrites cache before send.
        client={self.target:list(heroes)}
        state_sc=45001 if self.kind==1 else 45101
        for f in response.frames:
            if f['sc']==state_sc:
                data=codec.decode(f['payload'],'sc_'+str(state_sc))
                rows=data['boss_challenge_list'] if self.kind==1 else data['boss_list']
                client={int(r[self.field]):r.get('last_heroes_cfg',[]) for r in rows}
        self.assertEqual(client[self.target],json.loads(self.row()['last_heroes_cfg']))

    def assert_entry_rejected(self,heroes,code=None):
        before=self.full_state();sessions=copy.deepcopy(mw._BATTLE_SESSIONS)
        ctx=self.conn.to_handler_ctx()
        seq=(ctx.battle_seq[:],ctx.battle_id[:],ctx.battle_stage[:],ctx.hero_last_payload[:])
        with patch('battle_payload.build_sc_54003_dynamic',wraps=mw.battle_payload.build_sc_54003_dynamic) as build:
            packet,response=self.start(heroes)
        self.assertNotEqual(packet['result'],0)
        if code is not None:self.assertEqual(packet['result'],code)
        self.assertEqual(response.scs,[54031]);self.assertEqual(build.call_count,0)
        self.assertEqual(self.full_state(),before);self.assertEqual(mw._BATTLE_SESSIONS,sessions)
        self.assertEqual((ctx.battle_seq[:],ctx.battle_id[:],ctx.battle_stage[:],ctx.hero_last_payload[:]),seq)

    def test_other_boss_lock_rejects_save_and_entry_both_modes(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.configure(kind);self.lock(self.other,[1084])
                self.db.save_boss_challenge_hero_team(UID,kind,self.target,[1011,0,0])
                self.assert_save_rejected([1084,0,0]);self.assert_entry_rejected([1084],180021)

    def test_own_locked_hero_can_save_and_start_without_full_state_frames(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.configure(kind);self.lock(self.target,[1084]);self.lock(self.other,[1011])
                packet,response=self.save([1084,0,0])
                self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[self.sc])
                packet,response=self.start([1084])
                self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54031,54003,54007])
                session=mw.get_battle_session(self.conn.battle_id[0])
                self.assertEqual(session['heroes'],[1084]);self.assertEqual(session['boss_context']['boss_id'],self.target)
                self.assertEqual(json.loads(self.row()['used_heroes']),[1084])

    def test_stale_saved_team_rechecked_at_entry_then_reset_releases_lock(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.configure(kind);self.assertEqual(self.save([1084,0,0])[0]['result'],0)
                self.lock(self.other,[1084]);self.assert_entry_rejected([1084],180021)
                if kind==1:self.db.reset_boss_challenge_normal_boss(UID,self.other)
                else:self.db.reset_boss_challenge_advance_boss(UID,self.other)
                self.assertEqual(self.start([1084])[0]['result'],0)

    def test_presets_inactive_boss_other_mode_and_other_uid_do_not_lock(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.configure(kind)
                self.db.save_boss_challenge_hero_team(UID,kind,self.other,[1084,0,0])
                self.lock(999999,[1084])
                self.db.execute(f"INSERT INTO {self.table}(uid,{self.key},used_heroes) VALUES(?,?,?)",(UID+1,self.other,'[1084]'))
                if kind==1:self.db.record_boss_challenge_advance_clear(UID,20001,9000,[1084])
                else:self.db.record_boss_challenge_normal_clear(UID,5009,3011001,[1,1,1],[1084])
                self.assertEqual(self.save([1084,0,0])[0]['result'],0)
                self.assertEqual(self.start([1084])[0]['result'],0)
                # Keep this mode-isolation fixture from becoming a current
                # active boss lock in the next subcase's other mode.
                if kind==1:self.db.execute("UPDATE boss_challenge_advance SET used_heroes='[]' WHERE uid=? AND boss_id=20001",(UID,))

    def test_compatibility_wire_format_and_empty_slots_round_trip(self):
        for kind in (1,2):
            self.configure(kind)
            data={self.field:self.target,'heroes_cfg':[0,1084,0]}
            raw=codec.encode('cs_'+str(self.cmd),data)
            self.assertTrue(raw);self.assertEqual(codec.decode(raw,'cs_'+str(self.cmd)),data)
            packet,response=self.save([0,1084,0])
            self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[self.sc])
            self.assertEqual(json.loads(self.row()['last_heroes_cfg']),[0,1084,0])
            self.assert_save_rejected([1084,1011,1084,1011],2)

    def test_wrong_mode_unknown_or_inactive_target_does_not_change_presets(self):
        self.db.save_boss_challenge_hero_team(UID,2,self.target,[1011,0,0])
        self.assert_save_rejected([1084],2,999999)
        self.db.set_boss_challenge_mode(UID,4)
        self.assert_save_rejected([1084],2);self.assert_entry_rejected([1084])

    def test_failed_or_malformed_lock_reads_fail_closed(self):
        for kind in (1,2):
            with self.subTest(kind=kind):
                self.configure(kind);self.db.save_boss_challenge_hero_team(UID,kind,self.target,[1011,0,0])
                self.lock(self.other,[1084]);query=self.db.query;seen=[]
                def fail_once(sql,*args,**kwargs):
                    if sql=='SELECT * FROM '+self.table+' WHERE uid=?' and not seen:
                        seen.append(sql);raise RuntimeError('isolated locked heroes read')
                    return query(sql,*args,**kwargs)
                with patch.object(self.db,'query',side_effect=fail_once):self.assert_save_rejected([1084],9)
                seen.clear()
                with patch.object(self.db,'query',side_effect=fail_once):self.assert_entry_rejected([1084])
                # Valid JSON with the wrong structure must not silently become a lock-free list.
                for bad in ('{"1084":1}','["1084"]','broken-json'):
                    self.db.execute(f'UPDATE {self.table} SET used_heroes=? WHERE uid=? AND {self.key}=?',(bad,UID,self.other))
                    self.assert_entry_rejected([1084])
                self.db.execute(f'UPDATE {self.table} SET used_heroes=? WHERE uid=? AND {self.key}=?',('[1084]',UID,self.other))

    def test_empty_or_failed_save_ack_and_commit_refusal_roll_back_preset(self):
        for kind in (1,2):
            self.configure(kind);self.db.save_boss_challenge_hero_team(UID,kind,self.target,[1011,0,0])
            ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
            for fault in ('empty','raise','commit'):
                with self.subTest(kind=kind,fault=fault):
                    if fault=='commit':
                        connection=self.db._conn();deny_once(connection,'COMMIT')
                        try:self.assert_save_rejected([1084,0,0],9)
                        finally:connection.set_authorizer(None)
                    else:
                        def fail(name,data):
                            if name=='sc_'+str(self.sc):
                                if fault=='raise':raise RuntimeError('isolated save ACK')
                                return None
                            return encode(name,data)
                        with patch.object(ctx,'codec_encode',side_effect=fail):self.assert_save_rejected([1084,0,0],1)

    def test_direct_save_and_nested_write_failure_are_atomic(self):
        for kind in (1,2):
            self.configure(kind);self.db.save_boss_challenge_hero_team(UID,kind,self.target,[1011,0,0])
            self.lock(self.other,[1084]);before=self.full_state()
            with self.assertRaises(BossTeamError):self.db.save_boss_challenge_hero_team(UID,kind,self.target,[1084,0,0])
            self.assertEqual(self.full_state(),before)
            execute=self.db.execute
            def fail(sql,*a,**kw):
                result=execute(sql,*a,**kw)
                if sql.startswith('INSERT INTO '+self.table):raise RuntimeError('isolated save after write')
                return result
            with self.db.transaction():
                self.db.execute("UPDATE users SET nick='outer-kept' WHERE uid=?",(UID,))
                with patch.object(self.db,'execute',side_effect=fail):
                    with self.assertRaises(RuntimeError):self.db.save_boss_challenge_hero_team(UID,kind,self.target,[1011,1011,0])
            self.assertEqual(self.row(),before[self.table][0])
            self.assertEqual(self.db.get('users',UID)['nick'],'outer-kept')

    def test_new_cycle_releases_old_locks_before_save_and_entry(self):
        self.lock(self.other,[1084]);self.now+=604800
        self.configure(2)
        self.assertEqual(self.save([1084,0,0])[0]['result'],0)
        self.assertEqual(self.start([1084])[0]['result'],0)


if __name__=='__main__':
    if '--before' in sys.argv:
        base=ROOT/'archive/boss_team_lock_20261104/before/v5_server'
        path=base/'account_db.py';tree=ast.parse(path.read_text('utf-8-sig'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AccountDB')
        node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='save_boss_challenge_hero_team')
        ns=vars(account_db);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
        account_db.AccountDB.save_boss_challenge_hero_team=ns[node.name]
        path=base/'operations.py';tree=ast.parse(path.read_text('utf-8-sig'))
        for name in ('BossChallengeSaveHardTeamOp','BossChallengeSaveNormalTeamOp'):
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
            ns=vars(operations)
            for node in cls.body:
                if isinstance(node,ast.FunctionDef):
                    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
                    setattr(core.OPERATIONS[45108 if 'Hard' in name else 45110],node.name,ns[node.name].__get__(core.OPERATIONS[45108 if 'Hard' in name else 45110]))
            core.OPERATIONS[45108 if 'Hard' in name else 45110].prepare_response_in_transaction=False
            core.OPERATIONS[45108 if 'Hard' in name else 45110].error_recovery_frames=None
        path=base/'middleware.py';tree=ast.parse(path.read_text('utf-8-sig'))
        for name in ('_boss_battle_context','h_54030'):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            ns=vars(mw);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
        mw.CORE_HANDLERS[54030]=mw.h_54030
    suite=unittest.TestSuite(BossTeamTests(n) for n in BossTeamTests.__dict__ if n.startswith('test_'))
    if '--before' not in sys.argv:
        suite.addTests(unittest.TestSuite(BossResetTests(n) for n in BossResetTests.__dict__ if n.startswith('test_')))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
