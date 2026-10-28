"""Connection version isolation and battlepass migration; fixtures and memory DBs only."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import ast
import datetime
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.dont_write_bytecode = True
os.environ['ACCOUNT_DB_PATH'] = ':memory:'
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import client_version
import res_version_manager as versions
import resource_manifests as manager
import paid_entitlement_service as paid
import generator
import codec
import server_net as net
import core
import login
import account_db
import gm_api
import event_bus
from flask import Flask
from lazy_timer import lazy_timer
from transport import pack_up, unpack_down, FrameParser
from verify_manifest_management import fixture
from verify_paid_entitlements import memory_db


class VersionChecks(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir()
        self.sources = fixture(self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (('ROOT', self.root), ('CONFIG_PATH', self.root/'resource_distribution.json'),
                            ('NATIVE_PATH', self.root/'native_manifest_sources.json'), ('_startup', {}),
                            ('_pc_hint', None), ('_route_rows', {}), ('_route_stamp', None)):
            self.stack.enter_context(patch.object(manager, name, value))
        self.stack.enter_context(patch.object(manager, '_windows_host', return_value=False))
        self.stack.enter_context(patch.object(versions, '_CURRENT_VERSION', '321'))
        token = versions.enter_connection_version()
        self.addCleanup(versions.leave_connection_version, token)
        self.stack.enter_context(patch.object(event_bus.bus, '_handlers', {}))
        self.stack.enter_context(patch.dict(net._online_game_conns, clear=True))
        self.db = memory_db()
        self.addCleanup(self.db.close)
        self.now = int(datetime.datetime.fromisoformat('2026-10-28T16:00:00+08:00').timestamp())

    def native(self, platform, build):
        return next(r for r in self.sources if r['platform'] == platform and r['resource_version'] == build)

    def apply(self, platform='pc', build='229', profile=None):
        return manager.save_platform(dict(platform=platform, selected_id=(profile or self.native(platform, build))['id'],
            fallback_id=self.native(platform, '321')['id']))

    def test_platform_fields_and_sdk_channel_are_distinct(self):
        for enum, channel, platform in ((0,8,'android'), (1,7,'ios'), (4,9,'pc')):
            self.assertEqual(client_version.platform_from_login({'platform_type': enum, 'channel_id':9}), platform)
            self.assertEqual(client_version.platform_from_login({'channel_id': channel}), platform)
        app = Flask(__name__)
        for agent, channel, expected in (('iPhone',9,'ios'), ('Android',9,'android'), ('Windows',7,'pc')):
            with app.test_request_context('/?channelId='+str(channel), headers={'User-Agent':agent}):
                from flask import request
                self.assertEqual(client_version.http_platform(request),expected)

    def test_sdk_login_uses_channel_config_not_latest_other_client(self):
        self.apply('ios','229')
        fake_db = SimpleNamespace(get_system_config=lambda *args:'1')
        app = net.build_https_app(SimpleNamespace(db=fake_db),host_ip='127.0.0.1')
        client = app.test_client()
        for platform, channel in (('pc',9),('ios',7),('android',8),('ios',7)):
            response = client.post('/mix-sdk-api/pass/user/login',json={'platformType':platform,'channelId':channel}).get_json()
            self.assertEqual(response['data']['channelId'],channel)
            self.assertEqual(response['data']['enablePay'],platform=='ios')
        self.assertEqual(versions.get_current_version(),'321')

    def test_channel_selections_do_not_change_global_or_other_channels(self):
        self.apply()
        self.assertEqual(client_version.resolve({'platform_type':4,'resource_vs':'229','client_vs':'308'})['version'],'229')
        self.assertEqual(client_version.resolve({'platform_type':1,'resource_vs':'321'})['version'],'321')
        self.assertEqual(client_version.resolve({'platform_type':0})['version'],'321')
        self.assertEqual(versions.get_default_version(),'321')

    def test_alias_uses_native_base_not_advertised_build(self):
        data = dict(platform='ios', channel_id='7', base_id=self.native('ios','229')['id'],
                    target_app_version='308', target_resource_version='331', label='')
        preview = manager.preview_profile(data)
        row = manager.generate_profile({**data,'preview_token':preview['preview_token']})['profile']
        self.apply('ios',profile=row)
        binding = client_version.resolve({'platform_type':1,'resource_vs':'331','client_vs':'308'})
        self.assertEqual(binding['version'],'229')
        self.assertEqual(manager.get_status()['platforms']['ios']['effective']['base_resource_version'],'229')

    def test_running_client_report_and_connection_pin_survive_selection_changes(self):
        binding = client_version.resolve({'platform_type':4,'resource_vs':'229'})
        self.assertEqual(binding['version'],'229')
        with versions.connection_version(binding['version']):
            self.apply('pc','321')
            self.assertEqual(versions.get_version_config()['battlepass_list_id'],20032)
        self.assertEqual(versions.get_current_version(),'321')

    def test_parallel_connections_and_exception_cleanup(self):
        barrier = threading.Barrier(2)
        def worker(version):
            with versions.connection_version(version):
                barrier.wait(timeout=5)
                return versions.get_version_config()['battlepass_list_id']
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(worker,'229'), pool.submit(worker,'321')
            self.assertEqual((a.result(), b.result()),(20032,20033))
        with self.assertRaises(RuntimeError), versions.connection_version('229'):
            with versions.connection_version('321'):
                self.assertEqual(versions.get_current_version(),'321')
            self.assertEqual(versions.get_current_version(),'229')
            raise RuntimeError('isolated cleanup')
        self.assertEqual(versions.get_current_version(),'321')

    def seed_claimed_pass(self):
        with versions.connection_version('321'):
            paid.ensure_battlepass(self.db,42,self.now)
        self.db.set_currency(42,14,39400)
        self.claims = [{'id':33000+n,'is_pay':1} for n in range(1,15)]
        self.db.save_battlepass_receive_info(42,self.claims)
        state = paid.read_state(self.db,42)
        state['bp_gift_issued'] = True
        paid._save(self.db,42,state)
        self.state = state

    def test_same_week_retarget_preserves_xp_claims_and_gift_ledgers(self):
        self.seed_claimed_pass()
        mails = len(self.db.query('SELECT * FROM mail WHERE uid=42'))
        for version, expected in (('229',20032),('321',20033),('229',20032)):
            with versions.connection_version(version):
                self.assertFalse(paid.ensure_battlepass(self.db,42,self.now))
            bp = self.db.get('battlepass',42)
            self.assertEqual(bp['battlepass_list_id'],expected)
            self.assertEqual(self.db.get_item_num(42,14),39400)
            claims = json.loads(bp['receive_info'])
            offset = 32000 if version=='229' else 33000
            self.assertEqual(claims,[{'id':offset+n,'is_pay':1} for n in range(1,15)])
            state = paid.read_state(self.db,42)
            self.assertTrue(state['bp_gift_issued'])
            self.assertEqual(state['bp_weekly_mail_cycle'],self.state['bp_weekly_mail_cycle'])
            self.assertEqual(len(self.db.query('SELECT * FROM mail WHERE uid=42')),mails)

    def test_actual_week_rollover_still_resets(self):
        self.seed_claimed_pass()
        with versions.connection_version('229'):
            self.assertTrue(paid.ensure_battlepass(self.db,42,self.now+7*paid.DAY))
        self.assertEqual(self.db.get_item_num(42,14),0)
        self.assertEqual(json.loads(self.db.get('battlepass',42)['receive_info']),[])
        self.assertFalse(paid.read_state(self.db,42)['bp_gift_issued'])

    def test_generator_projects_compatible_claims_without_writing(self):
        self.seed_claimed_pass()
        before = self.db._conn().total_changes
        payload = generator.gen_payload(34031,uid=42,db=self.db,res_version='229')
        decoded = codec.decode(payload,'sc_34031')
        self.assertEqual(decoded['battlepass_list_id'],20032)
        self.assertEqual(decoded['receive_info'],[{'id':32000+n,'is_pay':1} for n in range(1,15)])
        self.assertEqual(self.db._conn().total_changes,before)

    def test_reward_request_retargets_before_checking_claims(self):
        self.seed_claimed_pass()
        tree = ast.parse((SERVER/'operations.py').read_text('utf-8-sig'))
        names = {'_get_battlepass_cfg','_get_battlepass_level','PassportClaimBonusOp','PassportOneKeyGetOp'}
        nodes = [node for node in tree.body if getattr(node,'name',None) in names]
        namespace = dict(__file__=str(SERVER/'operations.py'),_BATTLEPASS_CFG=None,os=os,json=json,
            Operation=core.Operation,OperationError=core.OperationError,operation=lambda c:c,
            _item_add=lambda *a: self.fail('Already claimed reward was issued again'))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'operations.py','exec'),namespace)
        self.db.execute('UPDATE battlepass SET pay_level=201 WHERE uid=42')
        context = SimpleNamespace(db=self.db)
        with patch('time.time',return_value=self.now):
            for version, bid in (('229',32001),('321',33001),('229',32001)):
                op = namespace['PassportClaimBonusOp']()
                op.uid=42
                with versions.connection_version(version):
                    result = op.apply({'id':bid,'is_pay':1},context)
                self.assertEqual(result['rewards'],[])
                self.assertEqual(result['receive_info'][0]['id'],bid)
                self.assertEqual(self.db.get_item_num(42,14),39400)

    def test_pushes_use_each_recipient_version(self):
        self.seed_claimed_pass()
        sent = {}
        for version in ('229','321'):
            conn = core.Connection(uid=42,db=self.db,codec_encode=codec.encode)
            conn.resource_version = version
            sent[version] = []
            conn.send_push = lambda cmd,p,v=version: sent[v].append((cmd,p)) or True
            net.register_game_conn(conn,42)
        actual = generator.gen_payload
        with patch.object(generator,'gen_payload',side_effect=lambda cmd,**kw: actual(cmd,**kw) if cmd==34031 else b'\x08\x00'), \
             patch.object(net,'push_mail_notice'):
            self.assertEqual(net.push_paid_entitlements(42,self.db),2)
        for version, expected in (('229',20032),('321',20033)):
            payload = next(p for cmd,p in sent[version] if cmd==34031)
            self.assertEqual(codec.decode(payload,'sc_34031')['battlepass_list_id'],expected)
        self.assertEqual(versions.get_current_version(),'321')

    def test_gm_request_scope_and_cleanup(self):
        conn = core.Connection(uid=42,db=self.db)
        conn.resource_version='229'
        net.register_game_conn(conn,42)
        app = Flask(__name__)
        app.before_request(gm_api._bind_save_request)
        app.teardown_request(gm_api._close_save_request)
        app.add_url_rule('/api/gm/account/probe','account',lambda: versions.get_current_version())
        app.add_url_rule('/api/gm/system/probe','system',lambda: versions.get_current_version())
        with patch.object(account_db,'_db',self.db), app.test_client() as client:
            self.assertEqual(client.get('/api/gm/account/probe').text,'229')
            self.assertEqual(client.get('/api/gm/system/probe').text,'321')
        self.assertEqual(versions.get_current_version(),'321')

    def test_real_handshake_login_flood_and_core_dispatch_share_bound_version(self):
        self.apply()
        self.seed_claimed_pass()
        class Socket:
            def __init__(inner):
                data = dict(user_id=42,server_id=8,gstoken='fixture',platform_type=4,resource_vs='229',client_vs='308')
                inner.chunks = [pack_up(10042,codec.encode('cs_10042',data)),pack_up(10200,b''),pack_up(34030,b''),b'']
                inner.sent = []
            def settimeout(inner,*args): pass
            def recv(inner,*args): return inner.chunks.pop(0)
            def sendall(inner,data): inner.sent.append(data)
        sock = Socket()
        gen = generator
        engine = core.Core(db=self.db,generator=gen,codec_encode=codec.encode,codec_decode=codec.decode,log=lambda *a,**k:None)
        seen=[]
        def dispatch(req,conn):
            seen.append(versions.get_current_version())
            return core.CoreResponse([dict(sc=34031,payload=generator.gen_payload(34031,uid=42,db=self.db),flag=0,srv=None)])
        handler = net.GameHandler.__new__(net.GameHandler)
        handler.request=sock
        handler.client_address=('127.0.0.1',12345)
        handler.server=SimpleNamespace(core=engine,db=self.db,generator=gen,codec_encode=codec.encode,
                                       codec_decode=codec.decode,game_port=8105,frame_delay=0)
        import fatigue_service
        from ai_translator import AITranslator
        with patch.object(engine,'_dispatch',side_effect=dispatch), \
             patch.object(login,'load_login_push',return_value=[(1,34031,generator.build_frame(34031,b'')[:11],b'',1,None)]), \
             patch.object(login,'prepare_skin_draw_login',return_value=([],False)), \
             patch.object(lazy_timer,'pulse'), patch.object(lazy_timer,'pulse_heartbeat'), patch.object(lazy_timer,'on_disconnect'), \
             patch.object(event_bus.bus,'emit'), patch.object(fatigue_service,'register_active_conn'), \
             patch.object(fatigue_service,'unregister_active_conn'), \
             patch.object(AITranslator,'get_instance',return_value=SimpleNamespace(trigger_login_greeting=lambda *a,**k:None)):
            handler.handle()
        parser=FrameParser('down')
        frames=[unpack_down(frame) for data in sock.sent for frame in parser.feed(data)]
        packets=[codec.decode(f['payload'],'sc_34031') for f in frames if f['cmd']==34031]
        self.assertEqual(len(packets),2)
        self.assertTrue(all(p['battlepass_list_id']==20032 for p in packets))
        self.assertEqual(seen,['229'])
        self.assertEqual(versions.get_current_version(),'321')


if __name__ == '__main__':
    ROOT=SERVER.parent/'archive/version229_loading_20261028'/('checks_'+uuid.uuid4().hex)
    ROOT.mkdir(parents=True)
    def audit(event,args):
        if event=='open':
            name,_,flags=args
            if isinstance(name,(str,bytes,os.PathLike)) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
                if not Path(name).resolve().is_relative_to(ROOT): raise PermissionError('Non-fixture write')
        if event in ('os.remove','os.rmdir','socket.connect','socket.bind'): raise PermissionError('No deletion/network')
        if event in ('os.rename','shutil.copyfile') and any(not Path(p).resolve().is_relative_to(ROOT) for p in args[:2]):
            raise PermissionError('Non-fixture mutation')
    sys.addaudithook(audit)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(VersionChecks))
    (ROOT/'result.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors))),encoding='utf-8')
    print('Retained fixtures:',ROOT)
    sys.exit(not result.wasSuccessful())
