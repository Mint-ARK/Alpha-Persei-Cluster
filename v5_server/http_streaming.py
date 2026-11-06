"""Close streamed WSGI responses even when the HTTP server's drain step fails."""
import ssl
import threading

_CLOSERS = 'aethergazer.response_closers'


class _DisconnectReader:
    def __init__(self, source):
        self.source = source

    def __getattr__(self, name):
        return getattr(self.source, name)

    def read(self, *args, **kwargs):
        try:
            return self.source.read(*args, **kwargs)
        except ssl.SSLError as error:
            # Some OpenSSL/Python combinations use generic SSLError for a
            # missing close_notify. Werkzeug already treats SSLEOFError as a
            # dropped connection. Other TLS failures retain their real type.
            if getattr(error, 'reason', None) != 'UNEXPECTED_EOF_WHILE_READING':
                raise
            raise ssl.SSLEOFError(error.errno, str(error)) from error


class _CloseOnce:
    def __init__(self, body):
        self.body = body
        self.iterator = iter(body)
        self.closed = False
        self.lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        return next(self.iterator)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        close = getattr(self.body, 'close', None) or getattr(self.iterator, 'close', None)
        if close:
            close()


def ensure_response_closed(app):
    def serve(environ, start_response):
        response = _CloseOnce(app(environ, start_response))
        closers = environ.get(_CLOSERS)
        if closers is not None:
            closers.append(response)
        return response
    return serve


class DisconnectAwareHandlerMixin:
    def make_environ(self):
        environ = super().make_environ()
        environ[_CLOSERS] = self._response_closers
        return environ

    def run_wsgi(self):
        self._response_closers = []
        source = self.rfile
        self.rfile = _DisconnectReader(source)
        try:
            return super().run_wsgi()
        finally:
            self.rfile = source
            # Werkzeug drains request bytes before calling application_iter.close.
            # A transport error during that drain must not skip response cleanup.
            closers, self._response_closers = self._response_closers, []
            for response in closers:
                try:
                    response.close()
                except Exception as error:
                    self.server.log('error', f'HTTP response cleanup failed: {type(error).__name__}: {error}')
