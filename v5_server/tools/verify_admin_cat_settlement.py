"""A232/A236: due-time and atomic asset deltas through real memory Core/codec."""
import datetime
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID
import admin_cat_explore_service as cat
import codec
import generator

SAT=int(datetime.datetime.fromisoformat('2026-11-07T12:00:00+08:00').timestamp())


class SettlementTests(MemoryTests):
    def setUp(self):
        super().setUp();self.now=SAT
        for p in (patch('time.time',side_effect=lambda:self.now),patch.object(cat.random,'randint',return_value=100)):
            p.start();self.addCleanup(p.stop)
        self.svc=cat.AdminCatExploreService.get_instance();self.svc.get_or_create_explore_data(self.db,UID)
        for iid in (53,54,55,56):self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))

    def prepare(self,cmd):
        if cmd==67006:self.db.save_admin_cat_queue(UID,10001,10001,self.now-14400,self.now,4,[])
        elif cmd==67008:
            data=self.svc.get_or_create_explore_data(self.db,UID);data.update(weekly_time=1,weekly_reward_state=0)
            self.db.save_admin_cat_explore_data(UID,data)
        elif cmd==67002:
            self.db.update_admin_cat_skill(UID,10001,1);self.db.set_currency(UID,56,300)
        elif cmd==67010:
            self.db.execute('DELETE FROM admin_cat_list WHERE uid=? AND mimir_id=10002',(UID,))
            self.db.set_currency(UID,53,2400);self.db.set_currency(UID,56,600)
            data=self.svc.get_or_create_explore_data(self.db,UID);data['total_explore_c']=400
            self.db.save_admin_cat_explore_data(UID,data)

    def call_kind(self,cmd):
        return self.call(cmd,{67006:dict(area_id=10001),67008:{},67002:dict(mimir_id=10001,skill_id=10001),67010:dict(mimir_id=10002)}[cmd],cmd+1)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('currency','material','admin_cat_list','admin_cat_explore_queue','admin_cat_explore_data')}

    def test_ordinary_finish_rejects_before_due_and_accepts_exact_due_once(self):
        stop=self.now+14400;self.db.save_admin_cat_queue(UID,10001,10001,self.now,stop,4,[])
        for now in (self.now,stop-1):
            self.now=now;before=self.state();packet,response=self.call_kind(67006)
            self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[67007]);self.assertEqual(self.state(),before)
        self.now=stop;packet,response=self.call_kind(67006);self.assertEqual(packet['result'],0)
        before=self.state();self.assertNotEqual(self.call_kind(67006)[0]['result'],0);self.assertEqual(self.state(),before)

    def test_service_default_also_requires_due_time(self):
        self.db.save_admin_cat_queue(UID,10001,10001,self.now,self.now+14400,4,[])
        before=self.state();result,_,items=self.svc.finish_explore(self.conn.to_handler_ctx(),UID,10001)
        self.assertNotEqual(result,0);self.assertEqual(items,[]);self.assertEqual(self.state(),before)

    def test_finish_assets_before_ack_and_snapshot_match_reward_and_new_level(self):
        self.db.set_currency(UID,53,2399);self.prepare(67006)
        packet,response=self.call_kind(67006);self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs,[17023,67007,67001])
        self.assertEqual({r['id']:r['num'] for r in packet['reward_list']},{53:220,54:264,56:36})
        delta=codec.decode(response.frames[0]['payload'],'sc_17023')
        self.assertEqual({r['id']:r['num'] for r in delta['normal_items']},{53:2619,54:264,56:36})
        state=codec.decode(response.frames[-1]['payload'],'sc_67001')
        self.assertFalse(state.get('explore_queue'));self.assertEqual(self.svc.calculate_level(self.db,UID)[0],2)

    def test_weekly_skill_and_unlock_assets_before_ack_keep_callback_snapshot_order(self):
        for cmd in (67008,67002,67010):
            self.prepare(cmd);packet,response=self.call_kind(cmd);self.assertEqual(packet['result'],0)
            self.assertEqual(response.scs,[17023,cmd+1,67001])
            delta=codec.decode(response.frames[0]['payload'],'sc_17023')
            for item in delta['normal_items']:self.assertEqual(item['num'],self.balance(item['id']))
            if cmd in (67002,67010):self.assertEqual(self.balance(56),0)
        self.assertEqual(self.db.get_admin_cat_list(UID)[0]['skill_level'],2)

    def test_required_ack_state_or_delta_failure_rolls_back_all_four_mutators(self):
        for cmd in (67006,67008,67002,67010):
            for target in ('ack','state','delta'):
                self.prepare(cmd);before=self.state();ctx=self.conn.to_handler_ctx()
                if target=='delta':
                    real=generator.gen_payload
                    change=patch.object(generator,'gen_payload',side_effect=lambda number,*a,**kw:None if number==17023 else real(number,*a,**kw))
                else:
                    real=ctx.codec_encode;name='sc_'+str(cmd+1) if target=='ack' else 'sc_67001'
                    change=patch.object(ctx,'codec_encode',side_effect=lambda schema,*a,**kw:None if schema==name else real(schema,*a,**kw))
                with change:packet,response=self.call_kind(cmd)
                self.assertNotEqual(packet['result'],0,(cmd,target));self.assertEqual(response.scs,[cmd+1])
                self.assertEqual(self.state(),before,(cmd,target));self.assertEqual(self.call_kind(cmd)[0]['result'],0)

    def test_finish_queue_delete_after_write_failure_rolls_back_grants_and_can_retry(self):
        self.prepare(67006);before=self.state();real=self.db.remove_admin_cat_queue
        def fail(*a,**kw):real(*a,**kw);raise RuntimeError('after deleting memory queue')
        with patch.object(self.db,'remove_admin_cat_queue',side_effect=fail):packet,response=self.call_kind(67006)
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[67007]);self.assertEqual(self.state(),before)
        self.assertEqual(self.call_kind(67006)[0]['result'],0)


if __name__=='__main__':unittest.main(verbosity=2)
