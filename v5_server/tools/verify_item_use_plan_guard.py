"""A54: preflight before any debit/effect, real Core/SQL/Inventory/codec."""
import ast
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from verify_item_use_atomic_random import UseTests, entry
from verify_report_small_fixes import UID
import inventory_service as inv
from inventory_service import InventoryService as Inventory

ROOT=Path(__file__).resolve().parents[2]


class PlanTests(UseTests):
    def setUp(self):
        super().setUp()
        for iid,kind in ((99999991,4),(41101,6),(90001,20),(20013,4)):
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))
            self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,?,2)',(UID,iid))
        self.db.execute("INSERT INTO mythic_final_progress(uid,now_difficulty,can_choose_list,receive_reward_list,clear_list,challenge_info_json,update_ts) VALUES(?,10,'[1,2]','[1]','[1]','[{\"team_id\":1,\"clear_state\":1,\"use_time\":45}]',?)",(UID,int(time.time())))
        self.db.execute('INSERT INTO mythic_progress_reward(uid,reward_id) VALUES(?,1)',(UID,))
        self.db.execute('INSERT INTO mythic_progress_partition(uid,partition_id,star) VALUES(?,1,3)',(UID,))

    def state(self):
        data=super().state()
        for table in ('mythic_final_progress','mythic_progress_reward','mythic_progress_partition'):
            data[table]=self.db.query('SELECT * FROM '+table+' ORDER BY rowid')
        return data

    def preflight_reject(self,items):
        with patch.object(Inventory,'cost_item',wraps=Inventory.cost_item) as debit:
            self.reject(items,2)
        self.assertEqual(debit.call_count,0)

    def test_invalid_choice_rejected_before_any_debit(self):
        count=len(inv._get_item_usable_cfg()['30009']['param'])
        for choices in ([0],[count+1],[1,count+1]):
            with self.subTest(choices=choices):self.preflight_reject([entry(30009,choices=choices)])

    def test_missing_cfg_and_unexecutable_real_config_do_not_consume(self):
        self.preflight_reject([entry(99999991)])
        cfg=copy.deepcopy(inv._get_item_usable_cfg());cfg.pop('20001')
        with patch.object(inv,'_ITEM_USABLE_CFG_CACHE',cfg):self.preflight_reject([entry(20001)])
        # Existing 404 scalar-link configuration has no implemented effect here.
        self.preflight_reject([entry(20013)])

    def test_later_invalid_entry_does_not_execute_earlier_state_effect(self):
        with patch.object(self.db,'reset_mythic_by_beacon',wraps=self.db.reset_mythic_by_beacon) as reset:
            self.preflight_reject([entry(41101),entry(99999991)])
        self.assertFalse(reset.called)
        self.preflight_reject([entry(20001),entry(30009,choices=[999])])
        self.preflight_reject([entry(90001),entry(99999991)])
        with patch.object(inv.random,'randint',wraps=inv.random.randint) as rng:
            self.preflight_reject([entry(30031),entry(99999991)])
        self.assertFalse(rng.called)

    def test_bad_reward_shapes_and_drop_configuration_rejected(self):
        for param in ([],[[4]],[[4,0]],[[0,30]],'broken'):
            cfg=copy.deepcopy(inv._get_item_usable_cfg());cfg['20001']['param']=param
            with self.subTest(param=param),patch.object(inv,'_ITEM_USABLE_CFG_CACHE',cfg):
                self.preflight_reject([entry(20001)])
        for pool in (None,{},dict(random='broken'),dict(random=[[40]]),dict(random=[[40,50,101]])):
            drops=copy.deepcopy(inv._get_drop_cfg())
            if pool is None:drops.pop('100010')
            else:drops['100010']=pool
            with self.subTest(pool=pool),patch.object(inv,'_DROP_CFG_CACHE',drops):
                self.preflight_reject([entry(30031)])

    def test_legal_selection_default_and_explicit_have_real_rewards(self):
        for choices in ([],[1]):
            packet,response=self.use([entry(30009,choices=choices)])
            self.assertEqual(packet['result'],0)
            self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},{11075:1})
            self.assertLess(response.scs.index(17023),response.scs.index(17013))
        self.assertEqual(self.balance(11075),2)

    def test_registered_beacon_can_succeed_with_no_usable_cfg_or_drops(self):
        cfg=copy.deepcopy(inv._get_item_usable_cfg());cfg.pop('41101')
        with patch.object(inv,'_ITEM_USABLE_CFG_CACHE',cfg):packet,response=self.use([entry(41101)])
        self.assertEqual(packet['result'],0)
        self.assertFalse(packet.get('drop_list'))
        self.assertEqual(self.balance(41101),1)
        self.assertFalse(self.db.query('SELECT * FROM mythic_progress_reward WHERE uid=?',(UID,)))
        self.assertFalse(self.db.query('SELECT * FROM mythic_progress_partition WHERE uid=?',(UID,)))
        self.assertEqual(self.db.get('mythic_final_progress',UID)['clear_list'],'[]')
        self.assertLess(response.scs.index(17023),response.scs.index(17013))

    def test_beacon_missing_handler_or_write_after_failure_does_not_consume(self):
        with patch.object(self.db,'reset_mythic_by_beacon',None):self.preflight_reject([entry(41101)])
        reset=self.db.reset_mythic_by_beacon
        def broken(uid):
            reset(uid)
            raise RuntimeError('isolated reset failure after state write')
        with patch.object(self.db,'reset_mythic_by_beacon',side_effect=broken):self.reject([entry(41101)])
        with patch.object(self.db,'reset_mythic_by_beacon',return_value=False):self.reject([entry(41101)])

    def test_sticker_state_effect_and_existing_notification_precede_ack(self):
        packet,response=self.use([entry(90001)])
        self.assertEqual(packet['result'],0)
        self.assertFalse(packet.get('drop_list'))
        self.assertEqual(self.balance(90001),1)
        self.assertTrue(self.db.query('SELECT 1 FROM chat_sticker WHERE uid=? AND emoji_id=138',(UID,)))
        self.assertLess(response.scs.index(12039),response.scs.index(17013))


if __name__=='__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        path=ROOT/'archive/item_use_plan_guard_20261103/before/inventory_service.py'
        cls=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='InventoryService')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_use_items')
        fn.decorator_list=[];ns=dict(vars(inv))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
        Inventory._use_items=classmethod(ns['_use_items'])
    # Run the eight new plan cases plus inherited ten regression cases once.
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PlanTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
