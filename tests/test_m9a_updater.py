#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import json
import logging
import os
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.m9a_updater import M9AUpdater


class TestGetVersionFromInterface(unittest.TestCase):
    """get_version_from_interface 静态方法测试"""

    def test_reads_version(self):
        """正常读取版本号"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'interface.json'), 'w') as f:
                json.dump({'version': 'v3.28.3'}, f)
            result = M9AUpdater.get_version_from_interface(tmpdir)
            self.assertEqual(result, 'v3.28.3')

    def test_no_file_fallback(self):
        """无 interface.json 回退"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = M9AUpdater.get_version_from_interface(tmpdir, 'v1.0.0')
            self.assertEqual(result, 'v1.0.0')

    def test_no_version_field(self):
        """无 version 字段"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'interface.json'), 'w') as f:
                json.dump({'other': 'data'}, f)
            result = M9AUpdater.get_version_from_interface(tmpdir, 'fallback')
            self.assertEqual(result, 'fallback')

    def test_empty_fallback_default(self):
        """默认 fallback 为空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = M9AUpdater.get_version_from_interface(tmpdir)
            self.assertEqual(result, '')

    def test_corrupted_json(self):
        """JSON 解析失败"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'interface.json'), 'w') as f:
                f.write('{not valid json}')
            result = M9AUpdater.get_version_from_interface(tmpdir, 'v9.9.9')
            self.assertEqual(result, 'v9.9.9')


class TestGetBackupName(unittest.TestCase):
    """get_backup_name 静态方法测试"""

    def test_standard_path(self):
        """标准路径"""
        import platform
        if platform.system() == 'Windows':
            name = M9AUpdater.get_backup_name(r'C:\M9A')
            self.assertEqual(name, 'C-M9A')
        else:
            name = M9AUpdater.get_backup_name('/home/user/M9A')
            self.assertEqual(name, '-M9A')

    def test_path_with_sub(self):
        """多级路径"""
        import platform
        if platform.system() == 'Windows':
            name = M9AUpdater.get_backup_name(r'D:\Games\M9A2')
            self.assertEqual(name, 'D-M9A2')
        else:
            name = M9AUpdater.get_backup_name('/opt/games/M9A2')
            self.assertEqual(name, '-M9A2')

    def test_no_drive_letter(self):
        """无盘符（UNC 路径）"""
        name = M9AUpdater.get_backup_name(r'\\server\share\M9A')
        # UNC 路径 drive 为空，replace(':','') 不变，结果为 \\server\share-M9A
        self.assertIn('M9A', name)


class TestCleanM9aFolder(unittest.TestCase):
    """clean_m9a_folder 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestUpdater")
        self.logger.setLevel(logging.CRITICAL)
        self.updater = M9AUpdater('存档', self.logger)

    def test_creates_nonexistent(self):
        """创建不存在的文件夹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, 'new_folder')
            result = self.updater.clean_m9a_folder(target)
            self.assertTrue(result)
            self.assertTrue(os.path.exists(target))

    def test_cleans_existing(self):
        """清理现有文件夹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, 'm9a')
            os.makedirs(os.path.join(target, 'sub'), exist_ok=True)
            with open(os.path.join(target, 'file.txt'), 'w') as f:
                f.write('data')
            with open(os.path.join(target, 'sub', 'nested.txt'), 'w') as f:
                f.write('data')

            result = self.updater.clean_m9a_folder(target)
            self.assertTrue(result)
            self.assertEqual(len(os.listdir(target)), 0)


class TestCleanTempFolder(unittest.TestCase):
    """clean_temp_folder 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestUpdater")
        self.logger.setLevel(logging.CRITICAL)
        self.updater = M9AUpdater('存档', self.logger)

    def test_removes_folder(self):
        """删除文件夹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, 'temp_to_clean')
            os.makedirs(target, exist_ok=True)
            with open(os.path.join(target, 'tmp.bin'), 'w') as f:
                f.write('temp data')

            result = self.updater.clean_temp_folder(target)
            self.assertTrue(result)
            self.assertFalse(os.path.exists(target))

    def test_nonexistent(self):
        """不存在的文件夹"""
        result = self.updater.clean_temp_folder('/nonexistent/path')
        self.assertFalse(result)


class TestBackupAndRestoreConfig(unittest.TestCase):
    """backup_config 与 restore_config 集成测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestUpdater")
        self.logger.setLevel(logging.CRITICAL)
        self.updater = M9AUpdater('测试存档', self.logger)
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_backup_and_restore(self):
        """全流程备份与回写"""
        m9a_folder = os.path.join(self.tmpdir, 'M9A')
        os.makedirs(os.path.join(m9a_folder, 'config'), exist_ok=True)
        with open(os.path.join(m9a_folder, 'config', 'settings.json'), 'w') as f:
            f.write('{"key": "value"}')

        # 写入版本号
        with open(os.path.join(m9a_folder, 'interface.json'), 'w') as f:
            json.dump({'version': 'v3.27.0'}, f)

        # 备份
        result = self.updater.backup_config(m9a_folder, 'v3.27.0')
        self.assertTrue(result)

        # 清理 M9A（模拟解压后的干净文件夹）
        self.updater.clean_m9a_folder(m9a_folder)
        os.makedirs(os.path.join(m9a_folder, 'config'), exist_ok=True)

        # 回写
        # 因为 clean 后 interface.json 被删，需要重新写入
        with open(os.path.join(m9a_folder, 'interface.json'), 'w') as f:
            json.dump({'version': 'v3.27.0'}, f)
        result = self.updater.restore_config(m9a_folder, 'v3.27.0')
        self.assertTrue(result)

        # 验证配置已恢复
        with open(os.path.join(m9a_folder, 'config', 'settings.json'), 'r') as f:
            content = f.read()
        self.assertEqual(content, '{"key": "value"}')

    def test_backup_no_config(self):
        """备份不存在的 config"""
        m9a_folder = os.path.join(self.tmpdir, 'M9A')
        os.makedirs(m9a_folder, exist_ok=True)
        with open(os.path.join(m9a_folder, 'interface.json'), 'w') as f:
            json.dump({'version': 'v1.0.0'}, f)

        result = self.updater.backup_config(m9a_folder, 'v1.0.0')
        self.assertFalse(result)

    def test_restore_no_backup(self):
        """回写不存在的备份"""
        m9a_folder = os.path.join(self.tmpdir, 'M9A')
        os.makedirs(m9a_folder, exist_ok=True)

        result = self.updater.restore_config(m9a_folder, 'v9.9.9')
        self.assertFalse(result)

    def test_backup_empty_version(self):
        """版本号为空跳过备份"""
        m9a_folder = os.path.join(self.tmpdir, 'M9A')
        os.makedirs(m9a_folder, exist_ok=True)

        result = self.updater.backup_config(m9a_folder, '')
        self.assertFalse(result)

    def test_find_lite_zip_not_found(self):
        """查找不存在的 CLI ZIP"""
        from modules.github_release_client import GitHubReleaseClient
        gh = GitHubReleaseClient('test/repo', 'release', '', self.logger)

        with tempfile.TemporaryDirectory() as tmp:
            result = self.updater.find_lite_zip(
                'M9A-win-x86_64-v*-Lite.zip', tmp, gh,
            )
            self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
