"""A40/A41: real Core, fixed equip positions, memory SQL and response shapes."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from hero_service import HeroService


class OwnershipTests(MemoryTests):
    def setUp(self):
        super().setUp()
        for hid,slots in ((1084,{'1':101}),(1039,{'1':201})):
            self.db.execute('INSERT INTO hero(uid,id,unlock,level,equip_slot) VALUES(?,?,1,70,?)',
                            (UID,hid,json.dumps(slots)))
        for pos in range(1,7):
            self.db.execute('INSERT INTO equip(uid,id,prefab_id,hero_id) VALUES(?,?,?,?)',
                            (UID,100+pos,500001+10000*pos,1084 if pos==1 else 0))
        self.db.execute('INSERT INTO equip(uid,id,prefab_id,hero_id) VALUES(?,201,510001,1039)',(UID,))
        self.svc=HeroService(self.db)

    def snapshot(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY id') for t in ('hero','equip')}

    def slots(self,hid):
        return json.loads(self.db.get('hero',UID,'AND id=?',(hid,))['equip_slot'])

    def owner(self,eid):
        return self.db.get('equip',UID,'AND id=?',(eid,))['hero_id']

    def single(self,hid=1039,eid=101,pos=1):
        return self.call(13012,dict(hero_id=hid,equip_id=eid,pos=pos),13013)

    def quick(self,items=None,hid=1039):
        if items is None:items=[dict(equip_id=101,pos=1)]
        return self.call(13026,dict(hero_id=hid,use_equip_list=items),13027)

    def reject(self,quick=False,**kwargs):
        before=self.snapshot()
        answer,response=self.quick(**kwargs) if quick else self.single(**kwargs)
        if quick:
            self.assertTrue(answer.get('result'))
            self.assertTrue(all(row['result']!=0 for row in answer['result']))
            self.assertEqual(response.scs,[13027])
        else:
            self.assertNotEqual(answer['result'],0)
        self.assertEqual(self.snapshot(),before)

    def test_missing_target_is_rejected_by_both_entries(self):
        for hid in (0,99999999):
            self.reject(hid=hid)
            self.reject(quick=True,hid=hid)

    def test_locked_hero_cannot_take_an_equipment(self):
        self.db.execute('UPDATE hero SET unlock=0 WHERE uid=? AND id=1039',(UID,))
        self.reject()
        self.reject(quick=True)

    def test_wrong_fixed_position_cannot_duplicate_same_item(self):
        self.reject(hid=1084,pos=2)
        self.reject(quick=True,hid=1084,items=[dict(equip_id=101,pos=2)])
        self.assertEqual(self.slots(1084),{'1':101})

    def test_same_slot_replay_preserves_unique_reference(self):
        self.assertEqual(self.single(hid=1084)[0]['result'],0)
        self.assertEqual(self.single(hid=1084)[0]['result'],0)
        self.assertEqual(self.quick(hid=1084)[0]['result'],[dict(equip_id=101,result=0)])
        self.assertEqual(self.slots(1084),{'1':101})
        self.assertEqual(self.owner(101),1084)

    def test_duplicate_ids_or_positions_reject_whole_quick_batch(self):
        for items in ([dict(equip_id=101,pos=1),dict(equip_id=101,pos=2)],
                      [dict(equip_id=101,pos=1),dict(equip_id=201,pos=1)]):
            self.reject(quick=True,items=items)

    def test_invalid_second_item_does_not_move_first(self):
        self.reject(quick=True,items=[dict(equip_id=101,pos=1),dict(equip_id=999999,pos=2)])
        self.reject(quick=True,items=[dict(equip_id=101,pos=1),dict(equip_id=102,pos=7)])

    def test_cross_hero_transfer_detaches_old_and_replaced_owner(self):
        self.assertEqual(self.single()[0]['result'],0)
        self.assertEqual(self.slots(1084),{})
        self.assertEqual(self.slots(1039),{'1':101})
        self.assertEqual((self.owner(101),self.owner(201)),(1039,0))

    def test_valid_six_slots_quick_batch_has_one_item_per_position(self):
        items=[dict(equip_id=100+p,pos=p) for p in range(1,7)]
        answer,response=self.quick(items)
        self.assertEqual(answer['result'],[dict(equip_id=100+p,result=0) for p in range(1,7)])
        self.assertEqual(self.slots(1039),{str(p):100+p for p in range(1,7)})
        self.assertEqual(self.slots(1084),{})
        self.assertTrue(all(self.owner(100+p)==1039 for p in range(1,7)))
        self.assertEqual(self.owner(201),0)
        self.assertEqual(response.scs[-1],13027)

    def test_sql_failure_rolls_back_and_quick_error_is_repeated_message(self):
        real=self.db.execute
        def fail(sql,*args,**kwargs):
            if sql.startswith('UPDATE hero SET equip_slot=') and args and args[0][-1]==1039:
                raise RuntimeError('injected destination save failure')
            return real(sql,*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=fail):
            self.reject()
            self.reject(quick=True)
        self.assertEqual(self.quick()[0]['result'],[dict(equip_id=101,result=0)])

    def test_direct_service_failure_rolls_back_ownership(self):
        from types import SimpleNamespace
        real=self.db.execute
        before=self.snapshot()
        def fail(sql,*args,**kwargs):
            if sql.startswith('UPDATE hero SET equip_slot=') and args and args[0][-1]==1039:
                raise RuntimeError('injected destination save failure')
            return real(sql,*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.svc.swap_equip(SimpleNamespace(db=self.db),UID,1039,101,1)
        self.assertEqual(self.snapshot(),before)

    def test_zero_destination_update_is_rejected_and_rolled_back(self):
        from types import SimpleNamespace
        real=self.db.execute
        def zero_rows(sql,*args,**kwargs):
            result=real(sql,*args,**kwargs)
            if sql.startswith('UPDATE hero SET equip_slot=') and args and args[0][-1]==1039:
                return SimpleNamespace(rowcount=0)
            return result
        with patch.object(self.db,'execute',side_effect=zero_rows):
            self.reject()
            self.reject(quick=True)

    def test_empty_quick_request_has_a_valid_empty_reply(self):
        before=self.snapshot()
        answer,response=self.quick(items=[])
        self.assertEqual(answer.get('result',[]),[])
        self.assertEqual(response.scs,[13027])
        self.assertEqual(self.snapshot(),before)
        self.reject(quick=True,items=[],hid=99999999)


if __name__=='__main__':
    unittest.main()
