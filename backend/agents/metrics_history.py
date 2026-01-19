"""Agent 监控数据历史存储"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from threading import Lock


class MetricsHistory:
    """Agent 监控数据历史管理"""

    def __init__(self, data_dir: str = None):
        """
        初始化监控历史管理器

        Args:
            data_dir: 数据存储目录，None 则自动选择
        """
        # 如果未指定目录，根据环境自动选择
        if data_dir is None:
            # 检查是否在生产环境（/opt/configflow 存在）
            if os.path.exists('/opt/configflow'):
                data_dir = "/opt/configflow/data/metrics"
            else:
                # 开发环境：使用项目根目录下的 data 目录
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                data_dir = os.path.join(project_root, "data", "metrics")

        self.data_dir = data_dir
        self.lock = Lock()

        # 确保数据目录存在
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except PermissionError:
            # 如果没有权限，回退到临时目录
            import tempfile
            self.data_dir = os.path.join(tempfile.gettempdir(), "configflow_metrics")
            os.makedirs(self.data_dir, exist_ok=True)
            print(f"Warning: Using temp directory for metrics: {self.data_dir}")

    def _get_agent_file(self, agent_id: str) -> str:
        """获取 Agent 的历史数据文件路径"""
        return os.path.join(self.data_dir, f"{agent_id}_metrics.json")

    def _load_agent_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """加载 Agent 的历史数据"""
        file_path = self._get_agent_file(agent_id)

        if not os.path.exists(file_path):
            return []

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('metrics', [])
        except Exception as e:
            print(f"Error loading metrics history for agent {agent_id}: {e}")
            return []

    def _save_agent_history(self, agent_id: str, metrics: List[Dict[str, Any]]):
        """保存 Agent 的历史数据"""
        file_path = self._get_agent_file(agent_id)

        try:
            data = {
                'agent_id': agent_id,
                'updated_at': datetime.now().isoformat(),
                'metrics': metrics
            }

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving metrics history for agent {agent_id}: {e}")

    def _cleanup_old_data(self, metrics: List[Dict[str, Any]], hours: int = 24) -> List[Dict[str, Any]]:
        """清理超过指定小时数的旧数据"""
        if not metrics:
            return []

        cutoff_time = datetime.now() - timedelta(hours=hours)

        cleaned_metrics = []
        for metric in metrics:
            try:
                # 解析时间戳
                timestamp = datetime.fromisoformat(metric.get('timestamp', ''))
                if timestamp >= cutoff_time:
                    cleaned_metrics.append(metric)
            except (ValueError, TypeError):
                # 如果时间戳无效，跳过该数据点
                continue

        return cleaned_metrics

    def add_metrics(self, agent_id: str, system_metrics: Dict[str, Any]) -> bool:
        """
        添加监控数据点

        Args:
            agent_id: Agent ID
            system_metrics: 系统监控数据

        Returns:
            bool: 是否添加成功
        """
        with self.lock:
            try:
                # 加载现有历史数据
                metrics = self._load_agent_history(agent_id)

                # 添加新数据点
                data_point = {
                    'timestamp': datetime.now().isoformat(),
                    'cpu': system_metrics.get('cpu', {}),
                    'memory': system_metrics.get('memory', {}),
                    'disk': system_metrics.get('disk', {}),
                    'network': system_metrics.get('network', {})
                }
                metrics.append(data_point)

                # 清理超过 24 小时的旧数据
                metrics = self._cleanup_old_data(metrics, hours=24)

                # 保存更新后的历史数据
                self._save_agent_history(agent_id, metrics)

                return True
            except Exception as e:
                print(f"Error adding metrics for agent {agent_id}: {e}")
                return False

    def get_metrics(self, agent_id: str, hours: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取 Agent 的监控历史数据

        Args:
            agent_id: Agent ID
            hours: 获取最近多少小时的数据（None 表示全部）

        Returns:
            List[Dict]: 历史数据列表
        """
        with self.lock:
            try:
                metrics = self._load_agent_history(agent_id)

                # 如果指定了小时数，过滤数据
                if hours is not None:
                    metrics = self._cleanup_old_data(metrics, hours=hours)

                return metrics
            except Exception as e:
                print(f"Error getting metrics for agent {agent_id}: {e}")
                return []

    def get_latest_metrics(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        获取 Agent 的最新监控数据

        Args:
            agent_id: Agent ID

        Returns:
            Dict: 最新监控数据，如果没有则返回 None
        """
        metrics = self.get_metrics(agent_id)

        if metrics:
            return metrics[-1]

        return None

    def delete_agent_history(self, agent_id: str) -> bool:
        """
        删除 Agent 的所有历史数据

        Args:
            agent_id: Agent ID

        Returns:
            bool: 是否删除成功
        """
        with self.lock:
            try:
                file_path = self._get_agent_file(agent_id)

                if os.path.exists(file_path):
                    os.remove(file_path)

                return True
            except Exception as e:
                print(f"Error deleting metrics history for agent {agent_id}: {e}")
                return False

    def get_metrics_summary(self, agent_id: str, hours: int = 1) -> Dict[str, Any]:
        """
        获取监控数据的统计摘要

        Args:
            agent_id: Agent ID
            hours: 统计最近多少小时的数据

        Returns:
            Dict: 统计摘要（平均值、最大值、最小值）
        """
        metrics = self.get_metrics(agent_id, hours=hours)

        if not metrics:
            return {}

        # 初始化统计数据
        cpu_values = []
        memory_values = []
        disk_values = []
        network_sent_values = []
        network_recv_values = []

        # 收集数据
        for metric in metrics:
            cpu = metric.get('cpu', {})
            memory = metric.get('memory', {})
            disk = metric.get('disk', {})
            network = metric.get('network', {})

            if 'usage_percent' in cpu:
                cpu_values.append(cpu['usage_percent'])
            if 'used_percent' in memory:
                memory_values.append(memory['used_percent'])
            if 'used_percent' in disk:
                disk_values.append(disk['used_percent'])
            if 'speed_sent' in network:
                network_sent_values.append(network['speed_sent'])
            if 'speed_recv' in network:
                network_recv_values.append(network['speed_recv'])

        # 计算统计值
        def calc_stats(values):
            if not values:
                return {'avg': 0, 'max': 0, 'min': 0}
            return {
                'avg': sum(values) / len(values),
                'max': max(values),
                'min': min(values)
            }

        return {
            'cpu': calc_stats(cpu_values),
            'memory': calc_stats(memory_values),
            'disk': calc_stats(disk_values),
            'network_sent': calc_stats(network_sent_values),
            'network_recv': calc_stats(network_recv_values),
            'data_points': len(metrics),
            'time_range_hours': hours
        }

    def get_traffic_stats(self, agent_id: str, period: str = 'total') -> Dict[str, Any]:
        """
        获取流量统计数据

        Args:
            agent_id: Agent ID
            period: 统计周期 ('total', 'today', 'week', 'hours_24')

        Returns:
            Dict: 流量统计数据
        """
        metrics = self.get_metrics(agent_id, hours=24)  # 最多获取24小时数据

        if not metrics:
            return {
                'bytes_sent': 0,
                'bytes_recv': 0,
                'bytes_sent_delta': 0,
                'bytes_recv_delta': 0,
                'avg_speed_sent': 0,
                'avg_speed_recv': 0,
                'period': period,
                'start_time': None,
                'end_time': None
            }

        # 根据周期过滤数据
        now = datetime.now()
        filtered_metrics = []

        if period == 'today':
            # 今日（从00:00开始）
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            filtered_metrics = [m for m in metrics
                              if datetime.fromisoformat(m['timestamp']) >= start_of_day]
        elif period == 'week':
            # 本周（从周一00:00开始）
            days_since_monday = now.weekday()
            start_of_week = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            filtered_metrics = [m for m in metrics
                              if datetime.fromisoformat(m['timestamp']) >= start_of_week]
        elif period == 'hours_24':
            # 最近24小时
            filtered_metrics = metrics
        else:  # 'total'
            # 全部可用数据
            filtered_metrics = metrics

        if not filtered_metrics:
            return {
                'bytes_sent': 0,
                'bytes_recv': 0,
                'bytes_sent_delta': 0,
                'bytes_recv_delta': 0,
                'avg_speed_sent': 0,
                'avg_speed_recv': 0,
                'period': period,
                'start_time': None,
                'end_time': None
            }

        # 获取最新的累计流量值
        latest_metric = filtered_metrics[-1]
        latest_network = latest_metric.get('network', {})
        current_sent = latest_network.get('bytes_sent', 0)
        current_recv = latest_network.get('bytes_recv', 0)

        # 计算流量增量
        first_metric = filtered_metrics[0]
        first_network = first_metric.get('network', {})
        start_sent = first_network.get('bytes_sent', 0)
        start_recv = first_network.get('bytes_recv', 0)

        # 检测计数器重置（系统重启）
        sent_delta = current_sent - start_sent
        recv_delta = current_recv - start_recv

        # 如果差值为负，说明发生了重置，使用当前值作为增量
        if sent_delta < 0:
            sent_delta = current_sent
        if recv_delta < 0:
            recv_delta = current_recv

        # 计算平均速率
        start_time = datetime.fromisoformat(first_metric['timestamp'])
        end_time = datetime.fromisoformat(latest_metric['timestamp'])
        duration_seconds = (end_time - start_time).total_seconds()

        avg_speed_sent = sent_delta / duration_seconds if duration_seconds > 0 else 0
        avg_speed_recv = recv_delta / duration_seconds if duration_seconds > 0 else 0

        return {
            'bytes_sent': current_sent,  # 当前累计总上传
            'bytes_recv': current_recv,  # 当前累计总下载
            'bytes_sent_delta': sent_delta,  # 时间段内上传增量
            'bytes_recv_delta': recv_delta,  # 时间段内下载增量
            'avg_speed_sent': avg_speed_sent,  # 平均上传速率
            'avg_speed_recv': avg_speed_recv,  # 平均下载速率
            'period': period,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'data_points': len(filtered_metrics)
        }

    def get_traffic_trend(self, agent_id: str, hours: int = 24,
                         interval_minutes: int = 5) -> List[Dict[str, Any]]:
        """
        获取流量趋势数据（用于绘制图表）

        Args:
            agent_id: Agent ID
            hours: 获取最近多少小时的数据
            interval_minutes: 数据点间隔（分钟）

        Returns:
            List[Dict]: 流量趋势数据点列表
        """
        metrics = self.get_metrics(agent_id, hours=hours)

        if not metrics:
            return []

        # 按时间间隔分组数据
        trend_data = []
        interval_seconds = interval_minutes * 60

        # 初始化上一个数据点
        prev_metric = None
        prev_time = None
        group_start_metric = None
        group_start_time = None

        for metric in metrics:
            timestamp = datetime.fromisoformat(metric['timestamp'])
            network = metric.get('network', {})

            # 第一个数据点
            if prev_metric is None:
                prev_metric = metric
                prev_time = timestamp
                group_start_metric = metric
                group_start_time = timestamp
                continue

            # 检查是否达到间隔时间
            time_diff = (timestamp - group_start_time).total_seconds()

            if time_diff >= interval_seconds:
                # 计算这个时间段的流量增量
                prev_network = prev_metric.get('network', {})

                sent_delta = network.get('bytes_sent', 0) - prev_network.get('bytes_sent', 0)
                recv_delta = network.get('bytes_recv', 0) - prev_network.get('bytes_recv', 0)

                # 处理计数器重置
                if sent_delta < 0:
                    sent_delta = network.get('bytes_sent', 0)
                if recv_delta < 0:
                    recv_delta = network.get('bytes_recv', 0)

                # 计算平均速率
                duration = (timestamp - prev_time).total_seconds()
                avg_speed_sent = sent_delta / duration if duration > 0 else 0
                avg_speed_recv = recv_delta / duration if duration > 0 else 0

                trend_data.append({
                    'timestamp': timestamp.isoformat(),
                    'bytes_sent': network.get('bytes_sent', 0),
                    'bytes_recv': network.get('bytes_recv', 0),
                    'sent_delta': sent_delta,
                    'recv_delta': recv_delta,
                    'speed_sent': avg_speed_sent,
                    'speed_recv': avg_speed_recv
                })

                # 更新分组起点
                group_start_metric = metric
                group_start_time = timestamp

            prev_metric = metric
            prev_time = timestamp

        # 添加最后一个数据点
        if prev_metric:
            network = prev_metric.get('network', {})
            trend_data.append({
                'timestamp': prev_time.isoformat(),
                'bytes_sent': network.get('bytes_sent', 0),
                'bytes_recv': network.get('bytes_recv', 0),
                'speed_sent': network.get('speed_sent', 0),
                'speed_recv': network.get('speed_recv', 0)
            })

        return trend_data
