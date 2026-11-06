"""A44/A45: actual Core operations, memory SQLite and encoded responses."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from equip_service import EquipService
import codec


class PreviewTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.execute('INSERT INTO equip(uid,id,race,race_preview,enchant_slots) VALUES(?,101,2,3,?)',
                        (UID,json.dumps([dict(id=1,effect_list=[dict(id=101,level=1)],
                                              preview_list=[[dict(id=102,level=1)],
                                                            [dict(id=103,level=1)]])])) )
        self.db.set_currency(UID,2,20000)

    def row(self):
        return self.db.get('equip',UID,'AND id=101')

    def slots(self):
        return json.loads(self.row()['enchant_slots'])[0]

    def race(self,confirm=True,equip=101):
        return self.call(13034,dict(equip_id=equip,confirm=confirm),13035)

    def enchant(self,index=1,confirm=True):
        data=dict(equip_id=101,enchant_slot_id=1,confirm=confirm)
        if index is not None:
            data['preview_index']=index
        return self.call(13030,data,13031)

    def test_repeat_race_rejects_without_clearing(self):
        answer,_=self.race()
        self.assertEqual(answer['result'],0)
        self.assertEqual((self.row()['race'],self.row()['race_preview']),(3,0))
        before=self.row()
        answer,response=self.race()
        self.assertEqual(answer['result'],2)
        self.assertEqual(response.scs,[13035])
        self.assertEqual(self.row(),before)
        payload=self.core.generator.gen_payload(13009,uid=UID,db=self.db)
        cold=codec.decode(payload,'sc_13009')
        self.assertEqual(cold['equip_list'][0]['race'],3)

    def test_invalid_race_preview_rejects(self):
        for preview in (0,6,7,999):
            with self.subTest(preview=preview):
                self.db.execute('UPDATE equip SET race_preview=? WHERE uid=? AND id=101',(preview,UID))
                before=self.row()
                self.assertEqual(self.race()[0]['result'],2)
                self.assertEqual(self.row(),before)

    def test_race_discard_and_new_preview(self):
        for _ in range(2):
            self.assertEqual(self.race(False)[0]['result'],0)
            self.assertEqual((self.row()['race'],self.row()['race_preview']),(2,0))
        for race in (1,2,3,4,5,9):
            self.db.execute('UPDATE equip SET race_preview=? WHERE uid=? AND id=101',(race,UID))
            self.assertEqual(self.race()[0]['result'],0)
            self.assertEqual(self.row()['race'],race)

    def test_unknown_equip_cannot_modify_other_item(self):
        before=self.row()
        self.assertEqual(self.race(equip=999)[0]['result'],406)
        self.assertEqual(self.row(),before)

    def test_invalid_enchant_index_never_consumes_first(self):
        for index in (0,3,99,4294967295):
            for confirm in (True,False):
                with self.subTest(index=index,confirm=confirm):
                    before=self.row()
                    answer,response=self.enchant(index,confirm)
                    self.assertEqual(answer['result'],2)
                    self.assertEqual(response.scs,[13031])
                    self.assertEqual(self.row(),before)

    def test_selected_second_preview_is_saved(self):
        self.assertEqual(self.enchant(2)[0]['result'],0)
        self.assertEqual(self.slots()['effect_list'],[dict(id=103,level=1)])
        self.assertEqual(self.slots()['preview_list'],[[dict(id=102,level=1)]])

    def test_discard_selected_then_default_first(self):
        self.assertEqual(self.enchant(2,False)[0]['result'],0)
        self.assertEqual(self.slots()['effect_list'],[dict(id=101,level=1)])
        self.assertEqual(self.enchant(None)[0]['result'],0)
        self.assertEqual(self.slots()['effect_list'],[dict(id=102,level=1)])
        self.assertEqual(self.slots()['preview_list'],[])
        before=self.row()
        self.assertEqual(self.enchant()[0]['result'],2)
        self.assertEqual(self.row(),before)

    def test_write_failure_rolls_back_and_retry_succeeds(self):
        before=self.row()
        real=self.db.execute
        def broken(sql,*args,**kwargs):
            if sql.startswith('UPDATE equip SET enchant_slots='):
                raise RuntimeError('injected write failure')
            return real(sql,*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=broken):
            self.assertNotEqual(self.enchant(2)[0]['result'],0)
        self.assertEqual(self.row(),before)
        self.assertEqual(self.enchant(2)[0]['result'],0)


if __name__=='__main__':
    unittest.main()
