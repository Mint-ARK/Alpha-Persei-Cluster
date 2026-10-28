"""Actual AccountDB/codec/reward paths; SQLite :memory: only, no servers/APIs."""
import ast
import datetime
import json
import logging
import os
import sqlite3
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import account_db
import codec
import event_bus
import generator
import paid_entitlement_service as paid
from core import Core, Operation, OperationError
from inventory_service import InventoryService
from mail_service import MailService
from middleware import HandlerCtx, CSRequest, DownFrame
from recharge_service import RechargeService


def memory_db():
    db = account_db.AccountDB.__new__(account_db.AccountDB)
    db.path = ':memory:'
    db._local = threading.local()
    db._ai_chat_lock = threading.Lock()
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    db._local.conn = conn
    conn.executescript((ROOT/'db_schema.sql').read_text('utf-8'))
    conn.executescript(account_db.SCHEMA)
    for table, column, ddl in account_db._COLUMN_MIGRATIONS:
        if conn.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone():
            if column not in [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
    conn.commit()
    for iid in (1,2,5,12,14,19,22,24,30,31,32,35,38):
        db.execute('INSERT INTO item_catalog(id,name,type) VALUES(?,?,1)', (iid,str(iid)))
    for iid in (40103,40203,40803,40701,40504,40603):
        db.execute('INSERT INTO item_catalog(id,name,type) VALUES(?,?,4)',(iid,str(iid)))
    db.execute('INSERT INTO users(uid,nick,level) VALUES(42,"权益测试",70)')
    db.execute('INSERT INTO game_user(uid) VALUES(42)')
    return db


def load_ops():
    names = {'_item_add','_item_table','_item_balance','_refresh_frames', '_get_skin_hero_map',
             '_get_skin_map', '_record_user_login','_task_rewards',
             'ActivityPointRewardOp','TaskOneKeySubmitOp','TaskSubmitOp',
             'MonthCardBonusOp','BigMonthCardSignOp','BigMonthCardBuyOp'}
    ns = dict(__file__=str(ROOT/'operations.py'),Operation=Operation, OperationError=OperationError, operation=lambda c:c,
        DownFrame=DownFrame, os=os, json=json, time=__import__('time'),
        event_bus=event_bus, USE_NEW_INVENTORY_SERVICE=True, logger=logging.getLogger('paid_probe'))
    tree=ast.parse((ROOT/'operations.py').read_text('utf-8-sig'))
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/'operations.py'),'exec'),ns)
    return ns


