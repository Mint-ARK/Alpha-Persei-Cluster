"""Replay Werkzeug's real drain path without sockets, real saves or TLS changes."""
from pathlib import Path
import hashlib
import io
import ssl
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from http_streaming import DisconnectAwareHandlerMixin, ensure_response_closed
from werkzeug.serving import WSGIRequestHandler
import cdn_proxy as cdn
import cdn_transfer_control as transfer


def tls_error(reason):
    error = ssl.SSLError(ssl.SSL_ERROR_SSL, reason)
    error.reason = reason
    return error


class Body:
    def __init__(self, error=None):
        self.error = error
        self.close_count = 0
    def __iter__(self):
        if self.error:
            raise self.error
        yield b'fixture'
    def close(self):
        self.close_count += 1


class BaseHandler:
    run_wsgi = WSGIRequestHandler.run_wsgi
    def make_environ(self):
        return {'REQUEST_METHOD': 'GET', 'SERVER_PROTOCOL': 'HTTP/1.0', 'wsgi.url_scheme': 'https'}


class Handler(DisconnectAwareHandlerMixin, BaseHandler):
    pass


class DisconnectTests(unittest.TestCase):
    def handler(self, app, *, write_error=None, drain_error=None):
        handler = Handler()
        handler.server = SimpleNamespace(app=ensure_response_closed(app), passthrough_errors=False, log=Mock())
        handler.headers = {}
        handler.protocol_version = 'HTTP/1.0'
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.connection_dropped = Mock()
        handler.connection = object()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = write_error
        handler.rfile = Mock()
        handler.rfile.read.return_value = b''
        handler.rfile.read.side_effect = drain_error
        return handler

    def execute(self, handler, ready=True):
        selector = Mock()
        selector.select.return_value = [(object(), 1)] if ready else []
        with patch('werkzeug.serving.selectors.DefaultSelector', return_value=selector):
            handler.run_wsgi()

    def app_for(self, body):
        def app(environ, start_response):
            start_response('200 OK', [('Content-Type', 'application/octet-stream')])
            return body
        return app

    def test_exact_broken_pipe_then_generic_tls_eof_closes_once(self):
        body = Body()
        handler = self.handler(self.app_for(body), write_error=BrokenPipeError('client closed'),
                               drain_error=tls_error('UNEXPECTED_EOF_WHILE_READING'))
        original = handler.rfile
        self.execute(handler)
        self.assertEqual(body.close_count, 1)
        self.assertIs(handler.rfile, original)
        handler.connection_dropped.assert_called_once()
        self.assertIsInstance(handler.connection_dropped.call_args.args[0], ssl.SSLEOFError)
        handler.server.log.assert_not_called()

    def test_normal_response_does_not_double_close(self):
        body = Body()
        handler = self.handler(self.app_for(body))
        self.execute(handler, ready=False)
        self.assertEqual(body.close_count, 1)
        handler.connection_dropped.assert_not_called()
        handler.server.log.assert_not_called()

    def test_plain_broken_pipe_remains_a_disconnect(self):
        body = Body()
        handler = self.handler(self.app_for(body), write_error=BrokenPipeError())
        self.execute(handler)
        self.assertEqual(body.close_count, 1)
        handler.connection_dropped.assert_called_once()
        handler.server.log.assert_not_called()

    def test_other_tls_error_stays_visible_and_cleanup_still_runs(self):
        body = Body()
        handler = self.handler(self.app_for(body), drain_error=tls_error('CERTIFICATE_VERIFY_FAILED'))
        self.execute(handler)
        self.assertEqual(body.close_count, 1)
        handler.connection_dropped.assert_not_called()
        self.assertTrue(any('Error on request' in call.args[1] for call in handler.server.log.call_args_list))

    def test_real_application_failure_stays_visible(self):
        body = Body(error=ValueError('fixture digest failure'))
        handler = self.handler(self.app_for(body))
        self.execute(handler, ready=False)
        self.assertEqual(body.close_count, 1)
        self.assertTrue(any('fixture digest failure' in call.args[1] for call in handler.server.log.call_args_list))

    def test_relay_origin_and_passive_priority_released_on_drain_failure(self):
        directory = SERVER.parent / 'archive/http_disconnect_20261030/fixtures'
        directory.mkdir(parents=True, exist_ok=True)
        gate = transfer.PriorityGate(Path(tempfile.mkdtemp(dir=directory)))
        payload = b'fixture stream' * 100
        raw = io.BytesIO(payload)
        origin = SimpleNamespace(status=200, close=Mock(), read=raw.read, read1=raw.read,
                                 getheader=lambda name: str(len(payload)) if name.lower() == 'content-length' else None)
        conn = SimpleNamespace(close=Mock(), _cdn_abort_detach=None)
        def app(environ, start_response):
            response = cdn.relay_upstream('pc', hashlib.md5(payload).hexdigest() + '.ys')
            return response(environ, start_response)
        handler = self.handler(app, write_error=BrokenPipeError(), drain_error=tls_error('UNEXPECTED_EOF_WHILE_READING'))
        with patch.object(transfer, 'gate', gate), patch.object(cdn, '_upstream_opener', return_value=lambda *a: (conn, origin)):
            self.execute(handler)
        origin.close.assert_called_once()
        conn.close.assert_called_once()
        self.assertEqual(gate._passive, 0)
        self.assertIsNone(transfer.current_token())
        handler.server.log.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
