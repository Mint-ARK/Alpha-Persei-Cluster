"""Native chip unlock gates, prices, sequences and Core/codec; memory only."""
import ast
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from verify_report_small_fixes import MemoryTests, UID
import chip_service as chip
import codec
from archive_service import ArchiveService
from event_bus import bus, Events
from export_chip_unlock_cfg import build_config

ROOT=Path(__file__).resolve().parents[2]


class UnlockTests(MemoryTests):
    def setUp(self):
        super().setUp()
        self.svc=chip.ChipService.get_instance(self.db)
        for hid in (1011,1094):self.hero(hid,100)
        self.db.execute("INSERT INTO chip(uid,id,cat,type_id,spec_char,role_type_id,unlocked) VALUES(? ,1,'kernel',1,0,0,1)",(UID,))
        self.db.set_currency(UID,47,500)

    def hero(self,hid,level,star=600,exp=1000,attrs=75):
        self.db.execute("INSERT OR REPLACE INTO hero(uid,id,unlock,level,star,skill_intensify,chip_state) VALUES(?,?,1,?,?,?,'{}')",
                        (UID,hid,level,star,json.dumps([[i,min(15,max(0,attrs-15*(i-1))) ] for i in range(1,6)])))
        archive=ArchiveService.get_instance().get_archive_id(hid)
        if archive:self.db.execute('INSERT OR REPLACE INTO hero_archive(uid,archive_id,exp) VALUES(?,?,?)',(UID,archive,exp))

    def state(self):
        return {t:self.db.query('SELECT * FROM '+t+' ORDER BY rowid') for t in ('chip','hero','hero_archive','material','currency','achievement')}

    def unlock(self,cid):return self.call(50002,dict(id=cid),50003)

    def reject(self,cid):
        before=self.state()
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            packet,response=self.unlock(cid)
        self.assertNotEqual(packet['result'],0)
        self.assertEqual(response.scs,[50003])
        self.assertEqual(self.state(),before)
        self.assertFalse(events.called)

    def test_unknown_targets_wrong_direct_category_and_missing_config_rejected(self):
        for cid in (0,999999,999999999):self.reject(cid)
        before=self.state()
        with self.assertRaises(Exception):self.svc.unlock_hero_chip(self.conn.to_handler_ctx(),UID,109451)
        self.assertEqual(self.state(),before)
        with patch.object(chip,'CHIP_UNLOCK_CFG',{}):self.reject(101101)

    def test_ai_missing_locked_host_level_and_known_free_target(self):
        self.db.execute('DELETE FROM hero WHERE uid=? AND id=1011',(UID,));self.reject(101101)
        self.hero(1011,69);self.reject(101101)
        self.db.execute('UPDATE hero SET level=70,unlock=0 WHERE uid=? AND id=1011',(UID,));self.reject(101101)
        self.db.execute('UPDATE hero SET unlock=1 WHERE uid=? AND id=1011',(UID,))
        self.assertEqual(self.unlock(101101)[0]['result'],0)
        r=self.db.get('chip',UID,'AND id=101101')
        self.assertEqual((r['type_id'],r['spec_char'],r['role_type_id']),(3,1011,1))

    def test_ai_real_price_insufficiency_repeat_and_atomic_delta(self):
        self.db.execute('UPDATE currency SET num=99 WHERE uid=? AND id=47',(UID,));self.reject(101105)
        self.db.execute('UPDATE currency SET num=500 WHERE uid=? AND id=47',(UID,))
        packet,response=self.unlock(101105)
        self.assertEqual(packet['result'],0)
        self.assertEqual(self.balance(47),400)
        self.assertLess(response.scs.index(17023),response.scs.index(50003))
        diff=next(codec.decode(f['payload'],'sc_17023') for f in response.frames if f['sc']==17023)
        self.assertIn(dict(id=47,num=400),diff['normal_items'])
        before=self.state()
        self.assertEqual(self.unlock(101105)[1].scs,[50003])
        self.assertEqual(self.state(),before)

    def test_base_gate_level_star_and_ontology_affection(self):
        self.hero(1094,69);self.reject(109451)
        self.hero(1094,70,star=399);self.reject(109451)
        self.hero(1094,70,star=400,exp=999);self.reject(109451)
        self.hero(1094,70,star=400,exp=1000)
        self.assertEqual(self.svc.check_char_chip_can_unlock(UID,109451)[0],True)
        self.assertEqual(self.unlock(109451)[0]['result'],0)

    def test_extra_requires_native_predecessors_and_own_conditions(self):
        self.reject(109452);self.reject(109453)
        self.assertEqual(self.unlock(109451)[0]['result'],0)
        self.reject(109453)
        self.hero(1094,79);self.reject(109452)
        self.hero(1094,80,attrs=44);self.reject(109452)
        self.hero(1094,80,attrs=45)
        self.assertEqual(self.unlock(109452)[0]['result'],0)
        self.hero(1094,89);self.reject(109453)
        self.hero(1094,90,star=499);self.reject(109453)
        self.hero(1094,90,star=500)
        self.assertEqual(self.unlock(109453)[0]['result'],0)
        row=self.db.get('chip',UID,'AND id=109453')
        self.assertEqual(row['spec_char'],109451)

    def test_redpoint_uses_same_guards_and_ignores_unknown_legacy_rows(self):
        for cid in (109451,109452,109453,999999):
            self.db.execute("INSERT INTO chip(uid,id,cat,unlocked) VALUES(?,?,'reviser',0)",(UID,cid))
        self.assertEqual(self.svc.get_char_chip_redpoint_states(UID),{109451:True})
        self.assertEqual(self.unlock(109451)[0]['result'],0)
        self.assertEqual(self.svc.get_char_chip_redpoint_states(UID),{109452:True})
        with patch.object(bus,'emit',wraps=bus.emit) as events:
            self.assertEqual(self.unlock(109451)[1].scs,[50003])
        self.assertFalse(events.called)

    def test_write_or_ack_failure_rolls_back_cost_unlock_and_notifications(self):
        execute=self.db.execute
        def fail(sql,*args,**kwargs):
            if sql.lstrip().startswith('INSERT INTO chip'):raise RuntimeError('isolated chip write')
            return execute(sql,*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=fail):self.reject(101105)
        ctx=self.conn.to_handler_ctx();encode=ctx.codec_encode;before=self.state()
        with patch.object(ctx,'codec_encode',side_effect=lambda n,d:None if n=='sc_50003' else encode(n,d)):
            packet,response=self.unlock(101105)
        self.assertNotEqual(packet['result'],0);self.assertEqual(response.scs,[50003]);self.assertEqual(self.state(),before)
        self.assertEqual(self.unlock(101105)[0]['result'],0);self.assertEqual(self.balance(47),400)

    def test_current_export_release_contract_and_all_native_targets(self):
        self.assertEqual(build_config(),chip.CHIP_UNLOCK_CFG)
        contract=json.loads((ROOT/'v5_server/tools/release_contract.json').read_text(encoding='utf-8'))
        self.assertIn('chip_unlock_cfg.json',contract['required_files'])
        for c in chip.CHIP_UNLOCK_CFG['chips'].values():
            if 'host_id' in c:self.hero(c['host_id'],100)
        self.db.execute('UPDATE currency SET num=1000000 WHERE uid=? AND id=47',(UID,))
        for sid,c in chip.CHIP_UNLOCK_CFG['chips'].items():
            if c['type_id']==3:self.assertEqual(self.unlock(int(sid))[0]['result'],0,sid)
        for sequence in chip.CHIP_UNLOCK_CFG['reviser_sequences'].values():
            for sid in sequence:self.assertEqual(self.unlock(sid)[0]['result'],0,sid)
        cold=codec.decode(codec.encode('sc_50001',self.svc.get_sc_50001_dict(UID)),'sc_50001')
        self.assertEqual(len(cold['unlock_hero_chip']),405)
        self.assertEqual(len(cold['unlock_reviser_chip']),45)


if __name__=='__main__':
    if '--before' in sys.argv:
        sys.argv.remove('--before')
        path=ROOT/'archive/chip_unlock_guards_20261103/before/chip_service.py'
        cls=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='ChipService')
        for name in ('unlock_hero_chip','activate_reviser_chip','unlock_any_chip','check_char_chip_can_unlock','get_char_chip_redpoint_states'):
            fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
            ns=dict(vars(chip));exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
            setattr(chip.ChipService,name,ns[name])
    unittest.main(verbosity=2)
