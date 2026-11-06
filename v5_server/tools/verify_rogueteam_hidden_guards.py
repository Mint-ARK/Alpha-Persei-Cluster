"""Hidden-map ownership and required-frame rollback, memory-only real Core."""
import json
import unittest
from unittest.mock import patch
import verify_rogueteam_transition as base
from verify_report_small_fixes import MemoryTests, UID
import rogueteam_flow as flow
import codec

class HiddenGuardTests(MemoryTests):
    saved=base.TransitionTests.saved
    set=base.TransitionTests.set
    state=base.TransitionTests.state
    attrs=base.TransitionTests.attrs
    op=base.TransitionTests.op
    ok=base.TransitionTests.ok
    rejected=base.TransitionTests.rejected
    def setUp(self):
        super().setUp()
        self.svc = base.RogueTeamService.get(db=self.db)
        self.db.upsert('hero', UID, base.hero_codec.initial_hero_row(1084, 100), keys=('uid','id'))
        self.assertEqual(self.svc.start_run(UID,100001,1,[dict(hero_id=1084)],[])[0],0)
        self.set(floor_num=2,select_node_id=0,other_info_json='{}',shop_info_json='{}',second_map_json='[]',map_info_json=json.dumps([
            dict(node_id=101,col=1,row=3,node_type=1,state=3,param=4101101,next_id_list=[201]),
            dict(node_id=201,col=2,row=3,node_type=8,state=0,param=60020,next_id_list=[301]),
            dict(node_id=301,col=3,row=3,node_type=1,state=2,param=4101101,next_id_list=[])]))
        self.ok(88004,node_id=201)
        self.ok(88024,opt_id=600202)
    def enter_pending(self):
        self.ok(88024,opt_id=600221)
    def hidden(self):
        self.enter_pending();self.ok(88008,sign=2)
        nid=json.loads(self.saved()['map_info_json'])[0]['node_id']
        self.ok(88004,node_id=nid)
        return nid
    def test_no_unearned_or_wrong_direction_switch_and_no_skipping_pending(self):
        self.rejected(88008,sign=2);self.rejected(88008,sign=3)
        self.enter_pending()
        self.rejected(88008,sign=1);self.rejected(88008,sign=3)
        self.rejected(88004,node_id=301)
        self.ok(88008,sign=2)
        self.rejected(88008,sign=2);self.rejected(88008,sign=3);self.rejected(88008,sign=1)
    def test_pending_owner_rejects_stale_run_floor_hidden_and_node(self):
        self.enter_pending();base=flow.flow(self.saved())
        for key,value in (('run_id','expired'),('floor',3),('hidden',True),('node_id',301)):
            m=json.loads(json.dumps(base));m['map_switch'][key]=value
            flow.save_flow(self.svc,UID,100001,m)
            self.rejected(88008,sign=2)
        flow.save_flow(self.svc,UID,100001,base)
        self.ok(88008,sign=2)
    def test_prepare_and_accept_required_frames_rollback(self):
        for sc in (88221,88003,88001):
            before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
            with patch.object(ctx,'codec_encode',side_effect=lambda schema,obj,sc=sc: None if schema=='sc_'+str(sc) else encode(schema,obj)):
                p,r=self.op(88024,opt_id=600221)
            self.assertNotEqual(p['result'],0);self.assertEqual(self.state(),before)
        self.enter_pending()
        for sc in (88001,88009):
            before=self.state();ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
            with patch.object(ctx,'codec_encode',side_effect=lambda schema,obj,sc=sc: None if schema=='sc_'+str(sc) else encode(schema,obj)):
                p,r=self.op(88008,sign=2)
            self.assertNotEqual(p['result'],0);self.assertEqual(self.state(),before)
        self.ok(88008,sign=2)
    def test_return_preserves_negative_relic_and_gives_toolbox_only_once(self):
        nid=self.hidden()
        self.set(other_item_list_json=json.dumps([dict(id=1320004,value=1)]))
        self.ok(88024,opt_id=600301);self.ok(88024,opt_id=600402)
        self.ok(88024,opt_id=600421)
        items=json.loads(self.saved()['other_item_list_json'])
        self.assertIn(1320004,[x['id'] for x in items]);self.assertEqual([x['id'] for x in items].count(1330003),1)
        self.rejected(88024,opt_id=600421);self.ok(88008,sign=3)
        self.assertEqual(self.saved()['floor_num'],2)
        self.assertEqual([n['node_id'] for n in json.loads(self.saved()['map_info_json'])],[101,201,301])
        self.rejected(88008,sign=3)
    def test_withdraw_while_switch_pending_never_replays_map_switch(self):
        self.enter_pending()
        self.assertEqual(self.svc.settle_run(UID)[0],0)
        self.assertEqual(self.svc.pending_frames(UID),[])
        self.assertFalse(self.saved()['in_game'])
    def test_threshold_reward_defers_exit_until_last_claim(self):
        self.hidden();self.ok(88024,opt_id=600301);self.ok(88024,opt_id=600402)
        self.attrs(**{'1':95})
        _,response=self.ok(88024,opt_id=600421)
        self.assertNotIn(88221,response.scs)
        state=self.saved();self.assertEqual(json.loads(state['map_info_json'])[0]['state'],1)
        self.assertTrue(state['select_node_id']);self.rejected(88008,sign=3)
        pending=json.loads(state['other_info_json']);self.assertTrue(pending['param_list'])
        _,response=self.ok(88200,event_id=pending['event_type'],param_arg=pending['param_list'][0]['index'])
        self.assertIn(88221,response.scs)
        self.assertLess(response.scs.index(88221),response.scs.index(88001))
        self.ok(88008,sign=3)

if __name__=='__main__':unittest.main(verbosity=2)
