"""节点管理路由"""
from flask import request, jsonify
import uuid

from backend.routes import nodes_bp
from backend.common.auth import require_auth
from backend.common.config import get_config, update_config_transaction
from backend.utils.reorder import resolve_new_order


@nodes_bp.route('', methods=['GET', 'POST'])
@require_auth
def handle_nodes():
    """节点管理"""
    config_data = get_config()

    if request.method == 'GET':
        return jsonify(config_data['nodes'])

    elif request.method == 'POST':
        node = request.json
        if not isinstance(node, dict):
            return jsonify({'success': False, 'message': '节点请求必须是 JSON 对象'}), 400
        # 如果没有 ID，生成一个唯一 ID
        if 'id' not in node:
            node['id'] = f"node_{uuid.uuid4().hex[:8]}"
        from backend.utils.dialer_references import validate_dialers, DialerReferenceError
        def create(profile):
            profile.setdefault('nodes', []).append(node)
            validate_dialers(profile)
        try:
            update_config_transaction(create)
        except DialerReferenceError as exc:
            return jsonify({'success': False, 'message': str(exc)}), 400
        return jsonify({'success': True, 'data': node})


@nodes_bp.route('/<node_id>', methods=['DELETE', 'PUT'])
@require_auth
def handle_node(node_id):
    """单个节点操作"""
    from werkzeug.exceptions import Conflict, NotFound
    from backend.utils.dialer_references import validate_dialers, incoming_dialers, DialerReferenceError
    new_data = request.get_json() if request.method == 'PUT' else None
    deleting = request.method == 'DELETE'
    if not deleting and not isinstance(new_data, dict):
        return jsonify({'success': False, 'message': '节点请求必须为非 null JSON 对象'}), 400

    def mutate(profile):
        nodes = profile.setdefault('nodes', [])
        original = next((n for n in nodes if n.get('id') == node_id), None)
        if original is None:
            raise NotFound('Node not found')
        if not deleting:
            if new_data.get('id', node_id) != node_id:
                raise DialerReferenceError('节点 ID 不可修改')
            new_data['id'] = node_id
        removing = deleting or not new_data.get('enabled', True)
        if removing and incoming_dialers(profile, 'node', node_id):
            raise Conflict('节点仍被拨号代理引用，请先修改引用后再删除或禁用')
        if deleting:
            nodes.remove(original)
        else:
            nodes[nodes.index(original)] = new_data
        validate_dialers(profile)
        if removing:
            for group in profile.get('proxy_groups', []):
                group['manual_nodes'] = [id for id in group.get('manual_nodes', []) if id != node_id]
                group['proxies_order'] = [item for item in group.get('proxies_order', [])
                                          if not (item.get('type') == 'node' and item.get('id') == node_id)]
                if group.get('source') == 'node':
                    group['proxies'] = [id for id in group.get('proxies', []) if id != node_id]
            for agg in profile.get('subscription_aggregations', []):
                if node_id in agg.get('nodes', []):
                    agg['nodes'] = [id for id in agg['nodes'] if id != node_id]
                    if not agg.get('nodes') and not agg.get('subscriptions'):
                        agg['enabled'] = False
                        for group in profile.get('proxy_groups', []):
                            group['aggregations'] = [id for id in group.get('aggregations', []) if id != agg['id']]
    try:
        update_config_transaction(mutate)
    except DialerReferenceError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except (Conflict, NotFound) as exc:
        return jsonify({'success': False, 'message': exc.description}), exc.code
    return jsonify({'success': True, **({'data': new_data} if new_data is not None else {})})


@nodes_bp.route('/reorder', methods=['POST'])
@require_auth
def reorder_nodes():
    """批量更新节点顺序"""
    from backend.utils.dialer_references import validate_dialers, DialerReferenceError
    from werkzeug.exceptions import NotFound
    order = []
    body = request.get_json() or {}
    def mutate(profile):
        new_order, missing = resolve_new_order(profile.get('nodes', []), body, 'nodes')
        if missing:
            raise NotFound(f'以下节点 id 不存在: {missing}')
        profile['nodes'] = new_order
        validate_dialers(profile)
        order.extend(item.get('id') for item in new_order)
    try:
        update_config_transaction(mutate)
        return jsonify({'success': True, 'order': order})
    except DialerReferenceError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except NotFound as exc:
        return jsonify({'success': False, 'message': exc.description}), 404
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500
