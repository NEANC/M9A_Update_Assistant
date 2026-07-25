#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import json
import logging
import os
import sys
import tempfile
import unittest
import zipfile

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.m9a_updater import (
    M9AUpdater,
    _parse_version_to_tuple,
    _collect_archive_versions,
    find_best_config_version,
)


class TestParseVersionToTuple(unittest.TestCase):
    """_parse_version_to_tuple 函数测试"""

    def test_standard_semver(self):
        """标准三位版本号"""
        self.assertEqual(_parse_version_to_tuple('v3.28.3'), (3, 28, 3, 3, 0))

    def test_no_v_prefix(self):
        """无 v 前缀"""
        self.assertEqual(_parse_version_to_tuple('3.28.3'), (3, 28, 3, 3, 0))

    def test_two_component(self):
        """两位版本号"""
        self.assertEqual(_parse_version_to_tuple('v2.0'), (2, 0, 0, 3, 0))

    def test_single_component(self):
        """一位版本号"""
        self.assertEqual(_parse_version_to_tuple('v1'), (1, 0, 0, 3, 0))

    def test_empty_string(self):
        """空字符串"""
        self.assertEqual(_parse_version_to_tuple(''), ())

    def test_invalid_string(self):
        """无效字符串"""
        self.assertEqual(_parse_version_to_tuple('abc'), ())

    def test_prerelease_suffix(self):
        """预发布后缀参与排序"""
        self.assertEqual(_parse_version_to_tuple('v3.19.0-beta1'), (3, 19, 0, 1, 1))

    def test_build_suffix(self):
        """构建后缀不影响稳定版排序"""
        self.assertEqual(_parse_version_to_tuple('v1.10.1+build.gb6da5ee'), (1, 10, 1, 3, 0))


class TestCollectArchiveVersions(unittest.TestCase):
    """_collect_archive_versions 函数测试"""

    def test_empty_dir(self):
        """空目录"""
        with tempfile.TemporaryDirectory() as tmp:
            result = _collect_archive_versions(Path(tmp), 'Z-M9A')
            self.assertEqual(result, [])

    def test_nonexistent_dir(self):
        """不存在的目录"""
        result = _collect_archive_versions(Path('/nonexistent/archive'), 'Z-M9A')
        self.assertEqual(result, [])

    def test_sorted_descending(self):
        """降序排列"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            for ver in ('v3.18.0', 'v3.20.0', 'v3.19.0', 'v4.0.0'):
                ver_dir = archive / ver
                (ver_dir / 'Z-M9A' / 'config').mkdir(parents=True)
            result = _collect_archive_versions(archive, 'Z-M9A')
            self.assertEqual(result, [((4, 0, 0, 3, 0), 'v4.0.0'), ((3, 20, 0, 3, 0), 'v3.20.0'),
                                       ((3, 19, 0, 3, 0), 'v3.19.0'), ((3, 18, 0, 3, 0), 'v3.18.0')])

    def test_skips_dirs_without_config(self):
        """跳过不含 config 的版本目录"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.20.0' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.19.0').mkdir()  # 无 backup_name/config
            (archive / 'v3.18.0' / 'Z-M9A').mkdir(parents=True)  # 有 backup_name 但无 config
            result = _collect_archive_versions(archive, 'Z-M9A')
            self.assertEqual(result, [((3, 20, 0, 3, 0), 'v3.20.0')])

    def test_skips_unparseable_version_names(self):
        """跳过无法解析的版本目录名"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'not-a-version' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.20.0' / 'Z-M9A' / 'config').mkdir(parents=True)
            result = _collect_archive_versions(archive, 'Z-M9A')
            self.assertEqual(result, [((3, 20, 0, 3, 0), 'v3.20.0')])


class TestFindBestConfigVersion(unittest.TestCase):
    """find_best_config_version 函数测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestFindBest")
        self.logger.setLevel(logging.CRITICAL)

    def test_exact_match(self):
        """精确命中目标版本"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.19.0' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.20.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'v3.19.0', self.logger,
            )
            self.assertEqual(result, 'v3.19.0')

    def test_target_not_found_use_lower(self):
        """目标版本不在存档中，使用更低版本"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.18.0' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.20.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'v3.19.0', self.logger,
            )
            self.assertEqual(result, 'v3.18.0')

    def test_no_lower_version_fallback(self):
        """所有存档版本均高于目标，回退到当前版本"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.20.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'v3.19.0', self.logger,
            )
            self.assertEqual(result, 'v3.20.0')

    def test_empty_archive_fallback(self):
        """存档目录无可用版本"""
        with tempfile.TemporaryDirectory() as tmp:
            result = find_best_config_version(
                Path(tmp), 'Z-M9A', 'v3.20.0', 'v3.19.0', self.logger,
            )
            self.assertEqual(result, 'v3.20.0')

    def test_invalid_target_version_fallback(self):
        """目标版本号无法解析"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.20.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'not-a-version', self.logger,
            )
            self.assertEqual(result, 'v3.20.0')

    def test_stable_target_uses_lower_prerelease_when_no_exact_match(self):
        """稳定版目标无精确备份时，可使用同版本更低预发布备份"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.19.0-beta1' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.18.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'v3.19.0', self.logger,
            )
            self.assertEqual(result, 'v3.19.0-beta1')

    def test_prerelease_target_exact_match(self):
        """目标版本带预发布后缀时，优先按目录名精确匹配"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.19.0-beta1' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.19.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'v3.19.0-beta1', self.logger,
            )
            self.assertEqual(result, 'v3.19.0-beta1')

    def test_prerelease_target_does_not_use_higher_stable(self):
        """预发布目标无精确备份时，不使用同版本正式版备份"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / 'v3.19.0' / 'Z-M9A' / 'config').mkdir(parents=True)
            (archive / 'v3.18.0' / 'Z-M9A' / 'config').mkdir(parents=True)

            result = find_best_config_version(
                archive, 'Z-M9A', 'v3.20.0', 'v3.19.0-beta1', self.logger,
            )
            self.assertEqual(result, 'v3.18.0')

    def test_prerelease_sort_order_is_deterministic(self):
        """alpha/beta/rc/stable 使用确定排序"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            for ver in ('v3.19.0-alpha2', 'v3.19.0-beta1', 'v3.19.0-rc1', 'v3.19.0'):
                (archive / ver / 'Z-M9A' / 'config').mkdir(parents=True)

            result = _collect_archive_versions(archive, 'Z-M9A')
            self.assertEqual([item[1] for item in result], [
                'v3.19.0',
                'v3.19.0-rc1',
                'v3.19.0-beta1',
                'v3.19.0-alpha2',
            ])


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


