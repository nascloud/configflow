"""Encode large ordinary JSON responses once, with the usual redaction policy.

Editor and Agent artifact endpoints must keep their existing response path:
their exceptions are applied by the global response hook. Non-native JSON
values also keep that path so Flask's custom encoders retain their semantics.
"""

import re

from flask import current_app, jsonify, request
from flask.json.provider import DefaultJSONProvider

from backend.common.config_export import _external_payload_secrets, _sanitize_with_secrets


_SURROGATE = re.compile('[\ud800-\udfff]')


class _SanitizedResponse:
    __slots__ = ('body', 'secrets', 'endpoint')

    def __init__(self, body, secrets):
        self.body = body
        self.secrets = secrets
        self.endpoint = request.endpoint


def _plain_json_tree(payload):
    """Check encoder parity using a depth-bounded traversal, not a wide stack."""
    if type(payload) not in (dict, list):
        return False
    active = set()
    stack = [(iter((payload,)), None)]
    while stack:
        iterator, owner = stack[-1]
        try:
            value = next(iterator)
        except StopIteration:
            stack.pop()
            if owner is not None:
                active.remove(owner)
            continue
        kind = type(value)
        if kind is str:
            # JSON decoding combines UTF-16 pairs. Keep that normalization
            # before redaction for unusual Python strings containing surrogates.
            if _SURROGATE.search(value):
                return False
            continue
        if kind in (int, float, bool, type(None)):
            continue
        if kind not in (dict, list) or len(stack) > 128 or id(value) in active:
            return False
        if kind is dict and any(type(key) is not str or _SURROGATE.search(key) for key in value):
            return False
        active.add(id(value))
        stack.append((iter(value.values() if kind is dict else value), id(value)))
    return True


def jsonify_external(payload):
    """Return ordinary metadata; preserve the global hook for all fallbacks."""
    if (type(current_app.json) is not DefaultJSONProvider
            or 'dumps' in vars(current_app.json) or 'loads' in vars(current_app.json)
            or not _plain_json_tree(payload)):
        return jsonify(payload)
    secrets = _external_payload_secrets()
    sanitized = _sanitize_with_secrets(payload, secrets, sort_dict_keys=current_app.json.sort_keys)
    # Match the final global-hook encoding, including its whitespace and lack
    # of jsonify's trailing newline. No intermediate JSON bytes/decoded tree.
    response = current_app.response_class(
        current_app.json.dumps(sanitized), mimetype=current_app.json.mimetype,
    )
    response._configflow_sanitized_json = _SanitizedResponse(response.get_data(), secrets)
    return response


def consume_sanitized_response(response):
    """Skip only an unchanged body sanitized against current credentials.

    A later response hook can change the body or a request can rotate tokens.
    Both cases must fall back to the global sanitizer. The marker is private
    server-side state, never an HTTP header or a field supplied by a client.
    """
    marker = getattr(response, '_configflow_sanitized_json', None)
    if marker is None:
        return False
    del response._configflow_sanitized_json
    return (
        type(marker) is _SanitizedResponse
        and marker.endpoint == request.endpoint
        and response.get_data() == marker.body
        and _external_payload_secrets() == marker.secrets
    )
