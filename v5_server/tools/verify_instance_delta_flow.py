"""A63: real draw/mail/box instance deltas and single hero refresh, memory only."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_hero_acquisition import AcquisitionTests
from verify_item_use_atomic_random import entry
from verify_report_small_fixes import UID
from verify_servant_awake_guard import AwakeTests
import codec
import draw_service as draw
import generator
import hero_codec
import illustrated_listener
import operations
from inventory_service import InventoryService as Inventory
from mail_service import MailService


ROOT=Path(__file__).resolve().parents[2]


class InstanceDeltaTests(AcquisitionTests):
    def setUp(self):
        super().setUp()
        for iid,kind in ((2510000,9),(2310001,9),(2530003,9),(2510999,9),(2530004,9),
                         (210001,7),(220001,7),(30023,5),(30025,5),(30076,5)):
            self.db.execute('INSERT OR IGNORE INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))
        self.db.execute('INSERT INTO servant(uid,id,prefab_id,stage,is_locked,owned) VALUES(?,500,2510000,4,1,1)',(UID,))
        self.db.upsert('hero',UID,dict(hero_codec.initial_hero_row(1084,100),weapon_servant_uid=500),keys=('uid','id'))
        self.db.execute('INSERT INTO equip(uid,id,prefab_id,exp,is_lock,race) VALUES(?,700,210001,123,1,3)',(UID,))
        self.mail=MailService(self.db)
        owner=patch.object(MailService,'_instance',self.mail);owner.start();self.addCleanup(owner.stop)

    def state(self):
        result=super().state()
        for table in ('servant','servant_history','equip','mail','illustrated','draw_group_state'):
            result[table]=self.db.query('SELECT * FROM '+table+' ORDER BY rowid')
        return result

    def send(self,items):
        return self.mail.send_mail(UID,'instance probe','probe',attachments=items)

    def seed_legacy_mail(self,items):
        # Current send_mail deliberately disallows equipment attachments.
        # Seed an old-format row only in memory to test its existing claimant.
        mid=self.send([])
        self.db.execute('UPDATE mail SET attachment_json=?,attach_flag=1 WHERE uid=? AND mail_id=?',
                        (json.dumps(items),UID,mid))
        return mid

    def claim(self,mid):
        return self.call(30004,dict(id=mid),30005)

    def delta(self,response):
        self.assertEqual(response.scs.count(17023),1)
        return next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)

    def project(self,before,response):
        # Limited projection of native AddNewServant/AddEquip; existing entries
        # retain their stored stage/lock/experience because only new UIDs arrive.
        servants={r['id']:dict(prefab=r['prefab_id'],stage=r['stage'],locked=r['is_locked']) for r in before['servant']}
        equips={r['id']:dict(prefab=r['prefab_id'],exp=r['exp'],locked=r['is_lock'],race=r['race']) for r in before['equip']}
        delta=self.delta(response)
        weapons=delta.get('weapon_list',[]);equipment=delta.get('equip_list',[])
        self.assertEqual(len(weapons),len({r['uid'] for r in weapons}))
        self.assertEqual(len(equipment),len({r['equip_id'] for r in equipment}))
        for row in weapons:
            if row['item']['num']==0:servants.pop(row['uid'],None)
            else:
                self.assertNotIn(row['uid'],servants)
                servants[row['uid']]=dict(prefab=row['item']['id'],stage=1,locked=0)
        for row in equipment:
            if row['num']==0:equips.pop(row['equip_id'],None)
            else:
                self.assertNotIn(row['equip_id'],equips)
                equips[row['equip_id']]=dict(prefab=row['prefab_id'],exp=0,locked=0,race=row.get('race',0))
        actual=self.state()
        self.assertEqual(servants,{r['id']:dict(prefab=r['prefab_id'],stage=r['stage'],locked=r['is_locked']) for r in actual['servant']})
        self.assertEqual(equips,{r['id']:dict(prefab=r['prefab_id'],exp=r['exp'],locked=r['is_lock'],race=r['race']) for r in actual['equip']})
        self.assertEqual(self.db.get('hero',UID,'AND id=1084')['weapon_servant_uid'],500)
        return delta

    def draw_servants(self,count=1):
        self.db.execute('INSERT OR IGNORE INTO draw_pool(pool_id,detail_json) VALUES(10002,?)',
                        (json.dumps(dict(pool_type=2,pool_selected_type=9,cost_once=[19,1],cost_ten_times=[19,10])),))
        with patch.object(self.svc,'calc_ssr_prob',return_value=1),patch.object(draw.random,'random',return_value=0.5),patch.object(draw.random,'choice',return_value=2510000):
            return self.call(16010,dict(pool=10002,type=count),16011)

    def test_ten_draws_send_only_new_instances_and_preserve_equipped_stage_four(self):
        self.db.set_currency(UID,19,20);before=self.state()
        with patch.object(generator,'gen_payload',wraps=generator.gen_payload) as generate:
            packet,response=self.draw_servants(10)
        self.assertEqual(packet['result'],0)
        self.assertEqual(len(packet['item']),10)
        self.assertNotIn(46011,response.scs)
        self.assertFalse(any(c.args and c.args[0]==46011 for c in generate.call_args_list))
        delta=self.project(before,response)
        self.assertEqual(len(delta['weapon_list']),10)
        self.assertEqual((self.balance(19),self.balance(36)),(10,1000))
        old=self.db.query('SELECT stage,is_locked FROM servant WHERE uid=? AND id=500',(UID,))[0]
        self.assertEqual(old,dict(stage=4,is_locked=1))

    def test_legacy_mail_instances_match_delta_and_online_illustration_inputs(self):
        mid=self.seed_legacy_mail([[2310001,2],[210001,2],[220001,1]])
        before=self.state();packet,response=self.claim(mid)
        self.assertEqual(packet['result'],0)
        self.assertEqual(packet['success_mail_ids'],[mid])
        self.assertLess(response.scs.index(17023),response.scs.index(30005))
        self.assertNotIn(46011,response.scs);self.assertNotIn(13009,response.scs)
        delta=self.project(before,response)
        self.assertEqual((len(delta['weapon_list']),len(delta['equip_list'])),(2,3))
        self.assertEqual({r['prefab_id'] for r in delta['equip_list']},{210001,220001})
        self.assertTrue(self.db.query("SELECT 1 FROM illustrated WHERE uid=? AND kind='servant' AND item_id=2310001",(UID,)))
        row=self.db.query("SELECT pos_list_json FROM illustrated WHERE uid=? AND kind='equip' AND item_id=1",(UID,))[0]
        self.assertEqual(set(json.loads(row['pos_list_json'])),{1,2})
        before=self.state();packet,response=self.claim(mid)
        self.assertFalse(packet.get('attachment_list'))
        self.assertEqual(self.state(),before)
        self.assertFalse(self.delta(response).get('weapon_list'))
        self.assertFalse(self.delta(response).get('equip_list'))

    def test_three_real_optional_servant_boxes_each_send_one_new_uid(self):
        cfg=__import__('inventory_service')._get_item_usable_cfg()
        for iid in (30023,30025,30076):
            with self.subTest(iid=iid):
                self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,?,1)',(UID,iid))
                before=self.state();packet,response=self.use([entry(iid,choices=[1])])
                self.assertEqual(packet['result'],0)
                delta=self.project(before,response)
                self.assertEqual(len(delta['weapon_list']),1)
                self.assertEqual(delta['weapon_list'][0]['item']['id'],cfg[str(iid)]['param'][0][0])
                self.assertEqual(self.balance(iid),0)
                self.assertNotIn(46011,response.scs)

    def test_box_hero_refresh_is_generated_and_sent_once(self):
        with self.bundle([[1075,1]]),patch.object(hero_codec,'build_single_hero_frame',wraps=hero_codec.build_single_hero_frame) as build:
            packet,response=self.use([entry(30009)])
        self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs.count(14007),1)
        self.assertEqual(build.call_count,1)
        self.assertLess(response.scs.index(14007),response.scs.index(17013))

    def test_mail_hero_refresh_is_generated_once_and_still_mandatory(self):
        mid=self.send([[1075,1]])
        with patch.object(hero_codec,'build_single_hero_frame',wraps=hero_codec.build_single_hero_frame) as build:
            packet,response=self.claim(mid)
        self.assertEqual(packet['result'],0)
        self.assertEqual(response.scs.count(14007),1)
        self.assertEqual(build.call_count,1)
        self.assertLess(response.scs.index(14007),response.scs.index(30005))

    def test_mail_missing_required_hero_frame_rolls_back_then_retries(self):
        mid=self.send([[1075,1]]);before=self.state()
        with patch.object(hero_codec,'build_single_hero_frame',return_value=None):packet,response=self.claim(mid)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[30005]);self.assertEqual(self.state(),before)
        self.assertEqual(self.claim(mid)[0]['result'],0)

    def test_legacy_mail_instance_delta_failure_rolls_back_receipt_and_assets(self):
        mid=self.seed_legacy_mail([[2310001,2],[210001,2]])
        before=self.state();gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):
            packet,response=self.claim(mid)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[30005]);self.assertEqual(self.state(),before)
        packet,response=self.claim(mid)
        self.assertEqual(packet['result'],0)
        delta=self.project(before,response)
        self.assertEqual((len(delta['weapon_list']),len(delta['equip_list'])),(2,2))

    def test_draw_missing_instance_collection_cannot_fall_back_to_success(self):
        self.db.set_currency(UID,19,20)
        # Set up the pool outside the measured operation snapshot.
        self.db.execute('INSERT INTO draw_pool(pool_id,detail_json) VALUES(10002,?)',(json.dumps(dict(cost_once=[19,1])),))
        before=self.state();grant=Inventory.grant_items
        def broken(ctx,*args,**kwargs):
            result=grant(ctx,*args,**kwargs)
            ctx.added_weapons.clear()
            return result
        with patch.object(Inventory,'grant_items',side_effect=broken):packet,response=self.draw_servants()
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[16011]);self.assertEqual(self.state(),before)


if __name__=='__main__':
    before='--before' in sys.argv
    if before:
        path=ROOT/'archive/instance_delta_flow_20261103/before/v5_server/operations.py'
        tree=ast.parse(path.read_text('utf-8-sig'));namespace=dict(vars(operations))
        method=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_refresh_frames')
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
        operations._refresh_frames=namespace['_refresh_frames']
        for name in ('DrawOp','UseItemOp','MailReceiveOp'):
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
            method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='respond')
            exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
            getattr(operations,name).respond=namespace['respond']
    names=[n for n in InstanceDeltaTests.__dict__ if n.startswith('test_')] if before else unittest.defaultTestLoader.getTestCaseNames(InstanceDeltaTests)
    suite=unittest.TestSuite(InstanceDeltaTests(n) for n in names)
    if not before:
        suite.addTests(AwakeTests(n) for n in ('test_preserves_uid_refinement_equipped_reference_and_other_instances','test_packet_failure_rolls_back_instance_currency_notifications_then_retry'))
        from verify_draw_atomic_response import DrawAtomicTests
        suite.addTest(DrawAtomicTests('test_servant_delta_failure_rolls_back_instances_and_ticket'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
