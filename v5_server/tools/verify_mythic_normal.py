"""A91/A92: actual memory AccountDB/Core and native codecs, no real saves."""
import copy
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID, NOW
import battle_server as udp
import codec
import generator
import hero_codec
import inventory_service as inventory
import middleware as mw
import mythic_normal_service as normal
import weekly_challenge_service as weekly


class NormalTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.now = NOW
        clock = patch('time.time', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        sessions = patch.dict(mw._BATTLE_SESSIONS, {}, clear=True)
        sessions.start(); self.addCleanup(sessions.stop)
        self.db.execute('UPDATE users SET level=70 WHERE uid=?', (UID,))
        self.db.upsert('hero', UID, hero_codec.initial_hero_row(1084, 100), keys=('uid', 'id'))
        for iid in (25, 40701):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)', (iid,))
        self.db.set_mythic_difficulty(UID, 1)

    def state(self):
        return {table: self.db.query('SELECT * FROM '+table+' ORDER BY rowid') for table in
                ('currency', 'material', 'hero', 'ops_common', 'mythic_progress_partition',
                 'mythic_progress_reward', 'chapter', 'stage_sub', 'server_global_config')}

    def seed(self, stars, stage=3026001, win=True):
        normal.record(self.db, UID, normal.entry(self.db, UID, stage), stars, win)

    def claim(self, star):
        return self.call(44012, dict(star=star), 44013)

    def enter(self, stage=3026001, result=1, report=None):
        packet, response = self.call(54030, dict(common_info=dict(type=11, dest=stage,
            hero_list=[dict(hero_id=1084, hero_type=1)])), 54031)
        self.assertEqual(packet['result'], 0, response.scs)
        self.bid = self.conn.battle_id[0]
        mw.get_battle_session(self.bid)['result_data'] = dict(result=result, info=report if report is not None else
            dict(battle_time=180001, item_ids=[], item_numbers=[]))
        return response

    def finish(self):
        return self.call(54032, dict(battle_id=self.bid), 54033)

    def reject_claim(self, star, code=None):
        before = self.state(); packet, response = self.claim(star)
        self.assertNotEqual(packet['result'], 0)
        if code is not None: self.assertEqual(packet['result'], code)
        self.assertEqual(response.scs, [44013]); self.assertEqual(self.state(), before)

    def test_empty_and_legacy_counts_never_fabricate_conditions(self):
        self.assertEqual(normal.payload(self.db, UID)['main_partition_star_list'], [])
        self.db.record_mythic_partition_clear(UID, 1, 3)
        self.assertEqual(weekly.get_mythic_data(UID, self.db)[1]['main_partition_star_list'], [])
        self.reject_claim(1, 2005)

    def test_noncontiguous_union_lower_replay_and_cold_recovery(self):
        self.seed([0, 1, 0]); self.seed([0, 0, 1]); self.seed([0, 1, 0])
        expected = dict(clear_partition_id_list=[1], main_partition_star_list=[2, 3], star_reward_provide_list=[])
        self.assertEqual(normal.payload(self.db, UID), expected)
        self.assertEqual(weekly.get_mythic_data(UID, self.db)[1], expected)
        cold=codec.decode(generator.gen_payload(44009, uid=UID, db=self.db), 'sc_44009')
        self.assertEqual({key:cold.get(key,[]) for key in expected}, expected)
        from new_player_service import payload
        self.assertEqual(payload(self.db, UID, 44009), expected)

    def test_tier_threshold_is_count_not_condition_id_and_all_tiers_once(self):
        self.seed([0, 1, 0]); self.reject_claim(2, 2005)
        for star in (0, 4, 999): self.reject_claim(star, 2)
        packet, response = self.claim(1)
        self.assertEqual(packet['result'], 0); self.assertEqual(response.scs, [17023, 44013])
        self.assertEqual({r['id']:r['num'] for r in packet['item_list']}, {25:135, 40701:4})
        delta = codec.decode(response.frames[0]['payload'], 'sc_17023')
        self.assertEqual({r['id']:r['num'] for r in delta['normal_items']}, {25:135, 40701:4})
        self.reject_claim(1, 2013)
        self.seed([1, 0, 1])
        for star in (2, 3): self.assertEqual(self.claim(star)[0]['result'], 0)
        self.assertEqual(self.balance(25), 405); self.assertEqual(self.balance(40701), 12)
        self.assertEqual(normal.payload(self.db, UID)['star_reward_provide_list'], [1, 2, 3])

    def test_difficulties_have_own_stars_shared_claims_and_unknown_rejected(self):
        self.seed([0, 1, 0]); self.assertEqual(self.claim(1)[0]['result'], 0)
        self.db.set_mythic_difficulty(UID, 2)
        self.assertEqual(normal.payload(self.db, UID)['main_partition_star_list'], [])
        self.reject_claim(1, 2013); self.reject_claim(2, 2005)
        self.seed([1, 1, 0], stage=3026002)
        self.assertEqual(self.claim(2)[0]['result'], 0); self.assertEqual(self.balance(25), 135+195)
        self.db.set_mythic_difficulty(UID, 999)
        self.reject_claim(3)

    def test_subpartitions_do_not_contribute_stars(self):
        self.seed([], stage=3027001)
        self.assertEqual(normal.payload(self.db, UID)['clear_partition_id_list'], [101])
        self.reject_claim(1, 2005)

    def test_current_legacy_claim_cannot_be_reissued_and_final_reset_preserves_normal(self):
        self.db.claim_mythic_reward(UID,1)
        self.seed([1,1,1]);self.reject_claim(1,2013)
        before=copy.deepcopy(normal.payload(self.db,UID))
        self.db.reset_mythic_final_team(UID)
        self.assertEqual(normal.payload(self.db,UID),before)

    def test_real_udp_packed_and_unpacked_collect_and_existing_record_fields(self):
        for packed in (False, True):
            def field(number, values):
                if packed:
                    return udp._f_bytes(number, b''.join(udp._varint(v) for v in values))
                return b''.join(udp._f_varint2(number, v) for v in values)
            info = field(12, [60001,60002])+field(13,[1,7])+field(14,[42])+field(15,[9])
            _, _, _, report = udp.parse_battle_result(udp._f_bytes(4, info))
            self.assertEqual(report['item_ids'], [60001,60002]); self.assertEqual(report['item_numbers'], [1,7])
            self.assertEqual(report['record_data_ids'], [42]); self.assertEqual(report['record_data_numbers'], [9])
            self.assertEqual(normal.evaluate(3026001, False, report)[0], [1,0,0])

    def test_collect_missing_malformed_negative_and_time_edge(self):
        for report in ({},dict(item_ids=None,item_numbers=None),dict(item_ids=[60001],item_numbers=[]),
                       dict(item_ids=[60001],item_numbers=[-1])):
            self.assertEqual(normal.evaluate(3026001, True, report)[0], [0,1,0])
        for elapsed, expected in ((180000,[0,1,1]), (180001,[0,1,0])):
            self.assertEqual(normal.evaluate(3026001, True, dict(battle_time=elapsed))[0], expected)
        malformed = udp._f_bytes(4, b'\x62\x01\x80')
        _,_,_,report=udp.parse_battle_result(malformed)
        self.assertIsNone(report['item_ids']); self.assertEqual(normal.evaluate(3026001,False,report)[0],[0,0,0])

    def test_real_core_win_only_condition_two_then_repeat_receipt(self):
        self.enter(); packet,response=self.finish()
        self.assertEqual(packet['result'],0)
        self.assertEqual([r['is_achieve'] for r in packet['battle_result']['star_list']],[0,1,0])
        self.assertEqual(normal.payload(self.db, UID)['main_partition_star_list'],[2])
        self.assertNotIn(44009,response.scs)
        before=self.state(); packet,response=self.finish()
        self.assertEqual(packet['result'],0);self.assertEqual(response.scs,[54033]);self.assertEqual(self.state(),before)

    def test_real_core_loss_quit_keep_collection_only_no_clear(self):
        for result in (2,3):
            normal.reset(self.db, UID)
            self.enter(result=result,report=dict(battle_time=1000,item_ids=[60001],item_numbers=[1]))
            packet,response=self.finish();self.assertEqual(packet['result'],0)
            self.assertEqual([r['is_achieve'] for r in packet['battle_result']['star_list']],[1,0,0])
            self.assertEqual(normal.payload(self.db,UID)['main_partition_star_list'],[1])
            self.assertEqual(normal.payload(self.db,UID)['clear_partition_id_list'],[])
            self.assertNotIn(17023,response.scs)

    def test_claim_failures_mark_grant_ack_and_delta_all_rollback(self):
        self.seed([1,1,1])
        for target in ('mark','grant','ack','delta'):
            before=self.state();ctx=self.conn.to_handler_ctx()
            if target=='mark':
                real=normal.save
                def fail(*a,**kw): real(*a,**kw); raise RuntimeError('after marker')
                fault=patch.object(normal,'save',side_effect=fail)
            elif target=='grant':
                real=inventory.grant_item
                def fail(*a,**kw): real(*a,**kw); raise RuntimeError('after grant')
                fault=patch.object(inventory,'grant_item',side_effect=fail)
            elif target=='ack':
                real=ctx.codec_encode
                fault=patch.object(ctx,'codec_encode',side_effect=lambda name,*a,**kw:None if name=='sc_44013' else real(name,*a,**kw))
            else:
                real=generator.gen_payload
                fault=patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else real(cmd,*a,**kw))
            with fault: self.reject_claim(1)
            self.assertEqual(self.state(),before)
        self.assertEqual(self.claim(1)[0]['result'],0)

    def test_settlement_ack_or_asset_delta_failure_rollback_then_retry(self):
        for target in ('ack','delta'):
            self.enter(); before=self.state();ctx=self.conn.to_handler_ctx()
            if target=='ack':
                real=ctx.codec_encode
                fault=patch.object(ctx,'codec_encode',side_effect=lambda name,*a,**kw:None if name=='sc_54033' else real(name,*a,**kw))
            else:
                real=generator.gen_payload
                fault=patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw:None if cmd==17023 else real(cmd,*a,**kw))
            with fault:
                packet,response=self.finish();self.assertNotEqual(packet['result'],0)
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))
            self.assertEqual(self.finish()[0]['result'],0)

    def test_period_change_and_beacon_reset_do_not_revive_old_stars_rewards(self):
        self.seed([1,1,1]);self.assertEqual(self.claim(1)[0]['result'],0)
        self.now=normal.bounds()[1]+1
        self.assertEqual(normal.payload(self.db,UID)['main_partition_star_list'],[])
        self.assertEqual(normal.payload(self.db,UID)['star_reward_provide_list'],[])
        self.seed([0,1,0]);self.assertEqual(self.claim(1)[0]['result'],0)
        self.db.reset_mythic_by_beacon(UID)
        self.assertEqual(normal.payload(self.db,UID),dict(clear_partition_id_list=[],main_partition_star_list=[],star_reward_provide_list=[]))

    def test_old_attempt_period_switch_or_beacon_reject_without_mutation(self):
        for target in ('period','switch','beacon','missing'):
            self.db.set_mythic_difficulty(UID,1);self.enter()
            if target=='period':self.now=normal.bounds()[1]+1
            elif target=='switch':self.db.set_mythic_difficulty(UID,2)
            elif target=='beacon':self.db.reset_mythic_by_beacon(UID)
            else:mw.get_battle_session(self.bid).pop('mythic_normal_context')
            before=self.state();packet,response=self.finish()
            self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54033])
            self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))

    def test_mid_settlement_period_change_rolls_back_and_returns_normal_error(self):
        self.enter();before=self.state();real=normal.record
        def advance(*a,**kw):
            real(*a,**kw);self.now=normal.bounds()[1]+1
        with patch.object(normal,'record',side_effect=advance):
            packet,response=self.finish()
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[54033])
        self.assertEqual(self.state(),before);self.assertNotIn('br',mw.get_battle_session(self.bid))

    def test_qualification_query_failure_never_means_unclaimed(self):
        self.seed([1,1,1]);before=self.state();real=self.db.query
        def fail(sql,*a,**kw):
            if 'SELECT value_json FROM ops_common' in sql:raise RuntimeError('isolated read failure')
            return real(sql,*a,**kw)
        with patch.object(self.db,'query',side_effect=fail):
            packet,response=self.claim(1)
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[44013])
        self.assertEqual(self.state(),before)


if __name__=='__main__':
    unittest.main(verbosity=2)
