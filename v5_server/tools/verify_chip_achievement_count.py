"""A71: real Core/Inventory/codec and current config, memory SQL only."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from verify_report_small_fixes import MemoryTests, UID
import achievement_service as ach
import chip_service as chip
import codec
from event_bus import bus, Events
from extract_autochess_cfg import parse_lua_file

ROOT = Path(__file__).resolve().parents[2]
RAW = parse_lua_file(str(ROOT/'decompiled_v2/x64/game/config/chipcfg.lua'))
CFG = {e['id']: e for e in RAW.values() if isinstance(e, dict) and 'id' in e}
IDS = (12601, 12602, 12701, 12702, 12703, 12704, 12705)


class ChipAchievementTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc = ach.AchievementService(self.db)
        self.svc.init_user_achievements(UID)
        for hid in (1011, 1094):
            self.db.execute("INSERT INTO hero(uid,id,unlock,level,chip_state) VALUES(?,?,1,70,'{}')", (UID,hid))
        # Reviser acquisition now enforces its native rank/affection gates.
        from archive_service import ArchiveService
        self.db.execute('UPDATE hero SET star=600 WHERE uid=? AND id=1094',(UID,))
        self.db.execute('INSERT INTO hero_archive(uid,archive_id,exp) VALUES(?,?,1000)',
                        (UID,ArchiveService.get_instance().get_archive_id(1094)))
        for cid, unlocked in ((1,1),(2,0),(101,1),(102,0),(101101,0),(109451,0)):
            self.add_chip(cid, unlocked)

    def add_chip(self, cid, unlocked=1):
        e = CFG[cid]
        category = {1:'kernel',2:'secondary',3:'hero',4:'reviser'}[e['type_id']]
        self.db.execute('INSERT OR REPLACE INTO chip(uid,id,cat,type_id,spec_char,role_type_id,unlocked,cost_condition,remark) '
                        'VALUES(?,?,?,?,?,?,?,\'[]\',\'[]\')',
                        (UID,cid,category,e['type_id'],e['spec_char'],e['role_type_id'],unlocked))

    def rows(self):
        return {r['achievement_id']:r for r in self.db.query('SELECT * FROM achievement WHERE uid=? AND achievement_id IN ('+
                ','.join('?' for _ in IDS)+') ORDER BY achievement_id', (UID,*IDS))}

    def counts(self, kernels, chips):
        for aid, row in self.rows().items():
            if row['complete_flag'] == 0:
                self.assertEqual(row['progress'], kernels if aid < 12700 else chips, aid)

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('chip','hero','achievement','currency','material','users')}

    def unlock(self, cid):
        return self.call(50002,dict(id=cid),50003)

    def claim(self, ids):
        return self.call(53004,dict(id=ids),53005)

    def test_real_unlock_categories_and_first_event_only(self):
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            for cid, expected in ((101101,(1,2)),(2,(2,2)),(102,(2,3)),(109451,(2,4))):
                packet,response=self.unlock(cid)
                self.assertEqual(packet['result'],0)
                self.counts(*expected)
                self.assertIn(53003,response.scs)
                self.assertLess(response.scs.index(53003),response.scs.index(50003))
                first_calls=len(events.call_args_list)
                packet,response=self.unlock(cid)
                self.assertEqual(packet['result'],0)
                self.assertEqual(response.scs,[50003])
                self.assertEqual(len(events.call_args_list),first_calls)
        names=[call.args[0] for call in events.call_args_list]
        self.assertEqual(names.count(Events.CHIP_UNLOCK),3)
        self.assertEqual(names.count(Events.CHAR_CHIP_ACTIVATE),1)

    def test_repeated_unlock_and_bogus_event_delta_do_not_inflate(self):
        self.assertEqual(self.unlock(101101)[0]['result'],0)
        for _ in range(12):
            self.assertEqual(self.unlock(101101)[0]['result'],0)
            bus.emit(Events.CHIP_UNLOCK,self.conn.to_handler_ctx(),UID,chip_id=101101,cat='hero')
        self.svc.trigger_condition(UID,261,delta=999,param=[0],ctx=self.conn.to_handler_ctx())
        self.counts(1,2)
        self.assertFalse(self.conn.to_handler_ctx().pending_frames)

    def test_equip_unload_changes_no_owned_count_or_achievement(self):
        self.add_chip(101101)
        self.svc.reconcile_achievements(UID)
        before=self.rows()
        for cid in (101101,0,101101,0):
            packet,response=self.call(50018,dict(hero_id=1011,slot_id=1,secondary=cid),50019)
            self.assertEqual(packet['result'],0)
            self.assertEqual(response.scs,[50019])
            self.assertEqual(self.rows(),before)
        for op in (1,2):
            packet,response=self.call(50006,dict(kernel_id=1,secondary_id=101,oper=op),50007)
            self.assertEqual(packet['result'],0)
            self.assertEqual(response.scs,[50007])
            self.assertEqual(self.rows(),before)

    def test_unclaimed_inflation_corrected_claimed_ledger_preserved_and_login_correct(self):
        self.db.execute('UPDATE achievement SET progress=999,achieve_time=123 WHERE uid=? AND achievement_id IN ('+
                        ','.join('?' for _ in IDS)+')',(UID,*IDS))
        self.db.execute('UPDATE achievement SET complete_flag=1 WHERE uid=? AND achievement_id=12601',(UID,))
        claimed=self.rows()[12601]
        self.svc.reconcile_achievements(UID)
        self.counts(1,1)
        self.assertEqual(self.rows()[12601],claimed)
        self.assertEqual(self.rows()[12702]['achieve_time'],0)
        payload=codec.decode(self.svc.get_achievement_login_payload(UID),'sc_53001')
        self.assertEqual(next(r for r in payload['achievement_list'] if r['id']==12705)['progress'],1)
        other=next(c for c in ach._CFG.values() if c['condition'] not in (260,261) and c.get('parameter') in ([],[0]))
        self.db.execute('UPDATE achievement SET progress=77 WHERE uid=? AND achievement_id=?',(UID,other['id']))
        self.svc.trigger_condition(UID,other['condition'],delta=5,set_value=True)
        self.assertEqual(self.db.query('SELECT progress FROM achievement WHERE uid=? AND achievement_id=?',(UID,other['id']))[0]['progress'],77)

    def test_claim_rechecks_assets_even_when_saved_progress_is_inflated(self):
        self.db.execute('UPDATE achievement SET progress=999 WHERE uid=? AND achievement_id IN (12602,12702)',(UID,))
        before=self.state()
        for ids in ([12602],[12702],[12701,12702]):
            packet,response=self.claim(ids)
            self.assertEqual(packet['result'],2005)
            self.assertEqual(response.scs,[53005])
            self.assertEqual(self.state(),before)

    def test_real_threshold_reward_once_with_deltas_before_ack(self):
        heroes=[e['id'] for e in CFG.values() if e['type_id']==3][:9]
        for cid in heroes:self.add_chip(cid)
        self.db.execute('UPDATE achievement SET progress=0 WHERE uid=? AND achievement_id=12702',(UID,))
        before={iid:self.balance(iid) for iid,_ in ach._CFG[12702]['reward']}
        packet,response=self.claim([12702])
        self.assertEqual(packet['result'],0)
        self.assertLess(response.scs.index(17023),response.scs.index(53005))
        self.assertLess(response.scs.index(53003),response.scs.index(53005))
        for frame in response.frames:
            if frame['sc']==53003:
                diff=codec.decode(frame['payload'],'sc_53003')['achievement_progress']
                for row in diff:
                    if row['id'] in IDS:
                        self.assertEqual(row['progress'],1 if row['id']<12700 else 10)
        self.assertEqual(self.rows()[12702]['complete_flag'],1)
        for iid,num in ach._CFG[12702]['reward']:
            self.assertEqual(self.balance(iid)-before[iid],num)
        after=self.state()
        self.assertEqual(self.claim([12702])[0]['result'],2013)
        self.assertEqual(self.state(),after)

    def test_only_explicitly_owned_known_categories_are_counted(self):
        for cid,cat,kind,unlocked in ((900001,'hero',0,1),(900002,'unknown',3,1),
                                     (900003,'secondary',2,None),(900004,'kernel',3,1)):
            self.db.execute('INSERT INTO chip(uid,id,cat,type_id,unlocked) VALUES(?,?,?,?,?)',(UID,cid,cat,kind,unlocked))
        self.svc.reconcile_achievements(UID)
        self.counts(1,1)

    def test_native_owned_chip_with_missing_legacy_metadata_counts_and_equips(self):
        self.db.execute('UPDATE chip SET unlocked=1,type_id=0,spec_char=0,role_type_id=0 WHERE uid=? AND id=101101',(UID,))
        self.svc.reconcile_achievements(UID)
        self.counts(1,2)
        packet,_=self.call(50018,dict(hero_id=1011,slot_id=1,secondary=101101),50019)
        self.assertEqual(packet['result'],0)

    def test_reconcile_encoding_failure_and_unlock_ack_failure_roll_back(self):
        self.svc.reconcile_achievements(UID)
        self.add_chip(101101)
        ctx=self.conn.to_handler_ctx()
        before=self.rows()
        original=ach.encode
        with patch.object(ach,'encode',side_effect=lambda n,d: None if n=='sc_53003' else original(n,d)):
            with self.assertRaises(ValueError):
                self.svc.trigger_condition(UID,261,ctx=ctx)
        self.assertEqual(self.rows(),before)
        self.assertFalse(ctx.pending_frames)
        self.svc.trigger_condition(UID,261,ctx=ctx)
        self.counts(1,2)
        ctx.pending_frames.clear()
        before=self.state()
        encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d: None if n=='sc_50003' else encode(n,d)):
            packet,response=self.unlock(102)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[50003])
        self.assertEqual(self.state(),before)
        self.assertEqual(self.unlock(102)[0]['result'],0)
        self.counts(1,3)

    def test_claim_ack_failure_restores_reward_flag_assets_and_notifications(self):
        self.svc.reconcile_achievements(UID)
        before=self.state()
        ctx=self.conn.to_handler_ctx()
        encode=ctx.codec_encode
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d: None if n=='sc_53005' else encode(n,d)):
            packet,response=self.claim([12701])
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[53005])
        self.assertEqual(self.state(),before)
        self.assertEqual(self.claim([12701])[0]['result'],0)


if __name__=='__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        # The extracted class shares the actual loaded catalog, not the initial
        # empty module placeholders that _ensure_cfg later rebinds.
        ach._ensure_cfg()
        before=ROOT/'archive/chip_achievement_count_20261103/before'
        tree=ast.parse((before/'achievement_service.py').read_text(encoding='utf-8'))
        ns=dict(vars(ach))
        selected=[n for n in tree.body if (isinstance(n,ast.ClassDef) and n.name=='AchievementService') or
                  (isinstance(n,ast.FunctionDef) and n.name in ('_on_chip_unlock','_on_chip_equip'))]
        for n in selected:
            if isinstance(n,ast.FunctionDef):n.decorator_list=[]
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(before/'achievement_service.py'),'exec'),ns)
        ach.AchievementService=ns['AchievementService']
        for event in (Events.CHIP_UNLOCK,Events.CHIP_EQUIP,Events.CHAR_CHIP_ACTIVATE):
            bus._handlers[event]=[h for h in bus._handlers.get(event,[]) if h.__module__!='achievement_service']
        bus._handlers[Events.CHIP_UNLOCK].append(ns['_on_chip_unlock'])
        bus._handlers[Events.CHIP_EQUIP].append(ns['_on_chip_equip'])
        cls=next(n for n in ast.parse((before/'chip_service.py').read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='ChipService')
        for name in ('unlock_hero_chip','activate_reviser_chip','unlock_any_chip'):
            fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            local=dict(vars(chip))
            exec(compile(ast.Module(body=[fn],type_ignores=[]),str(before/'chip_service.py'),'exec'),local)
            setattr(chip.ChipService,name,local[name])
    unittest.main(verbosity=2)
