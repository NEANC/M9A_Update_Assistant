#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import configparser
import logging
import sys

from pathlib import Path
from typing import Optional


class UpdateState:
    """自更新状态文件管理器，使用 INI 格式持久化状态机状态"""

    STATE_FILE_NAME = "update_state.ini"

    VALID_STATES = frozenset({
        "idle",
        "downloaded_verified",
        "helper_started",
        "replacing",
        "pending_new_verify",
        "verified",
        "rollback",
        "rollback_done",
        "failed_disabled",
    })

    _DEFAULTS = {
        "State": {"state": "idle", "last_error": ""},
        "Files": {"target": "", "new_file": "", "backup_file": "", "helper_file": ""},
        "Version": {"old_version": "", "new_version": "", "expected_sha256": ""},
        "Retry": {"retry_count": "0", "max_retry": "3"},
    }

    def __init__(self):
        """初始化状态对象，设置默认值"""
        self._config = configparser.ConfigParser(strict=False)
        self._ensure_defaults()
        self._file_path = self._resolve_file_path()

    @staticmethod
    def _get_exe_path() -> Path:
        """
        获取当前可执行文件的真实路径
        sys.argv[0] 在所有打包模式下均指向用户双击的真实 exe
        """
        argv_exe = Path(sys.argv[0]).resolve()
        if argv_exe.suffix.lower() == '.exe':
            return argv_exe
        return Path(sys.executable).resolve()

    @staticmethod
    def _resolve_file_path() -> Path:
        """解析状态文件路径，与可执行文件同目录"""
        return UpdateState._get_exe_path().with_name(UpdateState.STATE_FILE_NAME)

    def _ensure_defaults(self) -> None:
        """确保所有节和键存在"""
        for section, keys in self._DEFAULTS.items():
            if not self._config.has_section(section):
                self._config.add_section(section)
            for key, val in keys.items():
                if not self._config.has_option(section, key):
                    self._config.set(section, key, val)

    @classmethod
    def load(cls) -> Optional["UpdateState"]:
        """
        从 update_state.ini 加载状态

        Returns:
            UpdateState 实例，若文件不存在或损坏则返回 None
        """
        file_path = cls._resolve_file_path()
        if not file_path.exists():
            return None

        state = cls()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                state._config.read_file(f)
        except (configparser.Error, OSError) as e:
            logging.getLogger("M9AUpdateAssistant").warning(f"读取状态文件失败: {e}")
            return None

        state._ensure_defaults()
        current_state = state._config.get("State", "state", fallback="idle")
        if current_state not in cls.VALID_STATES:
            logging.getLogger("M9AUpdateAssistant").warning(f"未知状态: {current_state}")
            return None
        return state

    def save(self) -> None:
        """写入更新状态文件（原子写入：先写临时文件再替换）"""
        tmp_path = self._file_path.with_suffix('.ini.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                self._config.write(f)
            tmp_path.replace(self._file_path)
        except OSError as e:
            logging.getLogger("M9AUpdateAssistant").error(f"写入状态文件失败: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, section: str, key: str, fallback: str = "") -> str:
        """读取状态值"""
        return self._config.get(section, key, fallback=fallback)

    def set(self, section: str, key: str, value: str) -> None:
        """设置状态值"""
        self._config.set(section, key, value)

    def transition(self, new_state: str) -> None:
        """
        执行状态转换并自动保存

        Args:
            new_state: 目标状态
        """
        if new_state not in self.VALID_STATES:
            raise ValueError(f"无效的状态转换: {new_state}")
        self._config.set("State", "state", new_state)
        self.save()

    def delete(self) -> None:
        """删除状态文件"""
        try:
            self._file_path.unlink(missing_ok=True)
        except OSError:
            pass

    def __getitem__(self, key: str) -> str:
        """通过键名自动路由到正确的节，如 state['target'] → Files.target"""
        for section, keys in self._DEFAULTS.items():
            if key in keys:
                return self._config.get(section, key, fallback="")
        raise KeyError(key)

    def __setitem__(self, key: str, value: str) -> None:
        """通过键名自动路由到正确的节"""
        for section, keys in self._DEFAULTS.items():
            if key in keys:
                self._config.set(section, key, value)
                return
        raise KeyError(key)
