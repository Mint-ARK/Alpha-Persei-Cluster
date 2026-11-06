"""A235: current-native IDs through actual Core/codec; legacy SQL preserved."""
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests,UID,NOW
import admin_cat_explore_service as cat
import admin_cat_log_service as logs
import codec
import generator


class LogTests(MemoryTests):
    def setUp(self):
        super().setUp()
        clock=patch('time.time',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.svc=cat.AdminCatExploreService.get_instance();self.svc.get_or_create_explore_data(self.db,UID)

    def dispatch(self):return self.call(67004,dict(area_id=10001,mimir_id=10001,hour_time=4),67005)

    def test_export_and_all_native_address_content_domains(self):
        from export_admin_cat_logs import build
        self.assertEqual(logs.catalog(),build()['events'])
        address,content=logs.choices()
        self.assertEqual(address,list(range(101,111)))
        self.assertEqual(content,list(range(201,225))+list(range(301,307)))
        self.assertNotIn(225,content)
        for key,row in logs.catalog().items():
            self.assertEqual(int(key) in address,row['event_type']==1)

    def test_native_core_dispatch_uses_only_present_ids_and_persists_once(self):
        with patch.object(logs.random,'choice',side_effect=lambda options:options[-1]):
            packet,response=self.dispatch()
        self.assertEqual(packet['result'],0)
        payload=next(codec.decode(f['payload'],'sc_67001') for f in response.frames if f['sc']==67001)
        events=payload['explore_queue'][0]['explore_event'];self.assertTrue(events)
        self.assertTrue(all(e['address_id']==110 and e['content_id']==306 for e in events))
        stored=self.db.query('SELECT * FROM admin_cat_explore_queue')
        cold=codec.decode(generator.gen_payload(67001,uid=UID,db=self.db),'sc_67001')
        self.assertEqual(cold['explore_queue'][0]['explore_event'],events)
        self.assertEqual(self.db.query('SELECT * FROM admin_cat_explore_queue'),stored)
        self.assertEqual(self.balance(54),0)  # Log reward policy is A241, not this repair.

    def test_legacy_unknown_ids_are_hidden_on_wire_without_modifying_queue_or_assets(self):
        events=[dict(time=60,address_id=101,content_id=201),dict(time=90,address_id=101,content_id=225),
                dict(time=100,address_id=201,content_id=201),dict(time=120,address_id=101,content_id=101),
                dict(time=14401,address_id=101,content_id=301)]
        self.db.save_admin_cat_queue(UID,10001,10001,NOW,NOW+14400,4,events)
        before=self.db.query('SELECT * FROM admin_cat_explore_queue')
        packet=codec.decode(generator.gen_payload(67001,uid=UID,db=self.db),'sc_67001')
        self.assertEqual(packet['explore_queue'][0]['explore_event'],events[:1])
        self.assertEqual(packet['explore_queue'][0]['stop_time'],NOW+14400)
        self.assertEqual(self.db.query('SELECT * FROM admin_cat_explore_queue'),before)
        self.assertEqual((self.balance(1),self.balance(54)),(0,0))

    def test_missing_catalog_fails_dispatch_and_rolls_back_instead_of_fabricating_ids(self):
        tables=('admin_cat_explore_queue','admin_cat_explore_data','ops_common')
        before={t:self.db.query('SELECT * FROM '+t) for t in tables}
        with patch.object(logs,'catalog',side_effect=RuntimeError('isolated missing log catalog')):
            packet,response=self.dispatch()
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[67005])
        self.assertEqual({t:self.db.query('SELECT * FROM '+t) for t in tables},before)


if __name__=='__main__':unittest.main(verbosity=2)