def _create_version_zip(zip_path: Path, version: str) -> None:
    """创建包含 interface.json 的测试 ZIP"""
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('interface.json', json.dumps({'version': version}))


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

    def test_find_cli_zip_not_found(self):
        """查找不存在的 CLI ZIP"""
        from modules.github_release_client import GitHubReleaseClient
        gh = GitHubReleaseClient('test/repo', 'release', '', self.logger)

        with tempfile.TemporaryDirectory() as tmp:
            result = self.updater.find_cli_zip(
                'M9A-win-x86_64-v*.zip', tmp, gh,
            )
            self.assertIsNone(result)

    def test_find_cli_zip_matches_target_version_without_suffix_filter(self):
        """缓存查找按 ZIP 内版本匹配，不依赖文件名后缀"""
        from modules.github_release_client import GitHubReleaseClient
        gh = GitHubReleaseClient('test/repo', 'release', '', self.logger)

        with tempfile.TemporaryDirectory() as tmp:
            zip_dir = Path(tmp) / 'ZIP'
            zip_dir.mkdir()
            expected = zip_dir / 'M9A-win-x86_64-v4.5.4-PiCLI.zip'
            other = zip_dir / 'M9A-win-x86_64-v4.5.3-Lite.zip'
            _create_version_zip(expected, 'v4.5.4')
            _create_version_zip(other, 'v4.5.3')

            result = self.updater.find_cli_zip(
                'M9A-win-x86_64-v*.zip', tmp, gh, 'v4.5.4',
            )
            self.assertEqual(result, str(expected))

    def test_find_cli_zip_ignores_other_platforms(self):
        """缓存查找不匹配其他系统或架构"""
        from modules.github_release_client import GitHubReleaseClient
        gh = GitHubReleaseClient('test/repo', 'release', '', self.logger)

        with tempfile.TemporaryDirectory() as tmp:
            zip_dir = Path(tmp) / 'ZIP'
            zip_dir.mkdir()
            _create_version_zip(zip_dir / 'M9A-linux-x86_64-v4.5.4-PiCLI.zip', 'v4.5.4')
            _create_version_zip(zip_dir / 'M9A-win-arm64-v4.5.4-PiCLI.zip', 'v4.5.4')

            result = self.updater.find_cli_zip(
                'M9A-win-x86_64-v*.zip', tmp, gh, 'v4.5.4',
            )
            self.assertIsNone(result)


