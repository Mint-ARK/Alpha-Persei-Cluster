"""A42/A43: client config, actual Core/Inventory and memory SQL."""
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
from extract_autochess_cfg import parse_lua_file
from equip_service import EquipService, material_give_back
from pathlib import Path


class BreakTests(MemoryTests):
    @classmethod
    def setUpClass(cls):
        config=Path(__file__).resolve().parents[2]/'decompiled_v2/x64/game/config'
        configs=parse_lua_file(config/'equipcfg.lua')
        cls.cfg=configs[510001]
        cls.cfg2=configs[210001]
        cls.curve=parse_lua_file(config/'equipexpcfg.lua')
        cls.costs=parse_lua_file(config/'equipmaterialcfg.lua')
        cls.need10=sum(cls.curve[level]['exp5'] for level in range(1,10))
        cls.need20=sum(cls.curve[level]['exp5'] for level in range(1,20))

    def setUp(self):
        super().setUp()
        c=self.cfg
        self.db.execute('INSERT INTO equip_cfg(prefab_id,starlevel,exp_variant,max_level,break_cost,break_times_max) VALUES(510001,?,?,?,?,?)',
                        (c['starlevel'],json.dumps(c['exp']),json.dumps(c['max_level']),json.dumps(c['break_cost']),c['break_times_max']))
        for level,row in self.curve.items():
            if isinstance(level,int):
                self.db.execute('INSERT INTO equip_exp_curve VALUES(?,?,?,?,?,?,?,?)',(level,*[row['exp'+str(i)] for i in range(1,8)]))
        for key,row in self.costs.items():
            if isinstance(key,int):
                self.db.execute('INSERT INTO equip_material_cfg VALUES(?,?)',(key,json.dumps(row['item_list'])))
        self.db.execute('INSERT INTO equip(uid,id,prefab_id,exp,now_break_level,hero_id,is_lock) VALUES(?,101,510001,0,0,0,0)',(UID,))
        self.db.set_currency(UID,2,1000000)
        for item in (40501,40502,40503,40504,40801,40802,40803):
            self.db.set_material(UID,item,1000)
        self.svc=EquipService(self.db)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('equip','material','currency')}

    def ready(self,exp=None):
        self.db.execute('UPDATE equip SET exp=? WHERE uid=? AND id=101',(self.need10 if exp is None else exp,UID))

    def request(self,quick=False,breaks=1,target=10,mats=None,food=None):
        if quick:
            data=dict(equip_id=101,break_times=breaks,target_level=target,mat_list=mats or [],equip_list=food or [])
            return self.call(13058,data,13059)
        return self.call(13022,dict(equip_id=101),13023)

    def reject(self,**kwargs):
        before=self.state()
        answer,_=self.request(**kwargs)
        self.assertNotEqual(answer['result'],0)
        self.assertEqual(self.state(),before)

    def test_missing_cost_template_rejects_normal_and_quick(self):
        self.ready()
        self.db.execute('DELETE FROM equip_material_cfg WHERE mat_id=1001')
        self.reject()
        self.reject(quick=True)

    def test_invalid_cost_json_and_missing_step_reference_are_not_free(self):
        self.ready()
        self.db.execute("UPDATE equip_material_cfg SET item_list='broken' WHERE mat_id=1001")
        self.reject()
        self.reject(quick=True)
        self.db.execute("UPDATE equip_cfg SET break_cost='[]' WHERE prefab_id=510001")
        self.reject()
        self.reject(quick=True)

    def test_explicit_free_template_is_allowed(self):
        self.ready()
        self.db.execute("UPDATE equip_material_cfg SET item_list='[]' WHERE mat_id=1001")
        before=self.balance(2)
        self.assertEqual(self.request()[0]['result'],0)
        self.assertEqual(self.db.get('equip',UID,'AND id=101')['now_break_level'],1)
        self.assertEqual(self.balance(2),before)

    def test_zero_experience_without_input_cannot_break(self):
        self.reject()
        self.reject(quick=True)

    def test_legal_one_click_uses_new_experience_to_reach_breakpoint(self):
        count=math.ceil(self.need10/500)
        answer,_=self.request(quick=True,mats=[dict(id=40803,num=count)])
        self.assertEqual(answer['result'],0)
        row=self.db.get('equip',UID,'AND id=101')
        self.assertEqual((row['exp'],row['now_break_level']),(self.need10,1))
        expected_gold=self.need10+sum(num for item,num in self.costs[1001]['item_list'] if item==2)
        self.assertEqual(self.balance(2),1000000-expected_gold)
        self.assertEqual(answer.get('mat_list',[]),material_give_back(count*500-self.need10))

    def test_legal_multi_break_and_missing_second_template(self):
        count=math.ceil(self.need20/500)
        self.db.execute('DELETE FROM equip_material_cfg WHERE mat_id=1002')
        self.reject(quick=True,breaks=2,target=20,mats=[dict(id=40803,num=count)])
        self.db.execute('INSERT INTO equip_material_cfg VALUES(1002,?)',(json.dumps(self.costs[1002]['item_list']),))
        answer,_=self.request(quick=True,breaks=2,target=20,mats=[dict(id=40803,num=count)])
        self.assertEqual(answer['result'],0)
        row=self.db.get('equip',UID,'AND id=101')
        self.assertEqual((row['exp'],row['now_break_level']),(self.need20,2))

    def test_not_enough_for_second_break_rejects_all(self):
        self.reject(quick=True,breaks=2,target=20,mats=[dict(id=40803,num=math.ceil(self.need10/500))])

    def test_over_cap_break_count_is_not_silently_truncated(self):
        self.ready(self.need20)
        self.reject(quick=True,breaks=6,target=60)

    def test_missing_materials_do_not_consume_experience_items(self):
        self.db.set_material(UID,40501,0)
        self.reject(quick=True,mats=[dict(id=40803,num=math.ceil(self.need10/500))])

    def test_direct_failure_rolls_back_assets_and_removed_instances(self):
        self.db.execute('INSERT INTO equip(uid,id,prefab_id,exp,now_break_level,hero_id,is_lock) VALUES(?,102,510001,?,0,0,0)',(UID,self.need10))
        ctx=SimpleNamespace(db=self.db,touched_items=set(),removed_equips=[],pending_frames=[])
        before=self.state()
        real=self.db.execute
        def fail(sql,*args,**kwargs):
            if sql.startswith('UPDATE equip SET exp='):
                raise RuntimeError('injected target write failure')
            return real(sql,*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.svc.one_click_enhance(ctx,UID,101,target_level=10,break_times=1,equip_list=[102])
        self.assertEqual(self.state(),before)
        self.assertEqual(ctx.touched_items,set())
        self.assertEqual(ctx.removed_equips,[])
        result=self.svc.one_click_enhance(ctx,UID,101,target_level=10,break_times=1,equip_list=[102])
        self.assertEqual(result['break_level'],1)
        self.assertEqual(self.db.query('SELECT id FROM equip WHERE uid=? AND id=102',(UID,)),[])
        self.assertEqual([r['equip_id'] for r in ctx.removed_equips],[102])

    def test_zero_break_cap_is_not_defaulted_to_four(self):
        c=self.cfg2
        self.db.execute('INSERT INTO equip_cfg(prefab_id,starlevel,exp_variant,max_level,break_cost,break_times_max) VALUES(210001,?,?,?,?,?)',
                        (c['starlevel'],json.dumps(c['exp']),json.dumps(c['max_level']),json.dumps(c['break_cost']),c['break_times_max']))
        self.db.execute('UPDATE equip SET prefab_id=210001,exp=? WHERE uid=? AND id=101',(self.need10,UID))
        self.reject()
        self.reject(quick=True)

    def test_invalid_experience_items_and_protected_food_reject_before_write(self):
        self.reject(quick=True,mats=[dict(id=40501,num=100)])
        self.reject(quick=True,food=[101])
        self.db.execute('INSERT INTO equip(uid,id,prefab_id,exp,now_break_level,hero_id,is_lock) VALUES(?,102,510001,?,0,0,1)',(UID,self.need10))
        self.reject(quick=True,food=[102])


if __name__=='__main__':
    unittest.main()
