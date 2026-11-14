"""Gateway-only save actions, coalesced packets and deletion failure boundaries."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from uuid import uuid4

import verify_slot_delete_ui as base
from account_db import AccountDB
from server_net import GatewayHandler

slots = base.slots
HOST = base.HOST
codec = base.codec
SERVER = Path(__file__).resolve().parents[1]


class GatewayActions(unittest.TestCase):
    setUp=base.SlotDeleteTests.setUp
    def exchange(self, port, commands=(10038,10042,10038)):
        data = b''.join(base.pack_up(cmd,b'',index=7+i,server_idx=3+i) for i,cmd in enumerate(commands))
        class Socket:
            def __init__(self):
                self.chunks=[data,b''];self.sent=[];self.reads=0
            def recv(self,count):
                self.reads+=1
                return self.chunks.pop(0)
            def sendall(self,packet):self.sent.append(packet)
            def getsockname(self):return (HOST,port)
        sock=Socket()
        handler=GatewayHandler.__new__(GatewayHandler)
        handler.request=sock;handler.client_address=(HOST,12345)
        # No business DB or dispatcher is supplied. Any accidental mount or
        # kernel initialization fails rather than being silently mocked away.
        handler.server=SimpleNamespace(core=SimpleNamespace(log=lambda *a,**k:None),db=None,
            material=None,codec_encode=codec.encode,game_port=8105,gw_port=port,
            base_gw_port=8102,host_ip=HOST)
        with patch.object(slots,'get_slot_manager',return_value=self.manager), \
             patch.object(slots,'switch_active_database',side_effect=AssertionError('business mount forbidden')):
            handler.handle()
        result=[]
        for raw in sock.sent:
            frame=base.unpack_down(raw)
            result.append((frame['cmd'],codec.decode(frame['payload'],'sc_'+str(frame['cmd']))))
        return sock,result

    def test_toggle_reply_closes_before_coalesced_login_or_second_toggle(self):
        sock,frames=self.exchange(8103)
        self.assertEqual([cmd for cmd,_ in frames],[10039])
        self.assertEqual(frames[0][1]['result'],1005)
        self.assertEqual(self.manager.get_session_state(HOST),slots.STATE_DELETE_MODE)
        self.assertEqual(sock.reads,1)

    def test_delete_success_and_failure_never_enter_kernel(self):
        for success in (True,False):
            with self.subTest(success=success):
                self.manager.reset_session(HOST)
                self.manager.toggle_delete_mode(HOST)
                with patch.object(self.manager,'delete_slot_save',return_value=(success,'isolated delete result')) as delete:
                    sock,frames=self.exchange(8106)
                delete.assert_called_once_with(4,HOST)
                self.assertEqual(sock.reads,1)
                self.assertEqual([cmd for cmd,_ in frames],[10039])
                self.assertNotEqual(frames[0][1]['result'],0)
                self.assertFalse(frames[0][1].get('gstoken'))
                self.assertFalse(frames[0][1].get('ip'))

    def test_new_control_also_ends_connection(self):
        sock,frames=self.exchange(8102)
        self.assertEqual(sock.reads,1)
        self.assertEqual([cmd for cmd,_ in frames],[10039])
        self.assertNotEqual(frames[0][1]['result'],0)

    def test_game_login_without_successful_gateway_login_is_rejected(self):
        sock,frames=self.exchange(8103,commands=(10042,10038))
        self.assertEqual(sock.reads,1)
        self.assertEqual([cmd for cmd,_ in frames],[10043])
        self.assertNotEqual(frames[0][1]['result'],0)
        self.assertEqual(self.manager.get_session_state(HOST),slots.STATE_NORMAL)

    def test_normal_gateway_and_legacy_login_remain_available(self):
        mapping=self.manager.get_slot_mapping()
        mapping[3]=replace(mapping[3],db_path=str(SERVER/'account.db'))
        with patch.object(self.manager,'get_active_db',return_value=str(SERVER/'account.db')), \
             patch.object(slots.os.path,'isfile',return_value=True), \
             patch.object(slots.os.path,'exists',return_value=True), \
             patch.object(slots.os.path,'samefile',return_value=True), \
             patch('new_player_service.gateway_new_player',return_value=False):
            sock,frames=self.exchange(8104,commands=(10038,10042))
        self.assertEqual([cmd for cmd,_ in frames],[10039,10043])
        self.assertTrue(all(packet['result']==0 for _,packet in frames))
        self.assertEqual(frames[0][1]['port'],8105)
        self.assertEqual(sock.reads,2)


class DeleteBoundaries(unittest.TestCase):
    def setUp(self):
        base.SlotDeleteTests.setUp(self)
        self.manager.server_dir = SERVER.parent/'archive/panel_save_delete_20261113'/uuid4().hex
        self.manager.trash_dir = self.manager.server_dir/'Trash'
        self.manager.trash_dir.mkdir(parents=True)
        self.manager.registry_file = self.manager.server_dir/'slot_registry.json'
        self.manager.registry_file.write_text('{}', encoding='utf-8')
        self.manager._panel_requests = {}
        self.target = self.manager.server_dir/'待删 存档.db'
        self.target.write_bytes(b'fixture')
        mapping=self.manager.get_slot_mapping()
        mapping[4]=replace(mapping[4],db_path=str(self.target))

    def file_fixture(self, backup_error=None, recycle_error=None, active=False):
        stack=ExitStack();self.addCleanup(stack.close)
        order=[]
        stack.enter_context(patch.object(self.manager,'get_active_db',return_value=str(self.manager.server_dir/'account.db')))
        stack.enter_context(patch.object(slots.os.path,'samefile',return_value=active))
        stack.enter_context(patch.object(slots,'is_valid_save_db',return_value=True))
        stack.enter_context(patch.object(slots,'extract_save_metadata',return_value=(42, '探针', 1)))
        stack.enter_context(patch.object(self.manager,'_load_registry',return_value={3:'account.db',4:self.target.name,5:'other.db'}))
        save=stack.enter_context(patch.object(self.manager,'_save_registry'))
        def backup(path):
            order.append('backup')
            if backup_error:raise backup_error
            return 'isolated-backup'
        def recycle(paths):
            order.append('recycle')
            if recycle_error:raise recycle_error
            paths[0].rename(self.manager.trash_dir/'recycled')
        snap=stack.enter_context(patch.object(AccountDB,'backup_save_file',side_effect=backup))
        recycle_mock=stack.enter_context(patch.object(slots,'_recycle_save_files',side_effect=recycle))
        return order,save,snap,recycle_mock

    def test_mode_is_required_and_expiry_cannot_remove_a_save(self):
        with patch.object(self.manager,'get_slot_mapping',side_effect=AssertionError('must stop before files')):
            self.assertFalse(self.manager.delete_slot_save(4,HOST)[0])
            self.manager.toggle_delete_mode(HOST)
            with patch.object(slots.time,'time',return_value=base.NOW+61):
                self.assertFalse(self.manager.delete_slot_save(4,HOST)[0])

    def test_active_save_is_rejected_before_backup_or_recycle(self):
        self.manager.toggle_delete_mode(HOST)
        order,save,backup,recycle=self.file_fixture(active=True)
        self.assertFalse(self.manager.delete_slot_save(4,HOST)[0])
        self.assertEqual(order,[]);save.assert_not_called()

    def test_backup_then_recycle_then_release_slot(self):
        self.manager.toggle_delete_mode(HOST)
        order,save,backup,recycle=self.file_fixture()
        result,msg=self.manager.delete_slot_save(4,HOST)
        self.assertTrue(result,msg)
        self.assertEqual(order,['backup','recycle'])
        save.assert_called_once_with({3:'account.db',5:'other.db'}, strict=True)
        self.assertEqual(self.manager.get_session_state(HOST),slots.STATE_NORMAL)

    def test_backup_failure_never_recycles_or_releases_slot(self):
        self.manager.toggle_delete_mode(HOST)
        order,save,backup,recycle=self.file_fixture(backup_error=OSError('isolated backup failure'))
        self.assertFalse(self.manager.delete_slot_save(4,HOST)[0])
        self.assertEqual(order,['backup']);recycle.assert_not_called();save.assert_not_called()
        self.assertEqual(self.manager.get_session_state(HOST),slots.STATE_DELETE_MODE)

    def test_locked_file_failure_preserves_slot_and_delete_mode(self):
        self.manager.toggle_delete_mode(HOST)
        order,save,backup,recycle=self.file_fixture(recycle_error=PermissionError('isolated file in use'))
        self.assertFalse(self.manager.delete_slot_save(4,HOST)[0])
        self.assertEqual(order,['backup','recycle']);save.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'fixture')
        self.assertEqual(self.manager.get_session_state(HOST),slots.STATE_DELETE_MODE)


def load_tests(loader, tests, pattern):
    tests.addTests(loader.loadTestsFromTestCase(base.SlotDeleteTests))
    return tests


if __name__=='__main__':
    unittest.main(verbosity=2)
