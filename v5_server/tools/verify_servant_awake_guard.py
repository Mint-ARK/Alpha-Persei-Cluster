"""Memory-only awakening guards, instance lifetime, and native packet projection."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import codec
import servant_service as service
from export_servant_awake_cfg import build_config


class AwakeTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.db.set_currency(UID, 2, 1000)
        self.add_servant(101, 2510000, 4)
        self.add_servant(102, 2510000, 2, 1)
        self.add_servant(103, 2520000, 3)
        self.add_servant(104, 2310001, 1)
        self.db.execute('INSERT INTO hero(uid,id,unlock,weapon_servant_uid) VALUES(?,1084,1,101)', (UID,))
        self.db.execute('INSERT INTO hero(uid,id,unlock,weapon_servant_uid) VALUES(?,1039,1,102)', (UID,))
        self.db.execute('INSERT INTO users(uid,nick) VALUES(43,?)', ('other',))
        self.db.execute('INSERT INTO servant(uid,id,prefab_id,stage,is_locked) VALUES(43,999,2510000,1,0)')

    def add_servant(self, suid, prefab, stage, locked=0):
        self.db.execute('INSERT INTO servant(uid,id,prefab_id,stage,is_locked,owned) VALUES(?,?,?,?,?,1)',
                        (UID, suid, prefab, stage, locked))

    def state(self):
        return {table: self.db.query('SELECT * FROM ' + table + ' ORDER BY rowid') for table in
                ('servant', 'servant_history', 'hero', 'currency', 'illustrated')}

    def merge(self, target=2510001, costs=None):
        return self.call(46030, dict(servant_id=target, cost_uid_list=[101] if costs is None else costs), 46031)

    def reject(self, target=2510001, costs=None):
        before = self.state()
        packet, response = self.merge(target, costs)
        self.assertNotEqual(packet['result'], 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(response.scs, [46031])

    def test_invalid_targets_cannot_convert(self):
        for target in (0, 999999999, 2310001, 2510000):
            with self.subTest(target=target):
                self.reject(target)

    def test_exact_material_recipe_and_owned_uid(self):
        for costs in ([], [999], [103], [104], [0]):
            with self.subTest(costs=costs):
                self.reject(costs=costs)

    def test_extra_duplicate_locked_or_equipped_instances_stay_intact(self):
        for costs in ([101, 102], [101, 103], [101, 999], [101, 101]):
            with self.subTest(costs=costs):
                self.reject(costs=costs)

    def test_locked_primary_and_insufficient_gold(self):
        self.reject(costs=[102])
        self.db.set_currency(UID, 2, 99)
        self.reject()

    def test_preserves_uid_refinement_equipped_reference_and_other_instances(self):
        before = self.state()
        packet, response = self.merge()
        self.assertEqual(packet, dict(result=0, servant_uid=101))
        self.assertEqual(self.balance(2), 900)
        row = self.db.get('servant', UID, 'AND id=101')
        self.assertEqual((row['prefab_id'], row['stage'], row['is_locked']), (2510001, 4, 0))
        self.assertEqual(self.db.get('hero', UID, 'AND id=1084')['weapon_servant_uid'], 101)
        self.assertEqual([r for r in self.state()['servant'] if r['id'] != 101],
                         [r for r in before['servant'] if r['id'] != 101])
        self.assertEqual(response.scs, [17023, 46011, 46031])
        diff = codec.decode(response.frames[0]['payload'], 'sc_17023')
        self.assertEqual(diff['normal_items'], [dict(id=2, num=900)])
        self.assertEqual(diff['weapon_list'], [dict(uid=101, item=dict(id=2510000, num=0))])
        instance = codec.decode(response.frames[1]['payload'], 'sc_46011')['servant_list']
        self.assertEqual(instance, [dict(uid=101, id=2510001, stage=4, is_locked=0)])
        # Limited projection of Lua RemoveServant, InitServant/AddServant and ACK.
        instances = {r['id']: dict(id=r['prefab_id'], stage=r['stage'], locked=r['is_locked'])
                     for r in before['servant'] if r['uid'] == UID}
        index = {}
        for suid, data in instances.items():
            index.setdefault(data['id'], set()).add(suid)
        for item in diff['weapon_list']:
            index[item['item']['id']].remove(item['uid'])
            del instances[item['uid']]
        for item in instance:
            instances[item['uid']] = dict(id=item['id'], stage=item['stage'], locked=item['is_locked'])
            index.setdefault(item['id'], set()).add(item['uid'])
        instances[packet['servant_uid']]['locked'] = 0
        self.assertNotIn(101, index[2510000])
        self.assertEqual(index[2510001], {101})
        self.assertEqual(instances[101], dict(id=2510001, stage=4, locked=0))
        self.assertEqual(instances[102]['stage'], 2)

    def test_all_races_use_their_own_recipe(self):
        for offset, (race, recipe) in enumerate(service.SERVANT_AWAKE_CFG['recipes'].items()):
            target = next(int(sid) for sid, cfg in service.SERVANT_AWAKE_CFG['targets'].items()
                          if cfg['race'] == int(race) and cfg['display_type'] == 0)
            suid = 200 + offset
            self.add_servant(suid, recipe['base_id'], 5)
            self.assertEqual(self.merge(target, [suid])[0]['result'], 0)
            self.assertEqual(self.db.get('servant', UID, 'AND id=?', (suid,))['stage'], 5)
        self.assertEqual(self.balance(2), 400)

    def test_replay_is_rejected_without_spending_again(self):
        self.assertEqual(self.merge()[0]['result'], 0)
        self.reject()
        self.assertEqual(self.balance(2), 900)

    def test_packet_failure_rolls_back_instance_currency_notifications_then_retry(self):
        for failed in ('sc_46011', 'sc_46031'):
            original = service.encode
            with patch.object(service, 'encode', side_effect=lambda name, data: None if name == failed else original(name, data)):
                self.reject()
        import generator
        with patch.object(generator, 'gen_payload', return_value=None):
            self.reject()
        self.assertEqual(self.merge()[0]['result'], 0)
        self.assertEqual(self.balance(2), 900)

    def test_write_and_response_preparation_failure_allow_clean_retry(self):
        execute = self.db.execute
        def fail_update(sql, *args, **kwargs):
            if sql.lstrip().startswith('UPDATE servant'):
                raise RuntimeError('isolated instance update failure')
            return execute(sql, *args, **kwargs)
        with patch.object(self.db, 'execute', side_effect=fail_update):
            self.reject()
        import operations
        op = operations.OPERATIONS[46030] if hasattr(operations, 'OPERATIONS') else None
        if op is None:
            import core
            op = core.OPERATIONS[46030]
        with patch.object(op, 'respond', side_effect=RuntimeError('isolated response failure')):
            self.reject()
        self.assertEqual(self.merge()[0]['result'], 0)

    def test_hidden_target_requires_existing_illustration(self):
        # Current summonable targets all have display_type=0; exercise the
        # documented hidden-until-obtained branch with an explicit config fixture.
        cfg = dict(service.SERVANT_AWAKE_CFG,
                   targets=dict(service.SERVANT_AWAKE_CFG['targets']))
        cfg['targets']['2510001'] = dict(cfg['targets']['2510001'], display_type=1)
        with patch.object(service, 'SERVANT_AWAKE_CFG', cfg):
            self.reject()
            self.db.execute("INSERT INTO illustrated(uid,kind,item_id) VALUES(?,'servant',2510001)", (UID,))
            self.assertEqual(self.merge()[0]['result'], 0)

    def test_export_and_release_contract_match_current_lua(self):
        self.assertEqual(build_config(), service.SERVANT_AWAKE_CFG)
        root = Path(service.__file__).parent
        contract = json.loads((root / 'tools/release_contract.json').read_text(encoding='utf-8'))
        self.assertIn('servant_awake_cfg.json', contract['required_files'])
        promote = service.SERVANT_PROMOTE_CFG
        for sid, cfg in service.SERVANT_AWAKE_CFG['targets'].items():
            self.assertEqual(cfg['spec_hero'], promote[sid]['spec_hero'])
            self.assertTrue(promote[sid]['is_exclusive'])


if __name__ == '__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        before = Path(__file__).resolve().parents[2] / 'archive/servant_awake_guard_20261103/before/servant_service.py'
        tree = ast.parse(before.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ServantService')
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'awake_servant')
        ns = dict(vars(service))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(before), 'exec'), ns)
        service.ServantService.awake_servant = ns['awake_servant']
    unittest.main(verbosity=2)
