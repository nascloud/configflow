"""Profile-scoped OxiDNS template settings."""
from copy import deepcopy
from flask import jsonify, request
from backend.routes import oxidns_bp
from backend.common.auth import require_auth
from backend.common.config import get_config, save_config
from backend.common.oxidns_settings import normalize_oxidns_settings, OxidnsSettingsError


@oxidns_bp.route('/settings', methods=['GET', 'POST'])
@require_auth
def oxidns_settings():
    config = get_config()
    try:
        if request.method == 'GET':
            return jsonify(normalize_oxidns_settings(config.get('oxidns', {})))
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise OxidnsSettingsError('OxiDNS 设置必须是对象')
        # Whole-object updates are deliberate: the editor always loads defaults,
        # and callers can reset a feature without stale fields surviving.
        settings = normalize_oxidns_settings(value)
        config['oxidns'] = settings
        save_config(config)
        return jsonify({'success': True, 'settings': settings})
    except OxidnsSettingsError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@oxidns_bp.route('/render', methods=['POST'])
@require_auth
def render_oxidns_draft():
    """Render an authenticated editor draft without persisting or redacting it."""
    from backend.converters.oxidns import generate_oxidns_config
    try:
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or not isinstance(value.get('settings'), dict):
            raise OxidnsSettingsError('请提供 settings 配置对象')
        base_url = value.get('base_url', request.url_root.rstrip('/'))
        if not isinstance(base_url, str):
            raise OxidnsSettingsError('base_url 必须为文本')
        config = deepcopy(get_config())
        config['oxidns'] = normalize_oxidns_settings(value['settings'])
        response = jsonify({'config': generate_oxidns_config(config, base_url)})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except OxidnsSettingsError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
