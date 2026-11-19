"""A40/A41: real Core, fixed equip positions, memory SQL and response shapes."""
import json
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import battle_payload
from battle_server import parse_pb_top
import codec
from equip_service import INHERIT_COST_DEFAULT
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

    def test_cross_hero_exchange_matches_the_client_callback(self):
        answer, response = self.single()
        self.assertEqual(answer['result'], 0)
        self.assertLess(response.scs.index(13013), response.scs.index(14007))
        self.assertEqual(self.slots(1084),{'1':201})
        self.assertEqual(self.slots(1039),{'1':101})
        self.assertEqual((self.owner(101),self.owner(201)),(1039,1084))

    def test_single_unload_clears_wear_and_preserves_binding(self):
        self.db.execute('UPDATE equip SET race_hero=1084, is_lock=1 WHERE uid=? AND id=101', (UID,))
        answer, response = self.single(hid=1084, eid=0)
        self.assertEqual(answer['result'], 0)
        self.assertLess(response.scs.index(13013), response.scs.index(14007))
        self.assertEqual(self.slots(1084), {})
        equip = self.db.get('equip', UID, 'AND id=101')
        self.assertEqual((equip['hero_id'], equip['race_hero'], equip['is_lock']), (0, 1084, 1))
        self.assertEqual(self.single(hid=1084, eid=0)[0]['result'], 0)

    def test_unload_all_keeps_lock_but_clears_slot(self):
        self.db.execute('UPDATE equip SET is_lock=1 WHERE uid=? AND id=101', (UID,))
        answer, response = self.call(13018, dict(hero_id=1084), 13019)
        self.assertEqual(answer['result'], 0)
        self.assertLess(response.scs.index(13019), response.scs.index(14007))
        self.assertEqual(self.slots(1084), {})
        self.assertEqual(self.owner(101), 0)
        self.assertEqual(self.db.get('equip', UID, 'AND id=101')['is_lock'], 1)

    def test_inherit_reindexes_same_instance_with_its_training(self):
        for prefab_id in (512068, 519051):
            self.db.execute('INSERT INTO equip_cfg(prefab_id,starlevel) VALUES(?,5)', (prefab_id,))
        for item_id, count in INHERIT_COST_DEFAULT:
            item_type = 1 if item_id == 2 else 5
            self.db.execute('INSERT INTO item_catalog(id,type) VALUES(?,?)', (item_id, item_type))
            if item_type == 1:
                self.db.set_currency(UID, item_id, count)
            else:
                self.db.add_item(UID, item_id, count)
        self.db.execute('UPDATE equip SET prefab_id=512068, exp=123, is_lock=1, race_hero=1084 '
                        'WHERE uid=? AND id=101', (UID,))
        answer, response = self.call(13052, dict(new_equip_id=101, inherit_equip_prefab_id=519051), 13053)
        self.assertEqual(answer['result'], 0)
        self.assertEqual(response.scs[-3:], [17023, 13009, 13053])
        removal = codec.decode(response.frames[-3]['payload'], 'sc_17023')['equip_list'][0]
        self.assertEqual((removal['equip_id'], removal['prefab_id'], removal['num']), (101, 512068, 0))
        refreshed = codec.decode(response.frames[-2]['payload'], 'sc_13009')['equip_list'][0]
        self.assertEqual((refreshed['equip_id'], refreshed['prefab_id'], refreshed['exp'],
                          refreshed['hero_id'], refreshed['race'], refreshed['is_lock']),
                         (101, 519051, 123, 1084, 1084, True))
        self.assertEqual(self.single(hid=1084, eid=0)[0]['result'], 0)

    def test_valid_six_slots_quick_batch_has_one_item_per_position(self):
        items=[dict(equip_id=100+p,pos=p) for p in range(1,7)]
        answer,response=self.quick(items)
        self.assertEqual(answer['result'],[dict(equip_id=100+p,result=0) for p in range(1,7)])
        self.assertEqual(self.slots(1039),{str(p):100+p for p in range(1,7)})
        self.assertEqual(self.slots(1084),{'1':201})
        self.assertTrue(all(self.owner(100+p)==1039 for p in range(1,7)))
        self.assertEqual(self.owner(201),1084)
        self.assertLess(response.scs.index(13027), response.scs.index(14007))

    def test_battle_uses_omega_and_only_mutually_worn_equipment(self):
        self.db.execute('UPDATE hero SET star=600 WHERE uid=? AND id=1039', (UID,))
        items = [dict(equip_id=100+p, pos=p) for p in range(1, 7)]
        self.quick(items)
        self.db.execute('UPDATE equip SET race=1, race_hero=1039, exp=123, now_break_level=2 '
                        'WHERE uid=? AND id=101', (UID,))

        def read_start():
            payload = battle_payload.build_sc_54003_dynamic(self.db, UID, [1039])
            player = parse_pb_top(parse_pb_top(payload)[1])
            hero = parse_pb_top(player[2])[4]
            return codec.decode(hero, 'battle_hero_net_rec')

        hero = read_start()
        equips = hero['equip_list']
        self.assertEqual(hero['hero_base_info']['star'], 600)
        self.assertEqual([equip['equip_id'] for equip in equips], list(range(101, 107)))
        self.assertEqual((equips[0]['race'], equips[0]['exp'], equips[0]['now_break_level']),
                         (1039, 123, 2))
        self.assertEqual([slot['id'] for slot in equips[0]['enchant_slot_list']], [1, 2])

        # An old snapshot points to a piece that is already wearing another hero.
        self.db.execute('UPDATE equip SET hero_id=1084 WHERE uid=? AND id=101', (UID,))
        self.assertEqual([equip['equip_id'] for equip in read_start()['equip_list']], list(range(102, 107)))

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
