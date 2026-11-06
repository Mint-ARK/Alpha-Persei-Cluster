"""A55: ordinary optional boxes choose one index; quantity is item_info.num."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_item_use_atomic_random import UseTests, entry
from verify_report_small_fixes import UID
import codec
import inventory_service as inv
from inventory_service import InventoryService as Inventory


ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_TYPES = {504, 508, 514, 515, 516, 517}


class ChoiceTests(UseTests):
    def test_duplicate_or_distinct_multi_selection_rejected(self):
        for choices in ([1, 1], [1, 2], [1, 1, 2]):
            with self.subTest(choices=choices):
                self.reject([entry(30009, choices=choices)], 2)

    def test_all_configured_optional_boxes_have_single_choice_contract(self):
        from core import OperationError
        rows = [row for row in inv._get_item_usable_cfg().values() if row['sub_type'] in OPTIONAL_TYPES]
        self.assertEqual({row['sub_type'] for row in rows}, OPTIONAL_TYPES)
        self.assertEqual(len(rows), 67)
        empty = []
        for row in rows:
            iid = row['id']
            with self.subTest(iid=iid):
                if row['param']:
                    plan = Inventory._plan_item_use(self.db, iid, [1], inv._get_item_usable_cfg(), inv._get_drop_cfg(), set())
                    self.assertEqual(plan['mode'], 'choice')
                else:
                    empty.append(iid)
                    with self.assertRaises(OperationError):
                        Inventory._plan_item_use(self.db, iid, [1], inv._get_item_usable_cfg(), inv._get_drop_cfg(), set())
                for choices in ([1, 1], [1, 2]):
                    with self.assertRaises(OperationError) as caught:
                        Inventory._plan_item_use(self.db, iid, choices, inv._get_item_usable_cfg(), inv._get_drop_cfg(), set())
                    self.assertEqual(caught.exception.code, 2)
        self.assertEqual(empty, [30206])  # Native placeholder has no selectable rewards.

    def test_batch_count_scales_one_choice_and_delta_is_absolute(self):
        packet, response = self.use([entry(30009, count=3, choices=[2])])
        self.assertEqual(packet['result'], 0)
        self.assertEqual([(row['id'], row['num']) for row in packet['drop_list']], [(11076, 3)])
        self.assertEqual(self.balance(30009), 0)
        self.assertEqual(self.balance(11076), 3)
        delta = next(codec.decode(frame['payload'], 'sc_17023') for frame in response.frames if frame['sc'] == 17023)
        balances = {row['id']: row['num'] for row in delta['normal_items']}
        self.assertEqual((balances[30009], balances[11076]), (0, 3))
        self.assertLess(response.scs.index(17023), response.scs.index(17013))

    def test_invalid_later_selection_has_no_prior_debit_or_reward(self):
        with patch.object(Inventory, 'cost_item', wraps=Inventory.cost_item) as cost:
            with patch.object(Inventory, 'grant_items', wraps=Inventory.grant_items) as grant:
                self.reject([entry(20001), entry(30009, choices=[1, 2])], 2)
        self.assertEqual(cost.call_count, 0)
        self.assertEqual(grant.call_count, 0)

    def test_direct_api_rejects_multi_selection_and_preserves_notifications(self):
        ctx = self.conn.to_handler_ctx()
        ctx.touched_items = {77}
        before = self.state()
        result = Inventory.use_items(ctx, UID, [entry(30009, choices=[1, 1])])
        self.assertEqual(result['result'], 2)
        self.assertEqual(result['drop_list'], [])
        self.assertEqual(self.state(), before)
        self.assertEqual(ctx.touched_items, {77})

    def test_empty_selection_keeps_legacy_default(self):
        packet, _ = self.use([entry(30009)])
        self.assertEqual(packet['result'], 0)
        self.assertEqual([(r['id'], r['num']) for r in packet['drop_list']], [(11075, 1)])
        self.assertEqual(self.balance(30009), 2)

if __name__ == '__main__':
    before = '--before' in sys.argv
    if before:
        path = ROOT / 'archive/item_choice_count_20261103/before/v5_server/inventory_service.py'
        cls = next(n for n in ast.parse(path.read_text('utf-8-sig')).body if isinstance(n, ast.ClassDef) and n.name == 'InventoryService')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_plan_item_use')
        method.decorator_list = []
        namespace = dict(vars(inv))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
        Inventory._plan_item_use = classmethod(namespace['_plan_item_use'])
    # Before comparison runs new assertions only; final run includes inherited
    # atomic/RNG checks and the eight prior plan checks, without duplicating them.
    suite = unittest.TestSuite()
    names = [name for name in ChoiceTests.__dict__ if name.startswith('test_')]
    if not before:
        names = unittest.defaultTestLoader.getTestCaseNames(ChoiceTests)
    suite.addTests(ChoiceTests(name) for name in names)
    if not before:
        from verify_item_use_plan_guard import PlanTests
        suite.addTests(PlanTests(name) for name in PlanTests.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
