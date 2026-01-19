"""Windows exe 入口文件"""
import sys
import os
from flask import send_from_directory, jsonify

# 获取应用路径（PyInstaller 打包后的路径）
if getattr(sys, 'frozen', False):
    # 运行在 PyInstaller 打包的 exe 中
    application_path = sys._MEIPASS
    # 数据目录：优先使用环境变量，否则使用当前工作目录
    DATA_DIR = os.environ.get('DATA_DIR', os.getcwd())
else:
    # 运行在普通 Python 环境中
    application_path = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.environ.get('DATA_DIR', application_path)

# 设置环境变量（如果之前没有设置）
if 'DATA_DIR' not in os.environ:
    os.environ['DATA_DIR'] = DATA_DIR

# 导入 Flask backend（backend 模块已通过 PyInstaller 的 collect_submodules 打包）
from backend.app import app, load_config, validate_required_env_vars

# 静态文件目录
STATIC_DIR = os.path.join(application_path, 'static')

# 添加静态文件服务路由
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_static(path):
    """服务前端静态文件"""
    # API 路由由后端处理
    if path.startswith('api/'):
        return jsonify({'error': 'Not Found'}), 404

    # 如果是静态资源文件
    if os.path.exists(STATIC_DIR):
        static_file_path = os.path.join(STATIC_DIR, path)
        if path and os.path.isfile(static_file_path):
            return send_from_directory(STATIC_DIR, path)

        # SPA 路由支持：返回 index.html
        index_path = os.path.join(STATIC_DIR, 'index.html')
        if os.path.exists(index_path):
            return send_from_directory(STATIC_DIR, 'index.html')

    return jsonify({'error': 'Not Found'}), 404

if __name__ == '__main__':
    # 验证必需的环境变量
    validate_required_env_vars()

    # 加载配置
    load_config()

    # 启动 Flask 服务器
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting config_flow server on http://0.0.0.0:{port}")

    app.run(host='0.0.0.0', port=port, debug=False)
