"""Profile-scoped OxiDNS template settings."""
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
