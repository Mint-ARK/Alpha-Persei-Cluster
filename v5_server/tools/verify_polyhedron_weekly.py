"""Fixed-clock weekly boundaries, memory SQL and actual packet encoding."""
import os
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from account_db import AccountDB
from polyhedron_service import PolyhedronRunManager as Run
import weekly_challenge_service as weekly
import generator
import codec
import timer_listeners


def stamp(value):
    return int(datetime.datetime.fromisoformat(value+'+08:00').timestamp())


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.db = AccountDB(':memory:')
        self.now = stamp('2026-11-02T05:00:00')
        self.db.set_currency(42, 45, 1800)
        self.db.set_currency(42, 46, 555)
        self.db.execute('INSERT INTO polyhedron_policy_claimed VALUES(42,4407301,1,0)')
        self.db.execute('INSERT INTO polyhedron_terminal VALUES(42,1001,0)')
        self.db.execute("INSERT INTO polyhedron_run VALUES(42,2,1001,'{}',0)")

    def tearDown(self):
        self.db._checked_conn().close()

    def refresh(self, now, activity=4407301):
        with patch('polyhedron_service.time.time', return_value=now):
            return Run.check_and_refresh_policy_cycle(42,self.db,activity_id=activity)

    def test_fixed_zone_boundary(self):
        before = weekly.get_polyhedron_cycle(self.now-1)
        after = weekly.get_polyhedron_cycle(self.now)
        self.assertEqual(before[1],self.now)
        self.assertEqual(after,(self.now,self.now+604800))
        self.assertEqual(Run.get_policy_cycle_id(self.now),after[0])

    def test_initial_preserves_and_next_week_resets_once(self):
        self.assertFalse(self.refresh(self.now))
        self.assertEqual(self.db.get_currency_num(42,45),1800)
        self.assertEqual(len(self.db.query('SELECT * FROM polyhedron_policy_claimed')),1)
        self.assertTrue(self.refresh(self.now+604800))
        self.assertEqual(self.db.get_currency_num(42,45),0)
        self.assertEqual(self.db.query('SELECT * FROM polyhedron_policy_claimed'),[])
        self.assertEqual(self.db.get_currency_num(42,46),555)
        self.assertEqual(len(self.db.query('SELECT * FROM polyhedron_terminal')),1)
        self.assertEqual(self.db.get('polyhedron_run',42)['state'],2)
        self.db.set_currency(42,45,80)
        self.assertFalse(self.refresh(self.now+604801,4307301))
        self.assertEqual(self.db.get_currency_num(42,45),80)
        self.assertFalse(self.refresh(self.now-1))
        self.assertEqual(self.db.get_currency_num(42,45),80)

    def test_reset_transaction_rollback(self):
        self.refresh(self.now)
        original = self.db.upsert
        def fail(table,*args,**kwargs):
            if table=='polyhedron_meta':
                raise RuntimeError('injected write failure')
            return original(table,*args,**kwargs)
        with patch.object(self.db,'upsert',side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.refresh(self.now+604800)
        self.assertEqual(self.db.get_currency_num(42,45),1800)
        self.assertEqual(len(self.db.query('SELECT * FROM polyhedron_policy_claimed')),1)

    def test_activity_list_and_incremental_match_for_all_versions(self):
        for version in ('229','311','321'):
            with patch('weekly_challenge_service.time.time',return_value=self.now):
                activity=weekly.get_polyhedron_activity(version)
                listed=generator.get_activity_list(self.db,42,version)
            matched=[a for a in listed if a['activity_id']==activity['activity_id']]
            self.assertEqual(matched,[activity])
            for cmd,data in ((11001,dict(activity_list=listed)),(11003,dict(activity=activity))):
                decoded=codec.decode(codec.encode('sc_'+str(cmd),data),'sc_'+str(cmd))
                items=decoded['activity_list'] if cmd==11001 else [decoded['activity']]
                row=next(a for a in items if a['activity_id']==activity['activity_id'])
                self.assertEqual(row['stop_time'],self.now+604800)

    def test_online_refresh_packets_and_failure_rollback(self):
        self.refresh(self.now)
        ctx=SimpleNamespace(db=self.db,uid=42,res_version='321',pending_frames=[])
        new=self.now+604800
        with patch('polyhedron_service.time.time',return_value=new):
            timer_listeners.on_polyhedron_weekly_reset(ctx,42,now_ts=new)
        self.assertEqual([f.cmd for f in ctx.pending_frames],[11003,17023,18001])
        data=codec.decode(ctx.pending_frames[0].payload,'sc_11003')
        self.assertEqual(data['activity']['stop_time'],new+604800)
        self.db.set_currency(42,45,80)
        ctx.pending_frames=[]
        real=generator.gen_payload
        def broken(cmd,*args,**kwargs):
            return None if cmd==18001 else real(cmd,*args,**kwargs)
        with patch('polyhedron_service.time.time',return_value=new+604800), patch.object(generator,'gen_payload',side_effect=broken):
            with self.assertRaises(ValueError):
                timer_listeners.on_polyhedron_weekly_reset(ctx,42,now_ts=new+604800)
        self.assertEqual(ctx.pending_frames,[])
        self.assertEqual(self.db.get_currency_num(42,45),80)


if __name__=='__main__':
    unittest.main()
