"""A59/A60: capped reward display and mapped sticker ownership, memory only."""
import ast
import copy
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_item_use_atomic_random import UseTests, entry
from verify_report_small_fixes import UID
import codec
import inventory_service as inv
from inventory_service import InventoryService as Inventory


ROOT=Path(__file__).resolve().parents[2]
MAT=99999991


class OverflowTests(UseTests):
    def setUp(self):
        super().setUp()
        for iid,kind in ((MAT,6),(90001,20),(6051,21),(99999992,8),(99999993,12)):
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))
        self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,?,8)',(UID,MAT))

    @contextmanager
    def reward(self, rewards, targets=None, limit=10):
        self.db.execute('UPDATE material SET num=30 WHERE uid=? AND id=30009',(UID,))
        usable=copy.deepcopy(inv._get_item_usable_cfg())
        usable['30009'].update(sub_type=501,param=rewards)
        exchange=copy.deepcopy(inv._get_exchange_cfg())
        if targets is not None:exchange[str(MAT)]=dict(limit=limit,num_exchange_item=targets)
        with patch.object(inv,'_ITEM_USABLE_CFG_CACHE',usable),patch.object(inv,'_ITEM_EXCHANGE_CFG_CACHE',exchange):
            yield

    def drops(self, rewards, targets=None, limit=10):
        with self.reward(rewards,targets,limit):
            packet,response=self.use([entry(30009)])
        self.assertEqual(packet['result'],0)
        self.assertLess(response.scs.index(17023),response.scs.index(17013))
        return {r['id']:r['num'] for r in packet.get('drop_list',[])},response

    def test_no_overflow_exact_cap_partial_and_full_single_target(self):
        for have,count,expected in ((8,1,{MAT:1}),(8,2,{MAT:2}),(8,5,{MAT:2,2:30}),(10,5,{2:50})):
            with self.subTest(have=have,count=count):
                self.db.execute('UPDATE material SET num=? WHERE uid=? AND id=?',(have,UID,MAT))
                before=self.balance(2)
                actual,_=self.drops([[MAT,count]],[[2,10]])
                self.assertEqual(actual,expected)
                self.assertEqual(self.balance(MAT),min(10,have+count))
                self.assertEqual(self.balance(2)-before,expected.get(2,0))

    def test_multiple_targets_subtract_surplus_once(self):
        drops,response=self.drops([[MAT,5]],[[2,10],[1,1]])
        self.assertEqual(drops,{MAT:2,2:30,1:3})
        self.assertEqual((self.balance(MAT),self.balance(2),self.balance(1)),(10,30,3))
        delta=next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)
        balances={r['id']:r['num'] for r in delta['normal_items']}
        self.assertEqual((balances[MAT],balances[2],balances[1]),(10,30,3))

    def test_no_target_partial_and_full_caps_do_not_display_lost_units(self):
        drops,_=self.drops([[MAT,5]],[])
        self.assertEqual(drops,{MAT:2})
        self.assertEqual(self.balance(MAT),10)
        drops,_=self.drops([[MAT,5]],[])
        self.assertEqual(drops,{})
        self.assertEqual(self.balance(MAT),10)

    def test_repeated_source_rows_and_direct_target_rewards_merge_exactly(self):
        drops,_=self.drops([[MAT,3],[MAT,4],[2,7]],[[2,10],[1,1]])
        self.assertEqual(drops,{MAT:2,2:57,1:5})
        self.assertEqual((self.balance(MAT),self.balance(2),self.balance(1)),(10,57,5))

    def test_no_target_duplicate_scene_skin_and_decoration_not_reawarded(self):
        self.db.execute('INSERT INTO user_scene(uid,scene_id,lasted_time) VALUES(?,6051,0)',(UID,))
        self.db.execute('INSERT INTO player_skin_unlocked(uid,skin_id) VALUES(?,99999992)',(UID,))
        self.db.execute("INSERT INTO player_card(uid,kind,item_id,obtained) VALUES(?,?,99999993,1)",(UID,inv.COSMETIC_KIND_MAP[12]))
        cfg=copy.deepcopy(inv._get_exchange_cfg())
        for iid in (6051,99999992,99999993):cfg[str(iid)]=dict(limit=1,num_exchange_item=[])
        with patch.object(inv,'_ITEM_EXCHANGE_CFG_CACHE',cfg):
            for iid in (6051,99999992,99999993):
                with self.subTest(iid=iid):
                    # reward() otherwise supplies a complete exchange snapshot.
                    with self.reward([[iid,2]]):
                        packet,response=self.use([entry(30009)])
                    self.assertEqual(packet['result'],0)
                    self.assertFalse(packet.get('drop_list'))
                    self.assertNotIn(32009,response.scs)

    def test_mapped_owned_sticker_with_no_material_converts_all_received_units(self):
        self.db.execute("INSERT INTO chat_sticker(uid,emoji_id) VALUES(?,138)",(UID,))
        for held in (None,0,1):
            with self.subTest(held=held):
                self.db.execute('DELETE FROM material WHERE uid=? AND id=90001',(UID,))
                if held is not None:self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,90001,?)',(UID,held))
                before=self.balance(1)
                drops,response=self.drops([[90001,2]])
                self.assertEqual(drops,{1:40})
                self.assertEqual(self.balance(1)-before,40)
                self.assertEqual(self.balance(90001),held or 0)
                self.assertNotIn(12039,response.scs)
        self.assertEqual(self.db.query('SELECT emoji_id FROM chat_sticker WHERE uid=?',(UID,)),[dict(emoji_id=138)])

    def test_new_sticker_activates_real_id_once_then_converts_extra_copies(self):
        drops,response=self.drops([[90001,3]])
        self.assertEqual(drops,{90001:1,1:40})
        self.assertEqual(self.balance(90001),1)
        self.assertEqual(self.balance(1),40)
        self.assertEqual(self.db.query('SELECT emoji_id FROM chat_sticker WHERE uid=?',(UID,)),[dict(emoji_id=138)])
        self.assertLess(response.scs.index(12039),response.scs.index(17013))

    def test_missing_mapping_keeps_explicit_same_id_fallback(self):
        self.db.execute('INSERT INTO chat_sticker(uid,emoji_id) VALUES(?,90001)',(UID,))
        with patch.object(inv,'get_sticker_item_map',return_value={}):
            drops,_=self.drops([[90001,1]])
        self.assertEqual(drops,{1:20})
        self.assertEqual(self.balance(90001),0)

    def test_conversion_write_failure_rolls_back_source_targets_and_box(self):
        original=self.db.execute
        def fail(sql,args=()):
            if 'INSERT INTO currency' in sql and len(args)>1 and args[1]==1:
                raise RuntimeError('isolated second conversion target failure')
            return original(sql,args)
        with self.reward([[MAT,5]],[[2,10],[1,1]]),patch.object(self.db,'execute',side_effect=fail):
            self.reject([entry(30009)])
        drops,_=self.drops([[MAT,5]],[[2,10],[1,1]])
        self.assertEqual(drops,{MAT:2,2:30,1:3})


if __name__=='__main__':
    before='--before' in sys.argv
    if before:
        path=ROOT/'archive/overflow_ownership_20261103/before/v5_server/inventory_service.py'
        cls=next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n,ast.ClassDef) and n.name=='InventoryService')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='grant_items')
        method.decorator_list=[];namespace=dict(vars(inv))
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
        Inventory.grant_items=classmethod(namespace['grant_items'])
    names=[name for name in OverflowTests.__dict__ if name.startswith('test_')] if before else unittest.defaultTestLoader.getTestCaseNames(OverflowTests)
    suite=unittest.TestSuite(OverflowTests(name) for name in names)
    if not before:
        from verify_all_cosmetic_mail import CosmeticTests
        suite.addTests(CosmeticTests(name) for name in ('test_repeated_source_rows_subtract_surplus_once_per_conversion','test_box_use_retains_first_sticker_when_two_conversion_targets_exist'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
