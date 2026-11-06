"""A261 existing rights contract and A262 required delivery-state transaction."""
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID, NOW
import codec
import generator
import paid_entitlement_service as paid


class MonthDeliveryTests(MemoryTests):
    def setUp(self):
        super().setUp(); self.now=NOW
        for guard in (patch('time.time',side_effect=lambda:self.now),patch.object(paid,'settle')):
            guard.start(); self.addCleanup(guard.stop)
        self.db.execute('INSERT INTO game_user(uid) VALUES(?)',(UID,))
        for iid in (1,2,5,14,30,31,32,38):
            self.db.execute('INSERT OR REPLACE INTO item_catalog(id,type) VALUES(?,1)',(iid,))

    def purchase(self,num=1):
        return self.call(34010,dict(id=101,number=num),34011)

    def cold(self):
        return codec.decode(generator.gen_payload(34023,uid=UID,db=self.db),'sc_34023')

    def state(self):
        return {table:self.db.query('SELECT * FROM '+table+' ORDER BY rowid') for table in
                ('users','game_user','currency','material','user_recharge','month_card','ops_common',
                 'newbie_activity','battlepass','mail','sign','task')}

    def monthly(self,response):
        self.assertEqual(response.scs.count(34023),1)
        return next(codec.decode(frame['payload'],'sc_34023') for frame in response.frames if frame['sc']==34023)

    def test_actual_purchase_expiry_is_authoritative_after_delivery_callback(self):
        packet,response=self.purchase(2)
        self.assertEqual(packet['result'],0)
        card=self.monthly(response)
        self.assertEqual(card['monthly_card_timestamp'],self.now+60*86400)
        self.assertEqual(card['monthly_card_num'],1); self.assertEqual(card['is_sign'],0)
        self.assertLess(response.scs.index(34009),response.scs.index(34023))
        # Native34009 provisionally SignToday();34023 restores the actual unsigned day.
        local=dict(is_sign=1,monthly_card_timestamp=0); local.update(card)
        self.assertEqual(local,self.cold())
        self.assertEqual(self.db.get_user_recharge(UID)['total_recharge_num'],60)
        self.assertEqual(self.call(34024,{},34025)[0]['result'],0)
        self.assertEqual(self.cold()['is_sign'],1)

    def test_renewals_preserve_current_day_claim_and_next_day_remains_claimable(self):
        self.purchase(); self.call(34024,{},34025)
        first=self.cold()['monthly_card_timestamp']
        self.assertEqual(self.monthly(self.purchase()[1])['is_sign'],1)
        self.now+=86400
        card=self.monthly(self.purchase()[1])
        self.assertEqual(card['is_sign'],0)
        self.assertEqual(card['monthly_card_timestamp'],first+60*86400)
        self.assertEqual(self.call(34024,{},34025)[0]['result'],0)

    def test_required_order_delivery_or_expiry_encoding_failure_rolls_back_every_write(self):
        for target in ('sc_34011','sc_34009','state'):
            before=self.state()
            if target=='state':
                real=generator.gen_payload
                fault=patch.object(generator,'gen_payload',side_effect=lambda cmd,*a,**kw: None if cmd==34023 else real(cmd,*a,**kw))
            else:
                ctx=self.conn.to_handler_ctx();real=ctx.codec_encode
                fault=patch.object(ctx,'codec_encode',side_effect=lambda schema,*a,**kw: None if schema==target else real(schema,*a,**kw))
            with fault:
                packet,response=self.purchase()
            self.assertNotEqual(packet['result'],0); self.assertEqual(response.scs,[34011])
            self.assertEqual(self.state(),before)
        self.assertEqual(self.purchase()[0]['result'],0)
        self.assertEqual(self.cold()['monthly_card_timestamp'],self.now+30*86400)
        self.assertEqual(self.db.get_user_recharge(UID)['total_recharge_num'],30)

    def test_shared_transaction_preparation_keeps_noncard_order_path(self):
        packet,response=self.call(34010,dict(id=1,number=1),34011)
        self.assertEqual(packet['result'],0)
        delivery=next(codec.decode(frame['payload'],'sc_34009') for frame in response.frames if frame['sc']==34009)
        self.assertEqual(delivery['order']['goods_id'],1)
        self.assertNotIn(34023,response.scs)
        self.assertFalse(self.db.query('SELECT 1 FROM month_card WHERE uid=?',(UID,)))

    def test_missing_card_cold_is_readonly_inactive_and_claim_cannot_create_rights(self):
        before=self.state(); changes=self.db._conn().total_changes
        card=self.cold()
        self.assertEqual((card['monthly_card_num'],card['monthly_card_timestamp']),(0,0))
        self.assertEqual(self.db._conn().total_changes,changes)
        self.assertNotEqual(self.call(34024,{},34025)[0]['result'],0)
        self.assertEqual(self.state(),before)

    def test_expired_card_never_slides_forward_even_forty_days_later(self):
        expiry=self.now-86400
        self.db.upsert('month_card',UID,dict(monthly_card_timestamp=expiry,monthly_card_num=1,is_sign=0,update_ts=self.now-86400))
        for advance in (0,40*86400):
            self.now+=advance;before=self.state()
            card=self.cold()
            self.assertEqual((card['monthly_card_num'],card['monthly_card_timestamp']),(0,expiry))
            self.assertNotEqual(self.call(34024,{},34025)[0]['result'],0)
            self.assertEqual(self.state(),before)

    def test_explicit_long_right_and_paid_expiry_remain_separate_when_switch_disabled(self):
        expiry=self.now+10*86400
        self.db.upsert('month_card',UID,dict(monthly_card_timestamp=expiry,monthly_card_num=1,is_sign=0,update_ts=self.now))
        paid._save(self.db,UID,dict(small_long=True))
        card=self.cold();self.assertEqual(card['monthly_card_num'],1)
        self.assertGreater(card['monthly_card_timestamp'],expiry)
        self.assertEqual(self.db.get('month_card',UID)['monthly_card_timestamp'],expiry)
        paid._save(self.db,UID,dict(small_long=False))
        self.assertEqual(self.cold()['monthly_card_timestamp'],expiry)
        self.assertEqual(self.cold()['monthly_card_num'],1)
        self.now=expiry
        self.assertEqual(self.cold()['monthly_card_num'],0)


if __name__=='__main__':
    unittest.main(verbosity=2)