class TestRunUpdateConfigVersionSelection(unittest.TestCase):
    """run_update 配置版本选择集成测试"""

    def _create_assistant(self, archive_dir, current_version, target_version, m9a_folder):
        """创建只包含 run_update 所需依赖的助手实例"""
        from M9A_Update_Assistant import M9AUpdateAssistant

        assistant = object.__new__(M9AUpdateAssistant)
        assistant.logger = logging.getLogger("TestRunUpdate")
        assistant.logger.setLevel(logging.CRITICAL)
        assistant.keep_temp = True
        assistant._cleanup_old_logs = Mock()
        assistant.config = SimpleNamespace(
            cli_zip_pattern='M9A-win-x86_64-v*-Lite.zip',
            temp_folder='temp',
            gui_zip_pattern='M9A-win-x86_64-v*-Full.zip',
            m9a_folders=[str(m9a_folder)],
        )
        assistant._github = Mock()
        assistant._github.get_release_by_tag.return_value = {'tag_name': target_version}
        assistant._github.parse_release_keywords.return_value = {'cli': 'Lite', 'gui': 'Full'}
        assistant._collect_outdated_folders = Mock(return_value=[str(m9a_folder)])
        assistant._download_latest_release = Mock(return_value={
            'files': ['C:/cache/M9A-win-x86_64-v3-Lite.zip'],
            'cli_keyword': 'Lite',
            'gui_keyword': 'Full',
            'version': target_version,
            'cli_has_deps': True,
        })
        assistant._download = Mock()
        assistant._zip = Mock()
        assistant._zip.check_lite_zip_has_deps.return_value = True
        assistant._zip.extract_zip_with_progress.return_value = True
        assistant._updater = Mock()
        assistant._updater.archive_dir = Path(archive_dir)
        assistant._updater.find_cli_zip.return_value = None
        assistant._updater.backup_config.return_value = current_version
        assistant._updater.clean_m9a_folder.return_value = True
        assistant._updater.restore_config.return_value = True
        return assistant

    def test_missing_cli_zip_stops_during_release_download(self):
        """找不到目标版本 CLI ZIP 时，在资源解析阶段终止"""
        with tempfile.TemporaryDirectory() as tmp:
            m9a_folder = Path(tmp) / 'M9A'
            m9a_folder.mkdir()
            assistant = self._create_assistant(tmp, 'v4.1.1', 'v4.5.3', m9a_folder)
            assistant._github.get_release_by_tag.return_value = {
                'tag_name': 'v4.5.3',
                'assets': [
                    {'name': 'M9A-win-x86_64-v4.5.3-MXU.zip', 'browser_download_url': 'https://url/mxu'},
                ],
            }
            assistant._github.parse_release_keywords.return_value = {'cli': 'Lite', 'gui_keywords': ['MFAA', 'MXU']}
            assistant._github.find_download_url.side_effect = ['', 'https://url/mxu']
            from M9A_Update_Assistant import M9AUpdateAssistant

            assistant._download_latest_release = M9AUpdateAssistant._download_latest_release.__get__(assistant)
            assistant._updater.find_cli_zip.return_value = None

            result = assistant.run_update('v4.5.3')

            self.assertFalse(result)
            self.assertEqual(assistant._github.find_download_url.call_count, 1)
            assistant._download.download_file_with_progress.assert_not_called()
            self.assertEqual(assistant._updater.find_cli_zip.call_count, 1)

    def test_downgrade_uses_target_backup_when_exists(self):
        """实际降级且目标备份存在时，回写目标版本配置"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'archive'
            m9a_folder = Path(tmp) / 'M9A'
            m9a_folder.mkdir()
            backup_name = M9AUpdater.get_backup_name(str(m9a_folder))
            (archive / 'v3.19.0' / backup_name / 'config').mkdir(parents=True)
            assistant = self._create_assistant(archive, 'v3.20.0', 'v3.19.0', m9a_folder)

            result = assistant.run_update('v3.19.0')

            self.assertTrue(result)
            assistant._updater.restore_config.assert_called_once_with(str(m9a_folder), 'v3.19.0')

    def test_downgrade_uses_lower_backup_when_target_missing(self):
        """实际降级且目标备份不存在时，回写更早版本配置"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'archive'
            m9a_folder = Path(tmp) / 'M9A'
            m9a_folder.mkdir()
            backup_name = M9AUpdater.get_backup_name(str(m9a_folder))
            (archive / 'v3.18.0' / backup_name / 'config').mkdir(parents=True)
            assistant = self._create_assistant(archive, 'v3.20.0', 'v3.19.0', m9a_folder)

            result = assistant.run_update('v3.19.0')

            self.assertTrue(result)
            assistant._updater.restore_config.assert_called_once_with(str(m9a_folder), 'v3.18.0')

    def test_downgrade_fallbacks_to_current_when_no_history(self):
        """实际降级但无历史备份时，回写当前版本备份"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'archive'
            m9a_folder = Path(tmp) / 'M9A'
            m9a_folder.mkdir()
            assistant = self._create_assistant(archive, 'v3.20.0', 'v3.19.0', m9a_folder)

            result = assistant.run_update('v3.19.0')

            self.assertTrue(result)
            assistant._updater.restore_config.assert_called_once_with(str(m9a_folder), 'v3.20.0')

    def test_specified_upgrade_keeps_current_backup(self):
        """指定版本但实际为升级时，不启用历史配置查找"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'archive'
            m9a_folder = Path(tmp) / 'M9A'
            m9a_folder.mkdir()
            backup_name = M9AUpdater.get_backup_name(str(m9a_folder))
            (archive / 'v3.20.0' / backup_name / 'config').mkdir(parents=True)
            assistant = self._create_assistant(archive, 'v3.19.0', 'v3.20.0', m9a_folder)

            result = assistant.run_update('v3.20.0')

            self.assertTrue(result)
            assistant._updater.restore_config.assert_called_once_with(str(m9a_folder), 'v3.19.0')


