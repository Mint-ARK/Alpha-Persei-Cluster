"""Memory-only AutoChess durable-state/Core rollback regression."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from account_db import AccountDB
import autochess_service as ac
import codec


class PersistenceTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.execute('INSERT INTO item_catalog(id,type,category) VALUES(80,1,?)',('currency',))
        self.svc=ac.AutoChessService.get_instance(self.db)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('autochess_session','ops_common','autochess_stage','autochess_medal','task')}

    def op(self,cmd,**data):return self.call(cmd,dict(game_type=0,**data),cmd+1)
    def start(self):self.assertEqual(self.op(89116,other_param=101)[0]['result'],0)
    def edit(self,**values):
        session=self.svc.get_session(UID);session.update(values);self.svc.save_session(UID);return session
    def cold(self):
        self.db._autochess_service=ac.AutoChessService(self.db)
        self.svc=ac.AutoChessService.get_instance(self.db)
        return self.svc.get_session(UID)
    def pending(self,win=False,hp=None):
        self.start()
        if win:self.edit(board_chess=[dict(unique_id=100500,chess_id=101,index=1,star=1,exp=1,atk=10000,hp=10000)])
        if hp is not None:self.edit(hp=hp)
        self.assertEqual(self.op(89144)[0]['result'],0)
        packet,response=self.op(89108);self.assertEqual(packet['result'],0)
        return packet,response

    def test_database_owner_isolation_even_same_uid(self):
        self.start();self.edit(gold=3)
        other=AccountDB.new_save_workspace();self.addCleanup(other.close)
        other.execute('INSERT INTO users(uid,nick) VALUES(?,?)',(UID,'another'))
        svc=ac.AutoChessService.get_instance(other)
        self.assertIsNot(svc,self.svc);self.assertIs(svc.db,other);self.assertEqual(svc.get_session(UID)['state'],0)
        self.assertEqual(self.svc.get_session(UID)['gold'],3);self.assertIs(self.svc.db,self.db)

    def test_new_service_restores_real_loss_and_only_consumes_once(self):
        packet,_=self.pending();self.assertEqual(packet['replay_info']['result'],2)
        before=self.svc.get_session(UID);self.cold()
        self.assertEqual(self.op(89126)[0]['result'],0)
        after=self.cold();self.assertEqual(after['hp'],before['hp']-1)
        self.assertEqual((after['win_count'],after['defeat_count'],after['round']),(0,1,2))
        state=self.state();self.assertNotEqual(self.op(89126)[0]['result'],0);self.assertEqual(self.state(),state)

    def test_terminal_loss_cold_does_not_become_win(self):
        self.pending(hp=1);self.assertEqual(self.cold()['state'],5)
        self.assertEqual(self.op(89126)[0]['result'],0);s=self.cold()
        self.assertEqual((s['state'],s['hp'],s['win_count'],s['defeat_count']),(0,0,0,1))
        self.assertFalse(self.db.query('SELECT * FROM autochess_stage'))

    def test_real_win_cold_and_next_round_response_order(self):
        packet,_=self.pending(win=True);self.assertEqual(packet['replay_info']['result'],1);self.cold()
        packet,response=self.op(89126);self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs[:3],[89125,89127,89101]);self.assertEqual(self.cold()['win_count'],1)

    def test_final_win_cold_marks_stage_once(self):
        self.pending(win=True);self.op(89126);self.op(89144);self.op(89108)
        self.assertEqual(self.cold()['state'],4)
        self.assertEqual(self.op(89126)[0]['result'],0)
        self.assertEqual(len(self.db.query('SELECT * FROM autochess_stage')),1)
        before=self.state();self.assertNotEqual(self.op(89126)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_legacy_pending_does_not_guess_win_or_lose_and_can_cancel(self):
        self.pending();self.db.execute('DELETE FROM ops_common WHERE kind=?',(self.svc.SESSION_KIND,));self.cold()
        before=self.state();self.assertNotEqual(self.op(89126)[0]['result'],0);self.assertEqual(self.state(),before)
        self.assertEqual(self.op(89128)[0]['result'],0);self.start()

    def test_pending_identity_mismatch_or_consumed_rejected(self):
        self.pending();original=deepcopy(self.svc.get_session(UID))
        for field,value in [('round',999),('stage_id',999),('state',4),('consumed',True),('battle_uid',1)]:
            changed=deepcopy(original);changed['pending_round'][field]=value
            self.edit(**changed);before=self.state()
            self.assertNotEqual(self.op(89126)[0]['result'],0);self.assertEqual(self.state(),before)
        self.edit(**original);self.assertEqual(self.op(89126)[0]['result'],0)

    def test_battle_replay_is_durable_without_resimulation(self):
        packet,response=self.pending();saved=self.cold()['last_replay']
        self.assertEqual(codec.decode(codec.encode('sc_89109',dict(result=0,replay_info=saved['info'])),'sc_89109')['replay_info'],packet['replay_info'])
        frames=[codec.decode(f['payload'],'sc_89113') for f in response.frames if f['sc']==89113]
        self.assertEqual(saved['actions'],frames[0]['battle_info'].get('action_list',[]))
        with patch.object(self.svc,'_simulate_round_battle',side_effect=AssertionError('must not resimulate')):
            self.assertEqual(self.op(89126)[0]['result'],0)

    def test_free_refresh_snapshot_persistence_and_new_game_reset(self):
        self.start();self.edit(free_refresh=2,buy_count_311=1,gold=0);self.cold()
        payload=codec.decode(self.svc.build_89101_payload(self.db,UID),'sc_89101')
        self.assertEqual(next(a['value'] for a in payload['attr_list'] if a['key']==16),2)
        self.assertEqual(self.op(89118)[0]['result'],0);self.assertEqual(self.cold()['free_refresh'],1)
        self.assertEqual(self.svc.get_session(UID)['buy_count_311'],1)
        self.op(89128);self.start();s=self.cold()
        for key in self.svc.EXTRA_FIELDS:self.assertNotIn(key,s)

    def test_read_fault_and_corrupt_json_do_not_overwrite_existing_save(self):
        self.start();before=self.state();query=self.db.query
        def fail(sql,*args,**kw):
            if 'FROM autochess_session' in sql:raise RuntimeError('read failed')
            return query(sql,*args,**kw)
        with patch.object(self.db,'query',side_effect=fail):self.assertNotEqual(self.op(89116,other_param=101)[0]['result'],0)
        self.assertEqual(self.state(),before)
        self.db.execute('UPDATE autochess_session SET chess_board_json=? WHERE uid=?',('{bad',UID));before=self.state()
        self.assertNotEqual(self.op(89118)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_secondary_write_failure_rolls_back_both_halves_and_cache(self):
        self.start();before=self.state();expected=deepcopy(self.svc.get_session(UID));execute=self.db.execute
        def fail(sql,*args,**kw):
            if 'INSERT INTO ops_common' in sql:raise RuntimeError('second write failed')
            return execute(sql,*args,**kw)
        with patch.object(self.db,'execute',side_effect=fail):self.assertNotEqual(self.op(89118)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.svc.get_session(UID),expected)
        self.assertEqual(self.op(89118)[0]['result'],0)

    def test_primary_write_failure_cannot_report_success(self):
        before=self.state();execute=self.db.execute
        def fail(sql,*args,**kw):
            if 'INSERT OR REPLACE INTO autochess_session' in sql:raise RuntimeError('save failed')
            return execute(sql,*args,**kw)
        with patch.object(self.db,'execute',side_effect=fail):self.assertNotEqual(self.op(89116,other_param=101)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.svc.get_session(UID)['state'],0);self.start()

    def test_purchase_response_failure_does_not_leave_ghost_piece(self):
        self.start();item=self.svc.get_session(UID)['shop_items'][0]
        before=self.state();expected=deepcopy(self.svc.get_session(UID));encoder=ac._encode
        for bad in (None,b'',RuntimeError('response failed')):
            def fail(name,data):
                if name=='sc_89103':
                    if isinstance(bad,Exception):raise bad
                    return bad
                return encoder(name,data)
            with patch.object(ac,'_encode',side_effect=fail):
                self.assertNotEqual(self.op(89102,shop_type=0,shop_unique_id=item['shop_unique_id'],to_index=1)[0]['result'],0)
            self.assertEqual(self.state(),before);self.assertEqual(self.svc.get_session(UID),expected)
        packet,response=self.op(89102,shop_type=0,shop_unique_id=item['shop_unique_id'],to_index=1)
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs[:2],[89103,89101])
        self.assertEqual(len(self.cold()['board_chess']),1)

    def test_settlement_response_failure_preserves_pending_for_retry(self):
        self.pending();before=self.state();expected=deepcopy(self.cold());encoder=ac._encode
        with patch.object(ac,'_encode',side_effect=lambda n,d:None if n=='sc_89127' else encoder(n,d)):
            self.assertNotEqual(self.op(89126)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.svc.get_session(UID),expected)
        self.assertEqual(self.op(89126)[0]['result'],0);self.assertEqual(self.cold()['defeat_count'],1)

    def test_stage_write_failure_keeps_final_pending(self):
        self.pending(win=True);self.op(89126);self.op(89144);self.op(89108);before=self.state();execute=self.db.execute
        def fail(sql,*args,**kw):
            if 'INSERT INTO autochess_stage' in sql:raise RuntimeError('clear failed')
            return execute(sql,*args,**kw)
        with patch.object(self.db,'execute',side_effect=fail):self.assertNotEqual(self.op(89126)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.cold()['state'],4)
        self.assertEqual(self.op(89126)[0]['result'],0)

    def test_existing_prepare_session_upgrades_without_losing_board(self):
        self.start();self.edit(board_chess=[dict(unique_id=100888,chess_id=101,index=1,star=1,atk=2,hp=3)])
        self.db.execute('DELETE FROM ops_common WHERE kind=?',(self.svc.SESSION_KIND,));expected=self.cold()['board_chess']
        self.assertEqual(self.op(89118)[0]['result'],0);self.assertEqual(self.cold()['board_chess'],expected)

    def test_311_purchase_counter_survives_cold_and_triggers_second_purchase(self):
        self.start()
        shop=[dict(shop_unique_id=100600+i,id=101,index=i+1,exp=1,star=1,atk=2,hp=3,shop_type=0) for i in range(3)]
        self.edit(gold=100,board_chess=[dict(unique_id=100500,chess_id=311,index=1,star=1,exp=1,atk=2,hp=3)],shop_items=shop)
        self.assertEqual(self.op(89102,shop_type=0,shop_unique_id=100600,to_index=2)[0]['result'],0)
        self.assertEqual(self.cold()['buy_count_311'],1)
        self.assertEqual(self.op(89102,shop_type=0,shop_unique_id=100601,to_index=3)[0]['result'],0)
        state=self.cold();self.assertEqual(state['buy_count_311'],2)
        self.assertEqual((state['shop_items'][0]['atk'],state['shop_items'][0]['hp']),(3,4))

    def test_cancel_response_failure_preserves_original_session(self):
        self.pending();before=self.state();encoder=ac._encode
        with patch.object(ac,'_encode',side_effect=lambda n,d:None if n=='sc_89129' else encoder(n,d)):
            self.assertNotEqual(self.op(89128)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.cold()['state'],3)
        self.assertEqual(self.op(89128)[0]['result'],0)

    def test_launch_response_failure_returns_to_prepare_end_and_retries(self):
        self.start();self.op(89144);before=self.state();encoder=ac._encode
        with patch.object(ac,'_encode',side_effect=lambda n,d:None if n=='sc_89113' else encoder(n,d)):
            self.assertNotEqual(self.op(89108)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.cold()['state'],2)
        self.assertEqual(self.op(89108)[0]['result'],0)

    def test_actual_free_refresh_hook_survives_cold(self):
        self.start();self.edit(board_chess=[dict(unique_id=100500,chess_id=202,index=1,star=1,exp=1,atk=0,hp=1)])
        self.op(89144);self.op(89108);self.op(89126)
        self.assertEqual(self.cold()['free_refresh'],2)
        self.assertEqual(self.op(89118)[0]['result'],0);self.assertEqual(self.cold()['free_refresh'],1)

    def test_commit_failure_reloads_sql_not_failed_working_copy(self):
        self.start();before=self.state();expected=deepcopy(self.svc.get_session(UID));raw=self.db._checked_conn()
        class ConnectionProxy:
            def __getattr__(self,name):return getattr(raw,name)
            def commit(self):raise RuntimeError('injected commit failure')
        with patch.object(self.db,'_checked_conn',return_value=ConnectionProxy()):
            self.assertNotEqual(self.op(89118)[0]['result'],0)
        self.assertEqual(self.state(),before);self.assertEqual(self.svc.get_session(UID),expected)
        self.assertEqual(self.op(89118)[0]['result'],0)

    def test_empty_progress_packet_is_valid_and_does_not_fail_new_save(self):
        self.assertEqual(codec.decode(self.svc.build_89201_payload(self.db,UID),'sc_89201'),{})
        self.assertEqual(codec.decode(self.svc.build_89125_payload(self.db,UID),'sc_89125')['state'],0)

    def test_other_modes_remain_closed(self):
        for mode in (1,2,3):self.assertNotEqual(self.call(89116,dict(game_type=mode,other_param=101),89117)[0]['result'],0)
        self.assertNotEqual(self.op(89116,other_param=401)[0]['result'],0)


if __name__=='__main__':unittest.main(verbosity=2)
