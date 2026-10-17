"""每个 Agent 一个 JSON 文件的简单存储：文件名清洗、原子写入与删除。

文件不存在或已损坏时读出 None，由子类决定默认值——这些都是可以重新累计的统计数据，
不应因为一个坏文件让主流程报错。并发由各子类自己的模块级锁保护。
"""

import hashlib
import json
import os
from typing import Any, Dict, Optional


def _signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _file_digest(handle):
    """Hash the descriptor's raw bytes without decoding or retaining its body."""
    handle.seek(0)
    digest = hashlib.sha256()
    while chunk := handle.buffer.read(64 * 1024):
        digest.update(chunk)
    return digest.digest()


class JsonFileStore:
    def __init__(self, data_dir):
        self.data_dir = str(data_dir)

    def _path(self, agent_id) -> str:
        safe_id = str(agent_id).replace('/', '_').replace('\\', '_')
        return os.path.join(self.data_dir, f'{safe_id}.json')

    def _read_json(self, agent_id) -> Optional[Dict[str, Any]]:
        try:
            with open(self._path(agent_id), encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_json(self, agent_id, data: Dict[str, Any], *, verify_signature=False) -> Optional[os.stat_result]:
        """Write atomically, optionally returning a verified POSIX signature.

        Only write-through caches need a signature. Keep the descriptor across
        rename and compare bounded, streaming digests: rename updates ctime, so
        metadata alone cannot detect an in-place edit which restores size/mtime.
        Other stores and Windows keep the original close-before-replace path.
        """
        os.makedirs(self.data_dir, exist_ok=True)
        path = self._path(agent_id)
        temp_path = f'{path}.tmp'
        verify = verify_signature and os.name != 'nt'
        with open(temp_path, 'w+' if verify else 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
            if verify:
                handle.flush()
                before = os.fstat(handle.fileno())
                expected = _file_digest(handle)
                stable_before = _signature(before) == _signature(os.fstat(handle.fileno()))
                os.replace(temp_path, path)
                written = os.fstat(handle.fileno())
                actual = _file_digest(handle)
                verified = os.fstat(handle.fileno())
                if (not stable_before or expected != actual
                        or _signature(written) != _signature(verified)
                        or (verified.st_size, verified.st_mtime_ns) != (before.st_size, before.st_mtime_ns)):
                    return None
                return verified
        os.replace(temp_path, path)
        return None

    def _remove(self, agent_id) -> None:
        try:
            os.remove(self._path(agent_id))
        except FileNotFoundError:
            pass