class PaidTests(unittest.TestCase):
    def setUp(self):
        self.db=memory_db()
        self.clock=int(datetime.datetime.fromisoformat('2026-10-26T12:00:00+08:00').timestamp())
        self.time_patch=patch('time.time',side_effect=lambda:self.clock)
        self.time_patch.start()
        self.handlers=patch.object(event_bus.bus,'_handlers',{})
        self.handlers.start()
        self.random_patch=patch.object(paid.random,'choice',side_effect=lambda xs:min(xs))
        self.random_patch.start()
        self.logs=[]
        self.ctx=HandlerCtx(db=self.db,generator=generator,codec_encode=codec.encode,
            codec_decode=codec.decode,log=lambda *a,**k:self.logs.append(a))
        self.ctx.uid=42
        self.core=Core(db=self.db,generator=generator,codec_encode=codec.encode,codec_decode=codec.decode)
        self.ops=load_ops()
        self.lottery=patch('activity_lottery.roll_activity_box',return_value=(None,[],[]))
        self.lottery.start()

    def tearDown(self):
        self.lottery.stop();self.random_patch.stop();self.handlers.stop();self.time_patch.stop();self.db.close()

    def call(self,name,data):
        self.ctx.pending_frames.clear();self.ctx.touched_items.clear()
        op=self.ops[name]()
        req=CSRequest(op.cmd,codec.encode(f'cs_{op.cmd}',data),uid=42)
        result=self.core._run_operation(op,req,self.ctx)
        frames=[(f['sc'],codec.decode(f['payload'],f"sc_{f['sc']}")) for f in result.frames]
        self.assertFalse(any(p.get('result',0) for _,p in frames), (frames,self.logs))
        return frames

    def mails(self,title=None):
        return self.db.query('SELECT * FROM mail WHERE uid=42'+(' AND title=?' if title else ''),
                             (title,) if title else ())

    def mail_amount(self,title,iid):
        return sum(int(a.get('number') or 0) for m in self.mails(title)
                   for a in json.loads(m['attachment_json']) if a['id']==iid)

    def test_status_missing_is_readonly_and_inactive(self):
        before=self.db._conn().total_changes
        s=paid.status(self.db,42)
        self.assertFalse(s['small_month_card']['active']);self.assertFalse(s['big_month_card']['active'])
        self.assertEqual(self.db._conn().total_changes,before)
        p=codec.decode(generator.gen_payload(34023,uid=42,db=self.db),'sc_34023')
        self.assertEqual(p.get('monthly_card_timestamp',0),0)
        self.assertEqual(self.db._conn().total_changes,before)

    def test_highest_once_and_legacy_preserved_then_weekly_reset(self):
        paid.update_rights(self.db,42,{'pay_level':202})
        for tier in (201,0,202,0,202):paid.update_rights(self.db,42,{'pay_level':tier})
        self.assertEqual(len(self.mails('深度合约本期赠礼')),1)
        svc=RechargeService(db=self.db)
        with self.db.transaction():
            svc.handle_pay_order(self.db,42,202,log=lambda *a:None)
            svc.reset_recharge_data(self.db,42,scope='battlepass',log=lambda *a:None)
            svc.handle_pay_order(self.db,42,202,log=lambda *a:None)
        self.assertEqual(len(self.mails('深度合约本期赠礼')),1)
        self.assertEqual(self.db.get_item_num(42,14),0)  # Gift is an unclaimed mail.
        self.db.set_currency(42,14,70000)
        self.db.save_battlepass_receive_info(42,[{'id':33001,'is_pay':1}])
        self.clock+=7*paid.DAY
        paid.settle(self.ctx,42)
        self.assertEqual(self.db.get_item_num(42,14),0)
        self.assertEqual(json.loads(paid._row(self.db,'battlepass',42)['receive_info']),[])
        self.assertEqual(len(self.mails('深度合约本期赠礼')),2)
        self.assertEqual(len(self.mails('每周战令经验补给')),2)
        self.assertEqual(paid.status(self.db,42)['battlepass']['pay_level'],202)
        self.assertTrue(any(f.cmd==17023 for f in self.ctx.pending_frames))

    def test_first_migration_preserves_legacy_exp_claims_and_gift(self):
        self.db.set_battlepass_pay_level(42,202)
        self.db.set_currency(42,14,42000)
        self.db.save_battlepass_receive_info(42,[{'id':33001,'is_pay':1}])
        paid.settle(self.db,42)
        self.assertEqual(self.db.get_item_num(42,14),42000)
        self.assertEqual(len(self.mails('深度合约本期赠礼')),0)
        self.assertTrue(paid.status(self.db,42)['battlepass']['premium_gift_issued'])
        self.assertEqual(len(json.loads(paid._row(self.db,'battlepass',42)['receive_info'])),1)

    def test_gift_failure_rolls_back_tier_and_ledger(self):
        paid.settle(self.db,42)
        with patch.object(MailService,'send_mail',return_value=0):
            with self.assertRaises(ValueError):paid.update_rights(self.db,42,{'pay_level':202})
        self.assertEqual(paid.status(self.db,42)['battlepass']['pay_level'],0)
        self.assertFalse(paid.status(self.db,42)['battlepass']['premium_gift_issued'])
        paid.update_rights(self.db,42,{'pay_level':202})
        self.assertEqual(len(self.mails('深度合约本期赠礼')),1)

    def test_small_toggle_daily_and_offline_catchup(self):
        paid.update_rights(self.db,42,{'small_long_term':True})
        f=self.call('MonthCardBonusOp',{})
        self.assertEqual(self.db.get_item_num(42,1),90)
        self.assertTrue(any(cmd==17023 for cmd,_ in f))
        for on in (False,True,False,True):paid.update_rights(self.db,42,{'small_long_term':on})
        self.call('MonthCardBonusOp',{})
        self.assertEqual(self.db.get_item_num(42,1),90)
        self.clock+=3*paid.DAY
        paid.settle(self.db,42)
        self.assertEqual(self.mail_amount('恒定观测奖励补发',1),180)
        paid.settle(self.db,42)
        self.assertEqual(len(self.mails('恒定观测奖励补发')),1)
        self.call('MonthCardBonusOp',{})
        self.assertEqual(self.db.get_item_num(42,1),180)

    def test_small_no_activation_no_reward(self):
        with self.db.transaction():
            with self.assertRaises(ValueError):paid.claim_small(self.ctx,42)
        self.assertEqual(self.db.get_item_num(42,1),0)

    def test_small_purchase_extends_and_does_not_relabel_yesterday_claim(self):
        svc=RechargeService(db=self.db)
        with self.db.transaction():svc.handle_pay_order(self.db,42,101,log=lambda *a:None)
        first=paid.status(self.db,42)['small_month_card']['paid_until']
        self.call('MonthCardBonusOp',{})
        self.clock+=paid.DAY
        with self.db.transaction():svc.handle_pay_order(self.db,42,101,log=lambda *a:None)
        s=paid.status(self.db,42)
        self.assertEqual(s['small_month_card']['paid_until'],first+30*paid.DAY)
        self.assertFalse(s['small_month_card']['signed_today'])
        self.assertEqual(self.db.get_user_recharge(42)['total_recharge_num'],60)
        paid.update_rights(self.db,42,{'small_long_term':True})
        paid.update_rights(self.db,42,{'small_long_term':False})
        self.assertTrue(paid.status(self.db,42)['small_month_card']['active'])
        self.assertEqual(paid.status(self.db,42)['small_month_card']['paid_until'],first+30*paid.DAY)

    def test_small_5am_boundary(self):
        paid.update_rights(self.db,42,{'small_long_term':True})
        self.call('MonthCardBonusOp',{})
        self.clock=int(datetime.datetime.fromisoformat('2026-10-27T04:59:59+08:00').timestamp())
        self.call('MonthCardBonusOp',{})
        self.assertEqual(self.db.get_item_num(42,1),90)
        self.clock+=1
        self.call('MonthCardBonusOp',{})
        self.assertEqual(self.db.get_item_num(42,1),180)

    def test_big_daily_full_period_and_natural_renewal(self):
        paid.update_rights(self.db,42,{'big_auto_renew':True})
        f=self.call('BigMonthCardSignOp',{})
        self.assertEqual(next(p for c,p in f if c==34111)['total_sign_times'],1)
        self.assertEqual(self.db.get_item_num(42,1),100)
        self.call('BigMonthCardSignOp',{})
        self.assertEqual(self.db.get_item_num(42,1),100)
        initial_end=paid.status(self.db,42)['big_month_card']['cycle_end']
        self.clock=initial_end
        paid.settle(self.db,42)
        self.assertEqual(paid.status(self.db,42)['big_month_card']['sign_days'],0)
        self.assertEqual(self.mail_amount('百宝囊奖励补发',1),29*100+200+300)
        self.assertEqual(self.mail_amount('百宝囊奖励补发',40103),12) # first day deterministic below not assumed
        self.assertEqual(paid.status(self.db,42)['big_month_card']['cycle_end'],initial_end+30*paid.DAY)

    def test_big_missed_year_aggregates_one_mail_and_no_replay(self):
        paid.update_rights(self.db,42,{'big_auto_renew':True})
        self.clock+=360*paid.DAY
        paid.settle(self.db,42)
        self.assertEqual(self.mail_amount('百宝囊奖励补发',1),12*(3000+200+300))
        self.assertEqual(len(self.mails('百宝囊奖励补发')),1)
        self.assertEqual(paid.status(self.db,42)['big_month_card']['sign_days'],0)
        paid.settle(self.db,42)
        self.assertEqual(len(self.mails('百宝囊奖励补发')),1)

    def test_big_disabling_auto_renew_retains_period_then_stops(self):
        paid.update_rights(self.db,42,{'big_auto_renew':True})
        end=paid.status(self.db,42)['big_month_card']['cycle_end']
        paid.update_rights(self.db,42,{'big_auto_renew':False})
        self.assertTrue(paid.status(self.db,42)['big_month_card']['active'])
        self.clock=end+10*paid.DAY
        paid.settle(self.db,42)
        self.assertFalse(paid.status(self.db,42)['big_month_card']['active'])
        self.assertEqual(self.mail_amount('百宝囊奖励补发',1),3500)
        with self.assertRaises(ValueError):paid.claim_big(self.ctx,42)

    def test_box_exp_both_entrypoints_and_no_weekly_limit(self):
        paid.settle(self.db,42)
        self.db.execute('INSERT INTO activity_pt(uid,activity_pt_id,active_point,get_id_list) '
                        'VALUES(42,1,100,"[]"),(42,3,500,"[]")')
        for key,targets in ((1,(20,40,60)),(3,(100,200,300))):
            for target in targets:
                f=self.call('ActivityPointRewardOp',{'activity_pt_id':key,'need_active_point':target})
                p=next(p for c,p in f if c==28017)
                self.assertTrue(any(r['id']==14 for r in p['reward_list']))
                self.assertTrue(any(c==17023 for c,_ in f))
        self.assertEqual(self.db.get_item_num(42,14),53000)
        self.assertEqual(paid._row(self.db,'battlepass',42)['weekly_gain_exp'],0)
        self.call('ActivityPointRewardOp',{'activity_pt_id':1,'need_active_point':20})
        self.assertEqual(self.db.get_item_num(42,14),53000)
        self.db.execute('UPDATE activity_pt SET get_id_list="[]" WHERE uid=42')
        self.call('TaskOneKeySubmitOp',{'type':6})
        self.call('TaskOneKeySubmitOp',{'type':5})
        self.assertEqual(self.db.get_item_num(42,14),106000)

    def test_weekly_mail_claim_over_cap_and_cross_week_retained(self):
        paid.settle(self.db,42)
        mid=self.mails('每周战令经验补给')[0]['mail_id']
        self.clock+=7*paid.DAY
        paid.settle(self.db,42)
        with self.db.transaction():MailService(db=self.db).claim_mail_attachments(42,mid,ctx=self.ctx)
        self.assertEqual(self.db.get_item_num(42,14),25000)
        self.assertEqual(paid._row(self.db,'battlepass',42)['weekly_gain_exp'],0)
        with self.db.transaction():MailService(db=self.db).claim_mail_attachments(42,mid,ctx=self.ctx)
        self.assertEqual(self.db.get_item_num(42,14),25000)

    def test_separate_save_same_uid_isolation(self):
        other=memory_db()
        try:
            paid.update_rights(self.db,42,{'small_long_term':True,'pay_level':202})
            self.assertFalse(paid.status(other,42)['small_month_card']['active'])
            self.assertEqual(other.query('SELECT COUNT(*) n FROM mail')[0]['n'],0)
            self.assertEqual(paid.status(other,42)['battlepass']['pay_level'],0)
        finally:other.close()

    def test_card_asset_frame_failure_rolls_back_claim(self):
        paid.update_rights(self.db,42,{'small_long_term':True})
        original=generator.gen_payload
        def failing(cmd,*a,**kw):
            if cmd==17023:raise ValueError('isolated frame failure')
            return original(cmd,*a,**kw)
        with patch.object(generator,'gen_payload',side_effect=failing):
            op=self.ops['MonthCardBonusOp']()
            req=CSRequest(op.cmd,codec.encode('cs_34024',{}),uid=42)
            result=self.core._run_operation(op,req,self.ctx)
            self.assertTrue(codec.decode(result.frames[-1]['payload'],'sc_34025')['result'])
        self.assertEqual(self.db.get_item_num(42,1),0)
        self.assertFalse(paid.status(self.db,42)['small_month_card']['signed_today'])
        self.call('MonthCardBonusOp',{})
        self.assertEqual(self.db.get_item_num(42,1),90)

    def test_original_task_rewards_and_week_counter_remain_separate(self):
        paid.settle(self.db,42)
        for tid,kind,amount in ((71001,7,200),(81001,8,1000),(91001,9,2500)):
            self.db.execute('INSERT INTO task_cfg(task_id,task_type,need,reward_json) VALUES(?,?,1,?)',
                            (tid,kind,json.dumps([[14,amount]])))
            self.db.execute('INSERT INTO task(uid,task_id,progress) VALUES(42,?,1)',(tid,))
        self.call('TaskSubmitOp',{'id':71001})
        self.call('TaskOneKeySubmitOp',{'id_list':[81001,91001]})
        self.assertEqual(self.db.get_item_num(42,14),3700)
        self.assertEqual(paid._row(self.db,'battlepass',42)['weekly_gain_exp'],1200)
        self.assertEqual(self.db.get_item_num(42,1),0)

    def test_gm_status_readonly_and_switch_route(self):
        from flask import Flask
        import gm_api
        app=Flask('paid_probe')
        app.add_url_rule('/status',view_func=gm_api.gm_recharge_status)
        app.add_url_rule('/rights',view_func=gm_api.gm_recharge_rights,methods=['POST'])
        notices=[]
        transport=types.SimpleNamespace(push_paid_entitlements=lambda uid,expected_db:notices.append((uid,expected_db)))
        with patch.object(gm_api,'get_db',return_value=self.db),patch.dict(sys.modules,server_net=transport):
            self.db._conn().execute('PRAGMA query_only=ON')
            before=self.db._conn().total_changes
            response=app.test_client().get('/status?uid=42').get_json()
            self.assertEqual(response['code'],0)
            self.assertFalse(response['data']['small_month_card']['active'])
            self.assertEqual(self.db._conn().total_changes,before)
            self.db._conn().execute('PRAGMA query_only=OFF')
            response=app.test_client().post('/rights',json={'uid':42,'pay_level':202,'small_long_term':True}).get_json()
            self.assertEqual(response['code'],0)
            self.assertTrue(response['data']['small_month_card']['active'])
            self.assertEqual(notices,[(42,self.db)])
            for body in ({'uid':42,'pay_level':2},{'uid':42,'small_long_term':'true'},['invalid']):
                response=app.test_client().post('/rights',json=body).get_json()
                self.assertNotEqual(response['code'],0)


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
