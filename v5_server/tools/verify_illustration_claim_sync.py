"""A149 and polyhedron online cycle follow-up, real memory Core and codec."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec
import generator
import illustrated_listener
import operations
import res_version_manager as versions
import weekly_challenge_service as weekly

A,B=1000101,1000201
NOW=1793667600


class IllustrationTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.execute("INSERT INTO chapter(uid,id,clear_times,star_list) VALUES(?,1030107,1,'[1,1,1]')",(UID,))
        for pid,view in ((A,1),(B,0)):
            self.db.execute("INSERT INTO illustrated(uid,kind,item_id,is_receive,is_view,complete_flag) VALUES(?,'inbetweening',?,0,?,1)",(UID,pid,view))

    def rows(self):
        return self.db.query("SELECT * FROM illustrated WHERE uid=? AND kind='inbetweening' ORDER BY item_id",(UID,))

    def claim(self,ids=None):
        return self.call(52004,dict(id_list=[A] if ids is None else ids),52005)

    def test_automatic_claim_sends_all_real_changes_preserving_views(self):
        answer,response=self.claim()
        self.assertEqual(answer['result'],0)
        self.assertEqual(self.balance(1),40)
        updates=[codec.decode(f['payload'],'sc_52011') for f in response.frames if f['sc']==52011]
        self.assertEqual(len(updates),1)
        actual={v['id']:(v['is_receive'],v['is_view']) for v in updates[0]['inbetweening_info']}
        self.assertEqual(actual,{A:(1,1),B:(1,0)})
        self.assertLess(response.scs.index(17023),response.scs.index(52011))
        self.assertLess(response.scs.index(52011),response.scs.index(52005))

    def test_duplicate_ids_and_retry_do_not_duplicate_reward(self):
        self.assertEqual(self.claim([A,A,B])[0]['result'],0)
        self.assertEqual(self.balance(1),40)
        self.assertEqual(self.claim([B])[0]['result'],0)
        self.assertEqual(self.balance(1),40)

    def test_empty_request_keeps_existing_claim_all_behavior(self):
        self.assertEqual(self.claim([])[0]['result'],0)
        self.assertEqual(self.balance(1),40)

    def test_locked_or_unknown_explicit_request_is_not_success(self):
        self.db.execute("INSERT INTO illustrated(uid,kind,item_id,is_receive,is_view,complete_flag) VALUES(?,'inbetweening',1000401,0,0,0)",(UID,))
        for pid in (1000401,99999999):
            before=self.rows()
            answer,response=self.claim([A,pid])
            self.assertNotEqual(answer['result'],0)
            self.assertEqual(response.scs,[52005])
            self.assertEqual(self.rows(),before)
            self.assertEqual(self.balance(1),0)

    def test_unread_wedding_picture_rejects_without_claiming_others(self):
        pictures=illustrated_listener._get_collect_picture_cfg()
        wedding=next(pid for pid,cfg in pictures.items() if cfg.get('wedding_role_id'))
        self.db.execute("INSERT INTO illustrated(uid,kind,item_id,is_receive,is_view,complete_flag) VALUES(?,'inbetweening',?,0,0,1)",(UID,wedding))
        before=self.rows()
        self.assertNotEqual(self.claim([wedding])[0]['result'],0)
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.balance(1),0)

    def test_required_picture_delta_failure_rolls_back_and_retry_succeeds(self):
        before=self.rows()
        real=codec.encode
        def unavailable(name,*args,**kwargs):
            return None if name=='sc_52011' else real(name,*args,**kwargs)
        with patch.object(self.conn.to_handler_ctx(),'codec_encode',side_effect=unavailable):
            answer,response=self.claim()
        self.assertNotEqual(answer['result'],0)
        self.assertEqual(response.scs,[52005])
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.balance(1),0)
        self.assertEqual(self.claim()[0]['result'],0)
        self.assertEqual(self.balance(1),40)

    def test_cold_full_snapshot_preserves_claimed_and_view_flags(self):
        self.assertEqual(self.claim()[0]['result'],0)
        packet=codec.decode(generator.gen_payload(52001,uid=UID,db=self.db),'sc_52001')
        actual={v['id']:(v['is_receive'],v['is_view']) for v in packet['inbetweening_info'] if v['id'] in (A,B)}
        self.assertEqual(actual,{A:(1,1),B:(1,0)})
        self.assertEqual(self.balance(1),40)

    def test_new_save_schema_has_policy_cycle_column(self):
        self.assertIn('policy_cycle_start',{r['name'] for r in self.db.query('PRAGMA table_info(polyhedron_meta)')})

    def test_online_polyhedron_update_uses_same_weekly_end(self):
        for version in ('229','311','321'):
            with self.subTest(version=version), patch.object(versions,'get_current_version',return_value=version), patch.object(weekly.time,'time',return_value=NOW):
                frames=operations._build_18003_frame(self.conn.to_handler_ctx(),None,uid=UID)
                packet=next(codec.decode(f.payload,'sc_11003') for f in frames if f.cmd==11003)
                self.assertEqual(packet['activity'],weekly.get_polyhedron_activity(version,NOW))
                self.assertTrue(0<packet['activity']['stop_time']-NOW<=604800)


if __name__=='__main__':
    unittest.main()
