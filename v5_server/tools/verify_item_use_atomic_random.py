"""A53/A56: actual Core/Inventory/codec, deterministic RNG, memory SQL only."""
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import codec
import inventory_service as inv
from inventory_service import InventoryService as Inventory

ROOT=Path(__file__).resolve().parents[2]


def entry(iid,count=1,choices=None):
    return dict(item_info=dict(id=iid,num=count,time_valid=0),use_list=choices or [])


class UseTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for iid,kind in ((20001,4),(30031,5),(30009,5),(30005,5),(30006,5),(30007,5),
                         (11075,3),(11076,3),(1,1),(2,1),(4,1),(33,1),(40,1)):
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)',(iid,kind))
        for iid in (20001,30031,30009):
            self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,?,3)',(UID,iid))
        self.db.set_currency(UID,4,0)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in
                ('users','currency','material','hero_piece','achievement','chat_sticker','player_card')}

    def use(self,items):return self.call(17012,dict(use_item_list=items),17013)

    def reject(self,items,code=None):
        before=self.state()
        packet,response=self.use(items)
        self.assertNotEqual(packet['result'],0)
        if code is not None:self.assertEqual(packet['result'],code)
        self.assertFalse(packet.get('drop_list'))
        self.assertEqual(response.scs,[17013])
        self.assertEqual(self.state(),before)

    def test_duplicate_entries_are_preflighted_as_total_consumption(self):
        self.db.execute('UPDATE material SET num=1 WHERE uid=? AND id=20001',(UID,))
        with patch.object(Inventory,'cost_item',wraps=Inventory.cost_item) as cost:
            self.reject([entry(20001),entry(20001)],3)
        self.assertEqual(cost.call_count,0)

    def test_valid_duplicate_uses_and_distinct_choices_are_preserved(self):
        before=self.balance(4)
        packet,response=self.use([entry(20001),entry(20001)])
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(20001),1)
        self.assertEqual(self.balance(4),before+60)
        self.assertLess(response.scs.index(17023),response.scs.index(17013))
        self.assertLess(response.scs.index(17025),response.scs.index(17013))
        packet,_=self.use([entry(30009,choices=[1]),entry(30009,choices=[2])])
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(30009),1)
        self.assertEqual((self.balance(11075),self.balance(11076)),(1,1))

    def test_later_cost_failure_rolls_back_core_and_direct_structured_api(self):
        original=Inventory.cost_item
        def cost(ctx,uid,iid,count=1):
            return False if iid==30031 else original(ctx,uid,iid,count)
        items=[entry(20001),entry(30031)]
        with patch.object(Inventory,'cost_item',side_effect=cost):self.reject(items,3)
        ctx=self.conn.to_handler_ctx();ctx.touched_items={77};before=self.state()
        with patch.object(Inventory,'cost_item',side_effect=cost):
            result=Inventory.use_items(ctx,UID,items)
        self.assertEqual(result['result'],3)
        self.assertEqual(self.state(),before)
        self.assertEqual(ctx.touched_items,{77})

    def test_write_after_reward_failure_rolls_back_all_effects(self):
        grant=Inventory.grant_items
        def broken(*args,**kwargs):
            grant(*args,**kwargs)
            raise RuntimeError('isolated failure after reward write')
        with patch.object(inv.random,'randint',return_value=100),patch.object(Inventory,'grant_items',side_effect=broken):
            self.reject([entry(30031)])

    def test_ack_or_required_delta_failure_rolls_back_then_retries(self):
        import generator
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_17013' else encode(n,d)):
            self.reject([entry(30031)])
        gen=generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw:None if cmd==17023 else gen(cmd,**kw)):
            self.reject([entry(30031)])
        with patch.object(inv.random,'randint',return_value=100):
            packet,_=self.use([entry(30031)])
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(30031),2)
        self.assertEqual(self.balance(40),50)

    def test_real_30031_at_random_100_only_grants_guaranteed_line(self):
        with patch.object(inv.random,'randint',return_value=100):
            packet,response=self.use([entry(30031)])
        self.assertEqual(packet['result'],0)
        self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},{40:50})
        self.assertEqual(self.balance(40),50)
        self.assertEqual(self.balance(30031),2)
        self.assertEqual(self.balance(1),0)
        delta=next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)
        balances={r['id']:r['num'] for r in delta['normal_items']}
        self.assertEqual((balances[40],balances[30031]),(50,2))
        self.assertLess(response.scs.index(17023),response.scs.index(17013))

    def pool(self,random_items,base=None,weight=None,weight_count=0):
        cfg=copy.deepcopy(inv._get_drop_cfg())
        cfg['100010']=dict(random=random_items,base=base or [],weight=weight or [],weight_count=weight_count)
        return patch.object(inv,'_DROP_CFG_CACHE',cfg)

    def test_zero_hundred_two_fields_and_probability_boundary(self):
        for roll,expected in ((20,{2:2,40:3,33:4}),(21,{40:3,33:4})):
            with self.subTest(roll=roll),self.pool([[1,1,0],[2,2,20],[40,3,100],[33,4]]),patch.object(inv.random,'randint',return_value=roll):
                packet,_=self.use([entry(30031)])
                self.assertEqual(packet['result'],0)
                self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},expected)

    def test_multi_use_rolls_each_time_and_preserves_base_weight(self):
        with self.pool([[40,3,20]],base=[[2,7,100]],weight=[[33,2,1]],weight_count=2),patch.object(inv.random,'randint',side_effect=lambda a,b:min(20,b)) as rng:
            packet,_=self.use([entry(30031,2)])
        self.assertEqual(packet['result'],0)
        self.assertEqual({r['id']:r['num'] for r in packet['drop_list']},{2:14,40:6,33:8})
        self.assertEqual(rng.call_count,6)
        self.assertEqual(self.balance(30031),1)

    def test_legitimate_random_miss_can_consume_with_empty_drop(self):
        with self.pool([[1,5,0]]),patch.object(inv.random,'randint',return_value=100):
            packet,response=self.use([entry(30031)])
        self.assertEqual(packet['result'],0)
        self.assertFalse(packet.get('drop_list'))
        self.assertEqual(self.balance(30031),2)
        self.assertLess(response.scs.index(17023),response.scs.index(17013))

    def test_replay_keepsakes_remain_owned_for_repeated_entries(self):
        from birthday_service import commemorative_items
        iid=next(iter(commemorative_items()))
        self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,4)',(iid,))
        self.db.execute('INSERT INTO material(uid,id,num) VALUES(?,?,1)',(UID,iid))
        before=self.state()
        packet,_=self.use([entry(iid),entry(iid)])
        self.assertEqual(packet['result'],0)
        self.assertFalse(packet.get('drop_list'))
        self.assertEqual(self.state(),before)


if __name__=='__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        path=ROOT/'archive/item_use_atomic_random_20261103/before/inventory_service.py'
        cls=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='InventoryService')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='use_items')
        fn.decorator_list=[];ns=dict(vars(inv))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
        Inventory.use_items=classmethod(ns['use_items'])
    unittest.main(verbosity=2)
