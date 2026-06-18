#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config_manager import ConfigManager


class TestConfigManager(unittest.TestCase):
    """ConfigManager 单元测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestConfigManager")
        self.logger.setLevel(logging.CRITICAL)

    def _make_config(self, content: str) -> str:
        """创建临时配置文件并写入内容"""
        fd, path = tempfile.mkstemp(suffix='.ini')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_load_missing_file_generates_default(self):
        """测试配置文件不存在时生成默认配置"""
        nonexistent = os.path.join(tempfile.gettempdir(), 'nonexistent_config_test.ini')
        # 清理可能残留的文件
        if os.path.exists(nonexistent):
            os.unlink(nonexistent)

        cm = ConfigManager(nonexistent, self.logger)
        try:
            cm.load()
        except SystemExit:
            pass
        # 应该已生成配置文件
        self.assertTrue(os.path.exists(nonexistent))
        os.unlink(nonexistent)

    def test_load_basic_config(self):
        """测试加载基本配置文件"""
        content = r"""[Paths]
m9a_folders = Z:\M9A,Z:\M9A2

[Logs]
save_enabled = true
max_files = 10

[GitHub]
repo = MAA1999/M9A
m9a_update_channel = preview
proxy = http://127.0.0.1:7890
"""
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            cm.load()
            self.assertEqual(cm.m9a_folders, [r'Z:\M9A', r'Z:\M9A2'])
            self.assertTrue(cm.log_save_enabled)
            self.assertEqual(cm.log_max_files, 10)
            self.assertEqual(cm.github_repo, 'MAA1999/M9A')
            self.assertEqual(cm.github_release_version, 'preview')
            self.assertEqual(cm.github_proxy, 'http://127.0.0.1:7890')
        finally:
            os.unlink(path)

    def test_load_empty_folders(self):
        """测试 M9A 文件夹配置为空"""
        content = r"""[Paths]
m9a_folders =
"""
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            cm.load()
            self.assertEqual(cm.m9a_folders, [])
        finally:
            os.unlink(path)

    def test_default_values(self):
        """测试默认值（含新增的 [SelfUpdate] 节自动补全）"""
        content = (
            "[Paths]\n"
            "m9a_folders =\n"
            "\n"
            "[Logs]\n"
            "\n"
            "[GitHub]\n"
            "repo = MAA1999/M9A\n"
            "\n"
            "[SelfUpdate]\n"
        )
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            self.assertEqual(cm.archive_folder_path, '存档文件夹')
            self.assertEqual(cm.log_max_files, 5)
            self.assertEqual(cm.github_repo, 'MAA1999/M9A')
            self.assertEqual(cm.github_release_version, 'preview')
            self.assertEqual(cm.github_proxy, '')
            self.assertTrue(cm.self_update_enabled)
        finally:
            os.unlink(path)

    def test_resolve_temp_folder_empty(self):
        """测试空配置时解析临时文件夹"""
        from modules.config_manager import resolve_temp_folder
        result = resolve_temp_folder('')
        self.assertTrue(len(result) > 0)

    def test_resolve_temp_folder_temp_keyword(self):
        """测试 Temp 关键词"""
        from modules.config_manager import resolve_temp_folder
        result = resolve_temp_folder('Temp')
        self.assertIn('Temp', result)

    def test_resolve_temp_folder_custom(self):
        """测试自定义临时文件夹路径"""
        from modules.config_manager import resolve_temp_folder
        custom = r'D:\MyTemp'
        result = resolve_temp_folder(custom)
        self.assertEqual(result, custom)

    def test_validate_no_folders(self):
        """测试验证时缺少 M9A 文件夹"""
        content = r"""[Paths]
m9a_folders =
[GitHub]
repo = test/repo
"""
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            cm.load()
            self.assertFalse(cm.validate())
        finally:
            os.unlink(path)

    def test_validate_no_repo(self):
        """测试验证时缺少 GitHub 仓库"""
        content = r"""[Paths]
m9a_folders = Z:\M9A
[GitHub]
repo =
"""
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            cm.load()
            self.assertFalse(cm.validate())
        finally:
            os.unlink(path)

    def test_validate_bad_m9a_update_channel(self):
        """测试非法的 m9a_update_channel"""
        content = r"""[Paths]
m9a_folders = Z:\M9A
[GitHub]
repo = test/repo
m9a_update_channel = invalid
"""
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            cm.load()
            self.assertFalse(cm.validate())
        finally:
            os.unlink(path)

    def test_archive_folder_path_fallback(self):
        """测试 archive_folder_path 为空时回退到默认值"""
        content = r"""[Paths]
m9a_folders = Z:\M9A
archive_folder_path =
[GitHub]
repo = test/repo
"""
        path = self._make_config(content)
        try:
            cm = ConfigManager(path, self.logger)
            cm.load()
            self.assertEqual(cm.archive_folder_path, '存档文件夹')
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