class TestDownloadLatestReleaseCliOnly(unittest.TestCase):
    """_download_latest_release CLI-only 流程测试"""

    def _create_assistant(self):
        """创建只包含下载流程依赖的助手实例"""
        from M9A_Update_Assistant import M9AUpdateAssistant

        assistant = object.__new__(M9AUpdateAssistant)
        assistant.logger = logging.getLogger("TestDownloadLatestRelease")
        assistant.logger.setLevel(logging.CRITICAL)
        assistant.config = SimpleNamespace(
            cli_zip_pattern='M9A-win-x86_64-v*.zip',
            temp_folder=tempfile.mkdtemp(),
        )
        assistant._github = Mock()
        assistant._download = Mock()
        assistant._zip = Mock()
        assistant._zip.verify_zip_integrity.return_value = True
        return assistant

    def test_download_latest_release_uses_cli_only_pattern(self):
        """只查找并下载 Windows x86_64 CLI ZIP"""
        assistant = self._create_assistant()
        release = {
            'tag_name': 'v4.5.4',
            'assets': [
                {'name': 'M9A-linux-x86_64-v4.5.4-PiCLI.zip', 'browser_download_url': 'https://url/linux'},
                {'name': 'M9A-win-x86_64-v4.5.4-PiCLI.zip', 'browser_download_url': 'https://url/win'},
            ],
        }
        assistant._github.find_download_url.return_value = 'https://url/win/M9A-win-x86_64-v4.5.4-PiCLI.zip'
        assistant._download.download_file_with_progress.return_value = True

        from M9A_Update_Assistant import M9AUpdateAssistant
        assistant._check_or_download_zip = Mock(return_value='C:/cache/M9A-win-x86_64-v4.5.4-PiCLI.zip')

        result = M9AUpdateAssistant._download_latest_release(assistant, release)

        self.assertEqual(result['files'], ['C:/cache/M9A-win-x86_64-v4.5.4-PiCLI.zip'])
        self.assertEqual(result['version'], 'v4.5.4')
        self.assertNotIn('cli_keyword', result)
        self.assertNotIn('gui_keyword', result)
        self.assertNotIn('cli_has_deps', result)
        assistant._github.find_download_url.assert_called_once_with(
            release, 'M9A-win-x86_64-v*.zip',
        )

    def test_missing_cli_zip_returns_error(self):
        """找不到 Windows x86_64 CLI ZIP 时返回缺失错误"""
        assistant = self._create_assistant()
        release = {'tag_name': 'v4.5.4', 'assets': []}
        assistant._github.find_download_url.return_value = None

        from M9A_Update_Assistant import M9AUpdateAssistant
        result = M9AUpdateAssistant._download_latest_release(assistant, release)

        self.assertEqual(result, {'error': 'missing_cli_zip'})
        assistant._download.download_file_with_progress.assert_not_called()

    def test_cached_cli_is_used_without_download_url(self):
        """传入有效缓存时可直接使用缓存 ZIP"""
        assistant = self._create_assistant()
        release = {'tag_name': 'v4.5.4', 'assets': []}
        assistant._github.find_download_url.return_value = None
        assistant._zip.verify_zip_integrity.return_value = True

        from M9A_Update_Assistant import M9AUpdateAssistant
        result = M9AUpdateAssistant._download_latest_release(
            assistant, release, cached_cli='C:/cache/M9A-win-x86_64-v4.5.4-PiCLI.zip',
        )

        self.assertEqual(result['files'], ['C:/cache/M9A-win-x86_64-v4.5.4-PiCLI.zip'])
        assistant._download.download_file_with_progress.assert_not_called()


if __name__ == '__main__':
    unittest.main()
