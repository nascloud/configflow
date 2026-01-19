"""日志管理路由"""
import os
import re
from flask import jsonify, request
from pathlib import Path
from datetime import datetime
from backend.routes import Blueprint

logs_bp = Blueprint('logs', __name__, url_prefix='/api/logs')


def get_log_file_path():
    """获取日志文件路径"""
    # 参考 config.py 的逻辑：优先使用环境变量，如果目录不存在则回退到当前目录
    data_dir = os.environ.get('DATA_DIR', '/data')
    if not os.path.exists(data_dir):
        data_dir = '.'  # 开发模式，使用当前目录

    default_log_file = str(Path(data_dir) / 'app.log')

    # 从环境变量获取日志文件路径，如果没有则使用默认路径
    log_file = os.environ.get('LOG_FILE', default_log_file)
    log_path = Path(log_file)

    # 如果日志文件不存在，尝试其他可能的位置
    if not log_path.exists():
        possible_paths = [
            Path(data_dir) / 'app.log',
            './app.log',
            'data/app.log',
            'logs/app.log',
            '/var/log/configflow/app.log',
            'app.log'
        ]
        for path in possible_paths:
            path_obj = Path(path)
            if path_obj.exists():
                log_path = path_obj
                break

    return log_path


@logs_bp.route('/tail', methods=['GET'])
def get_logs():
    """获取日志内容

    支持参数：
    - lines: 返回最后N行，默认100
    - search: 搜索关键词
    - level: 日志级别过滤 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    try:
        log_path = get_log_file_path()

        # 获取参数
        lines = request.args.get('lines', 100, type=int)
        search = request.args.get('search', '', type=str)
        level_filter = request.args.get('level', '', type=str).upper()

        # 限制最大行数
        lines = min(lines, 10000)

        if not log_path.exists():
            return jsonify({
                'success': True,
                'logs': [],
                'total_lines': 0,
                'message': f'日志文件不存在: {log_path}'
            })

        # 读取日志文件
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()

        total_lines = len(all_lines)

        # 获取最后N行
        log_lines = all_lines[-lines:] if lines > 0 else all_lines

        # 过滤日志
        filtered_logs = []
        for line in log_lines:
            # 关键词过滤
            if search and search.lower() not in line.lower():
                continue

            # 日志级别过滤
            if level_filter:
                # 匹配常见的日志级别格式
                # 例如: 2024-01-01 12:00:00 - INFO - message
                #      [2024-01-01 12:00:00] INFO: message
                #      INFO: message
                if not re.search(rf'\b{level_filter}\b', line, re.IGNORECASE):
                    continue

            filtered_logs.append(line.rstrip('\n'))

        return jsonify({
            'success': True,
            'logs': filtered_logs,
            'total_lines': total_lines,
            'filtered_lines': len(filtered_logs),
            'log_file': str(log_path)
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'logs': [],
            'total_lines': 0
        }), 500


@logs_bp.route('/info', methods=['GET'])
def get_log_info():
    """获取日志文件信息"""
    try:
        log_path = get_log_file_path()

        if not log_path.exists():
            return jsonify({
                'success': True,
                'exists': False,
                'path': str(log_path)
            })

        # 获取文件信息
        stat = log_path.stat()

        return jsonify({
            'success': True,
            'exists': True,
            'path': str(log_path),
            'size': stat.st_size,
            'size_mb': round(stat.st_size / (1024 * 1024), 2),
            'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'created': datetime.fromtimestamp(stat.st_ctime).isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@logs_bp.route('/clear', methods=['POST'])
def clear_logs():
    """清空日志文件"""
    try:
        log_path = get_log_file_path()

        if not log_path.exists():
            return jsonify({
                'success': True,
                'message': '日志文件不存在'
            })

        # 清空日志文件
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('')

        return jsonify({
            'success': True,
            'message': '日志已清空'
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
