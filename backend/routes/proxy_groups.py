"""策略组管理路由"""
from flask import request, jsonify

from backend.routes import proxy_groups_bp
from backend.common.auth import require_auth
from backend.common.config import get_config, save_config


@proxy_groups_bp.route('', methods=['GET', 'POST'])
@require_auth
def handle_proxy_groups():
    """策略组管理"""
    config_data = get_config()

    if request.method == 'GET':
        return jsonify(config_data['proxy_groups'])

    elif request.method == 'POST':
        group = request.json
        config_data['proxy_groups'].append(group)
        save_config()
        return jsonify({'success': True, 'data': group})


@proxy_groups_bp.route('/<group_id>', methods=['DELETE', 'PUT'])
@require_auth
def handle_proxy_group(group_id):
    """单个策略组操作"""
    config_data = get_config()
    groups = config_data['proxy_groups']

    if request.method == 'DELETE':
        # 删除策略组
        config_data['proxy_groups'] = [g for g in groups if g['id'] != group_id]

        # 清理其他策略组中对被删除策略组的引用
        for group in config_data['proxy_groups']:
            if 'include_groups' in group and group_id in group['include_groups']:
                group['include_groups'].remove(group_id)

        save_config()
        return jsonify({'success': True})

    elif request.method == 'PUT':
        for i, g in enumerate(groups):
            if g['id'] == group_id:
                # 清理策略组数据:如果选择了聚合,清空subscriptions和manual_nodes字段
                group_data = request.json
                if group_data.get('aggregations') and len(group_data.get('aggregations', [])) > 0:
                    # 只保留聚合ID,清空策略组自身的subscriptions和manual_nodes
                    # 注意:只有当subscriptions/manual_nodes为空时才清理,如果用户同时选择了聚合和订阅/节点,则保留
                    pass  # 暂时不做强制清理,因为用户可能同时选择聚合和订阅/节点

                config_data['proxy_groups'][i] = group_data
                save_config()
                return jsonify({'success': True, 'data': group_data})
        return jsonify({'success': False, 'message': 'Group not found'}), 404


@proxy_groups_bp.route('/reorder', methods=['POST'])
@require_auth
def reorder_proxy_groups():
    """批量更新策略组顺序"""
    try:
        config_data = get_config()
        new_order = request.json.get('groups', [])
        config_data['proxy_groups'] = new_order
        save_config()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
