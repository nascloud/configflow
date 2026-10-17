"""Dispatch recognized ConfigFlow HTTP callbacks without waiting on its pool.

The caller must establish the same-origin API boundary first. These calls still
pass through the normal Flask routing and authentication middleware; they never
inherit credentials or the original request's mutable context.
"""
from contextvars import Context, ContextVar
from time import monotonic
import weakref
from urllib.parse import urlsplit

import requests
from flask import current_app, has_app_context


_application = None
_callback_depth = ContextVar('configflow_local_http_depth', default=0)
_MISSING = object()


def register_application(app):
    global _application
    _application = weakref.ref(app)


class _RequestContext:
    def __init__(self, depth):
        self.context = Context()
        self.context.run(_callback_depth.set, depth)
        self.document = _MISSING

    def run(self, callback, *args, **kwargs):
        # Repository transactions use threading.local rather than ContextVar.
        # A nested request must not borrow the caller's in-progress document.
        from backend.common import config
        repository = config._repository
        local = repository._local if repository is not None else None
        previous = getattr(local, 'document', _MISSING) if local is not None else _MISSING
        if local is not None:
            local.document = None if self.document is _MISSING else self.document
        try:
            return self.context.run(callback, *args, **kwargs)
        finally:
            if local is not None:
                self.document = getattr(local, 'document', _MISSING)
                if previous is _MISSING:
                    local.__dict__.pop('document', None)
                else:
                    local.document = previous


class _ResponseBody:
    def __init__(self, response, context, read_timeout):
        self.response = response
        self.iterator = iter(response.response)
        self.context = context
        self.read_timeout = read_timeout

    def stream(self, amount, decode_content=True):
        try:
            while self.iterator is not None:
                try:
                    started = monotonic()
                    chunk = self.context.run(next, self.iterator)
                except StopIteration:
                    break
                _check_read_timeout(started, self.read_timeout)
                if amount:
                    for start in range(0, len(chunk), amount):
                        yield chunk[start:start + amount]
                elif chunk:
                    yield chunk
        finally:
            self.close()

    def close(self):
        response, self.response = self.response, None
        if response is not None:
            try:
                self.context.run(response.close)
            finally:
                self.iterator = None
                self.context = None

    release_conn = close


def _check_read_timeout(started, read_timeout):
    # Same-process route execution cannot be forcibly interrupted. Still report
    # an overdue response as a timeout and release its iterator when it returns.
    if read_timeout is not None and monotonic() - started > read_timeout:
        raise requests.ReadTimeout('ConfigFlow API callback exceeded its read timeout')


def request_local(url, *, method, prefix='', **kwargs):
    """Return a requests-compatible response, or None if no app is available."""
    app = current_app._get_current_object() if has_app_context() else (_application() if _application else None)
    if app is None:
        return None
    depth = _callback_depth.get()
    if depth >= 8:
        raise requests.TooManyRedirects('Too many nested ConfigFlow API callbacks')
    context = _RequestContext(depth + 1)
    # A fresh client and explicit headers preserve ordinary endpoint auth. In
    # particular, do not forward the outer request's cookie or internal token.
    prepared = requests.Request(method.upper(), url, **{
        key: kwargs[key] for key in ('headers', 'params', 'data', 'json', 'cookies', 'auth') if key in kwargs
    }).prepare()
    parsed = urlsplit(prepared.url)
    if not parsed.path.startswith(prefix + '/api/'):
        # Requests normalizes dot segments while preparing a URL. Recheck the
        # API boundary after that normalization rather than dispatch outside it.
        return None
    path = parsed.path[len(prefix):]
    if parsed.query:
        path += '?' + parsed.query
    base_url = f'{parsed.scheme}://{parsed.netloc}{prefix}'
    client = app.test_client(use_cookies=False)
    timeout = kwargs.get('timeout')
    read_timeout = timeout[1] if isinstance(timeout, tuple) else timeout
    started = monotonic()
    local = context.run(client.open, path, base_url=base_url, method=method.upper(),
                        headers=dict(prepared.headers), data=prepared.body, buffered=False,
                        follow_redirects=False)
    response = requests.Response()
    response.status_code = local.status_code
    response.headers = requests.structures.CaseInsensitiveDict(local.headers)
    response.reason = local.status.partition(' ')[2]
    response.url = prepared.url
    response.request = prepared
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.raw = _ResponseBody(local, context, read_timeout)
    try:
        _check_read_timeout(started, read_timeout)
    except requests.ReadTimeout:
        response.close()
        raise
    if not kwargs.get('stream'):
        response.content
    return response
