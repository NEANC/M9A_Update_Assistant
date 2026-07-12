#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config_self_updater import UpdateState
from modules.self_updater import SelfUpdater, _get_existing_retry_count
from M9A_Update_Assistant import _cleanup_update_residue, _is_safe_recovery_runtime_dir


class TestGetExePath(unittest.TestCase):
    """_get_exe_path 测试"""

    def test_returns_path(self):
        path = SelfUpdater._get_exe_path()
        self.assertIsInstance(path, Path)


class TestUpdateRuntimePaths(unittest.TestCase):
    """更新运行时路径 helper 测试"""

    def setUp(self):
        _suppress_logs()
        self.tmpdir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        self.other_cwd = tempfile.mkdtemp()
        self.logger = logging.getLogger("TestUpdateRuntimePaths")
        self.updater = SelfUpdater('', self.tmpdir, self.logger)

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.other_cwd, ignore_errors=True)

    def test_get_update_runtime_dir_uses_production_versioned_strategy(self):
        """runtime_dir 应按生产路径策略使用真实版本号，而不是历史固定目录名。"""
        os.chdir(self.other_cwd)
        exe_path = Path(self.tmpdir) / 'program' / 'M9A_Update_Assistant.exe'

        runtime_dir = self.updater._get_update_runtime_dir(exe_path, 'v9.9.9')
        repeated_runtime_dir = self.updater._get_update_runtime_dir(exe_path, 'v9.9.9')

        self.assertIsInstance(runtime_dir, Path)
        self.assertTrue(runtime_dir.is_absolute())
        self.assertEqual(runtime_dir, Path(self.tmpdir).resolve() / 'v9.9.9')
        self.assertEqual(runtime_dir.name, 'v9.9.9')
        self.assertEqual(runtime_dir, repeated_runtime_dir)
        self.assertNotEqual(runtime_dir.parent, Path.cwd())

    def test_get_update_file_paths_returns_absolute_stable_paths(self):
        """更新文件路径 helper 应返回固定 runtime_dir 下的绝对路径字典。"""
        exe_path = Path(self.tmpdir) / 'program' / 'M9A_Update_Assistant.exe'

        paths = self.updater._get_update_file_paths(exe_path, 'v8.8.8')
        repeated_paths = self.updater._get_update_file_paths(exe_path, 'v8.8.8')

        expected_keys = {
            'runtime_dir', 'helper_ps1', 'update_ps1', 'state_file', 'log_file',
            'new_file', 'backup_file', 'lock_file',
        }
        self.assertTrue(expected_keys.issubset(paths.keys()))
        self.assertEqual(paths['runtime_dir'], self.updater._get_update_runtime_dir(exe_path, 'v8.8.8'))
        self.assertEqual(paths['helper_ps1'], paths['runtime_dir'] / 'M9A_Update_Assistant_Update_Helper.ps1')
        self.assertEqual(paths['update_ps1'], paths['runtime_dir'] / 'M9A_Update_Assistant_Update.ps1')
        self.assertEqual(paths['state_file'], exe_path.parent.resolve() / 'update_state.ini')
        self.assertEqual(paths['log_file'], exe_path.parent.resolve() / 'update.log')
        self.assertEqual(paths['new_file'], paths['runtime_dir'] / 'M9A_Update_Assistant.new.exe')
        self.assertEqual(paths['backup_file'], paths['runtime_dir'] / 'M9A_Update_Assistant.backup.exe')
        self.assertEqual(paths['lock_file'], paths['runtime_dir'] / 'update_started.lock')
        self.assertEqual(paths, repeated_paths)
        for path in paths.values():
            self.assertIsInstance(path, Path)
            self.assertTrue(path.is_absolute())

    def test_get_update_file_paths_defaults_new_file_to_runtime_dir(self):
        """未显式传入 new_version 时应要求调用方提供生产版本号。"""
        exe_path = Path(self.tmpdir) / 'program' / 'M9A_Update_Assistant.exe'

        with self.assertRaises(TypeError):
            self.updater._get_update_file_paths(exe_path)

    def test_build_update_runtime_paths_defaults_to_localappdata_runtime_dir(self):
        """默认 runtime_dir 应位于 LOCALAPPDATA 下，状态和日志仍在程序目录。"""
        program_dir = Path(self.tmpdir) / 'program'
        current_exe = program_dir / 'M9A_Update_Assistant.exe'
        localappdata = Path(self.tmpdir) / 'localappdata'
        updater = SelfUpdater('', '', self.logger)

        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(localappdata)}):
            paths = updater._build_update_runtime_paths(current_exe, 'v1.2.3')

        expected_temp_folder = localappdata / 'M9A_Update_Assistant' / 'SelfUpdate'
        expected_runtime_dir = expected_temp_folder / 'v1.2.3'
        self.assertEqual(paths['program_dir'], program_dir.resolve())
        self.assertEqual(paths['temp_folder'], expected_temp_folder)
        self.assertEqual(paths['runtime_dir'], expected_runtime_dir)
        self.assertEqual(paths['state_file'], program_dir.resolve() / 'update_state.ini')
        self.assertEqual(paths['log_file'], program_dir.resolve() / 'update.log')
        self.assertEqual(paths['new_file'], expected_runtime_dir / 'M9A_Update_Assistant.new.exe')
        self.assertEqual(paths['backup_file'], expected_runtime_dir / 'M9A_Update_Assistant.backup.exe')
        self.assertTrue(paths['runtime_dir'].is_dir())

    def test_build_update_runtime_paths_falls_back_to_program_dir_when_localappdata_mkdir_fails(self):
        """LOCALAPPDATA runtime_dir 创建失败时应回退到程序目录。"""
        program_dir = Path(self.tmpdir) / 'program'
        current_exe = program_dir / 'M9A_Update_Assistant.exe'
        localappdata = Path(self.tmpdir) / 'localappdata'
        blocked_runtime_dir = localappdata / 'M9A_Update_Assistant' / 'SelfUpdate' / 'v1.2.4'
        updater = SelfUpdater('', '', self.logger)
        blocked_runtime_dir.parent.mkdir(parents=True)
        blocked_runtime_dir.write_text('', encoding='utf-8')

        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(localappdata)}):
            paths = updater._build_update_runtime_paths(current_exe, 'v1.2.4')

        expected_runtime_dir = program_dir.resolve() / 'SelfUpdate' / 'v1.2.4'
        self.assertEqual(paths['runtime_dir'], expected_runtime_dir)
        self.assertEqual(paths['temp_folder'], expected_runtime_dir.parent)
        self.assertTrue(expected_runtime_dir.is_dir())

    def test_build_update_runtime_paths_uses_current_exe_stem_for_new_and_backup_files(self):
        """new_file 和 backup_file 应基于当前 exe 文件名 stem。"""
        program_dir = Path(self.tmpdir) / 'program'
        current_exe = program_dir / 'Custom_Assistant.exe'
        updater = SelfUpdater('', '', self.logger)

        with mock.patch.dict(os.environ, {'LOCALAPPDATA': ''}):
            paths = updater._build_update_runtime_paths(current_exe, 'v4.0.0')

        self.assertEqual(paths['new_file'], paths['runtime_dir'] / 'Custom_Assistant.new.exe')
        self.assertEqual(paths['backup_file'], paths['runtime_dir'] / 'Custom_Assistant.backup.exe')

    def test_build_update_runtime_paths_uses_temp_folder_runtime_dir(self):
        """传入 temp_folder 时 runtime_dir 应位于 temp_folder 下。"""
        program_dir = Path(self.tmpdir) / 'program'
        current_exe = program_dir / 'M9A_Update_Assistant.exe'
        temp_folder = Path(self.tmpdir) / 'custom_runtime'
        localappdata = Path(self.tmpdir) / 'localappdata'
        updater = SelfUpdater('', str(temp_folder), self.logger)

        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(localappdata)}):
            paths = updater._build_update_runtime_paths(current_exe, 'v2.0.0')

        expected_runtime_dir = temp_folder / 'v2.0.0'
        self.assertEqual(paths['temp_folder'], temp_folder.resolve())
        self.assertEqual(paths['runtime_dir'], expected_runtime_dir.resolve())
        self.assertEqual(paths['state_file'], program_dir.resolve() / 'update_state.ini')
        self.assertEqual(paths['log_file'], program_dir.resolve() / 'update.log')
        self.assertTrue(paths['runtime_dir'].is_dir())

    def test_build_update_runtime_paths_falls_back_to_program_dir_without_localappdata(self):
        """LOCALAPPDATA 不可用时 runtime_dir 应回退到程序目录下。"""
        program_dir = Path(self.tmpdir) / 'program'
        current_exe = program_dir / 'M9A_Update_Assistant.exe'
        updater = SelfUpdater('', '', self.logger)

        with mock.patch.dict(os.environ, {'LOCALAPPDATA': ''}):
            paths = updater._build_update_runtime_paths(current_exe, 'v3.0.0')

        expected_temp_folder = program_dir.resolve() / 'SelfUpdate'
        expected_runtime_dir = expected_temp_folder / 'v3.0.0'
        self.assertEqual(paths['temp_folder'], expected_temp_folder)
        self.assertEqual(paths['runtime_dir'], expected_runtime_dir)
        self.assertEqual(paths['state_file'], program_dir.resolve() / 'update_state.ini')
        self.assertEqual(paths['log_file'], program_dir.resolve() / 'update.log')
        self.assertEqual(paths['helper_ps1'], expected_runtime_dir / 'M9A_Update_Assistant_Update_Helper.ps1')
        self.assertEqual(paths['update_ps1'], expected_runtime_dir / 'M9A_Update_Assistant_Update.ps1')
        self.assertEqual(paths['lock_file'], expected_runtime_dir / 'update_started.lock')
        self.assertEqual(paths['new_file'], expected_runtime_dir / 'M9A_Update_Assistant.new.exe')
        self.assertEqual(paths['backup_file'], expected_runtime_dir / 'M9A_Update_Assistant.backup.exe')
        self.assertTrue(paths['runtime_dir'].is_dir())


class TestDetectPackageType(unittest.TestCase):
    """detect_package_type 静态方法测试"""

    def test_source_run(self):
        """源码运行检测 — 在测试框架下可能显示为 bundled"""
        is_bundled, pkg_type = SelfUpdater.detect_package_type()
        # unittest runner 的 argv[0] 可能不是 .py，所以 is_bundled 可能为 True
        # 但 pkg_type 应始终为 'Nuitka'（未检测到 PyInstaller）
        self.assertEqual(pkg_type, 'Nuitka')


class TestVersionToTuple(unittest.TestCase):
    """version_to_tuple 静态方法测试"""

    def test_semver(self):
        self.assertEqual(SelfUpdater.version_to_tuple('v3.28.3'), (3, 28, 3))
        self.assertEqual(SelfUpdater.version_to_tuple('v1.0.0'), (1, 0, 0))
        self.assertEqual(SelfUpdater.version_to_tuple('v0.0.1'), (0, 0, 1))

    def test_semver_no_v(self):
        self.assertEqual(SelfUpdater.version_to_tuple('3.28.3'), (3, 28, 3))

    def test_ordering(self):
        """版本比较"""
        self.assertGreater(
            SelfUpdater.version_to_tuple('v3.28.3'),
            SelfUpdater.version_to_tuple('v3.27.0'),
        )
        self.assertGreater(
            SelfUpdater.version_to_tuple('v3.28.3'),
            SelfUpdater.version_to_tuple('v3.28.2'),
        )
        self.assertGreater(
            SelfUpdater.version_to_tuple('v4.0.0'),
            SelfUpdater.version_to_tuple('v3.99.99'),
        )

    def test_equal(self):
        self.assertEqual(
            SelfUpdater.version_to_tuple('v3.28.3'),
            SelfUpdater.version_to_tuple('v3.28.3'),
        )

    def test_invalid_version(self):
        """非法版本号返回空元组"""
        self.assertEqual(SelfUpdater.version_to_tuple(''), ())
        self.assertEqual(SelfUpdater.version_to_tuple('invalid'), ())
        self.assertEqual(SelfUpdater.version_to_tuple('v'), ())

    def test_prerelease_parsed_as_core(self):
        """预发布版本提取核心三数字"""
        self.assertEqual(SelfUpdater.version_to_tuple('v1.11.0-alpha'), (1, 11, 0))
        self.assertEqual(SelfUpdater.version_to_tuple('v1.11.0-beta2'), (1, 11, 0))
        self.assertEqual(SelfUpdater.version_to_tuple('v1.11.0-rc3'), (1, 11, 0))
        self.assertEqual(SelfUpdater.version_to_tuple('v1.10.1-9-build.gb6da5ee'), (1, 10, 1))

    def test_is_prerelease(self):
        """预发布版本检测"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._is_prerelease('v1.11.0-alpha'))
        self.assertTrue(su._is_prerelease('v1.11.0-Alpha'))
        self.assertTrue(su._is_prerelease('v1.11.0-ALPHA'))
        self.assertTrue(su._is_prerelease('v1.11.0-beta2'))
        self.assertTrue(su._is_prerelease('v1.11.0-Beta.1'))
        self.assertTrue(su._is_prerelease('v1.11.0-rc'))
        self.assertTrue(su._is_prerelease('v1.11.0-RC1'))
        self.assertTrue(su._is_prerelease('v1.11.0-Rc-1'))
        self.assertFalse(su._is_prerelease('v1.11.0'))
        self.assertFalse(su._is_prerelease('v1.10.1-9-build.gb6da5ee'))

    def test_version_newer_than_stable_upgrade(self):
        """正式版之间的升级"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.10.0', 'v1.11.0'))
        self.assertFalse(su._version_newer_than('v1.11.0', 'v1.10.0'))

    def test_version_newer_than_prerelease_to_stable(self):
        """预发布 → 正式版 视为升级"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.11.0-alpha', 'v1.11.0'))
        self.assertTrue(su._version_newer_than('v1.11.0-beta', 'v1.11.0'))
        self.assertTrue(su._version_newer_than('v1.11.0-rc', 'v1.11.0'))

    def test_version_newer_than_alpha_to_beta(self):
        """alpha → beta → rc 递进"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.11.0-alpha', 'v1.11.0-beta'))
        self.assertTrue(su._version_newer_than('v1.11.0-beta', 'v1.11.0-rc'))
        self.assertFalse(su._version_newer_than('v1.11.0-rc', 'v1.11.0-alpha'))

    def test_version_newer_than_stable_to_prerelease(self):
        """正式版 → 同数字版本的预发布 不视为升级"""
        su = SelfUpdater('', '', None)
        self.assertFalse(su._version_newer_than('v1.11.0', 'v1.11.0-rc'))
        self.assertFalse(su._version_newer_than('v1.12.0', 'v1.12.0-beta'))

    def test_version_newer_than_build_format(self):
        """无标签构建格式比较"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.10.1-9-build.gb6da5ee', 'v1.11.0'))
        self.assertFalse(su._version_newer_than('v1.11.0', 'v1.10.1-9-build.gb6da5ee'))

    def test_version_newer_than_prerelease_without_dot(self):
        """无点号预发布数字比较：beta → beta1 → beta2"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.13.0-beta', 'v1.13.0-beta1'))
        self.assertTrue(su._version_newer_than('v1.13.0-beta1', 'v1.13.0-beta2'))
        self.assertFalse(su._version_newer_than('v1.13.0-beta2', 'v1.13.0-beta1'))

    def test_version_newer_than_prerelease_with_dot(self):
        """带点号预发布数字比较：beta.1 → beta.2"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.13.0-beta.1', 'v1.13.0-beta.2'))
        self.assertFalse(su._version_newer_than('v1.13.0-beta.2', 'v1.13.0-beta.1'))

    def test_version_newer_than_case_insensitive(self):
        """大小写不敏感预发布比较：Alpha → Beta → RC"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.13.0-Alpha', 'v1.13.0-Beta'))
        self.assertTrue(su._version_newer_than('v1.13.0-BETA', 'v1.13.0-RC'))
        self.assertTrue(su._version_newer_than('v1.13.0-RC1', 'v1.13.0-RC2'))
        self.assertTrue(su._version_newer_than('v1.13.0-Alpha', 'v1.13.0-alpha1'))
        # Alpha 和 Alpha.1 数字相同都是1，权重相等不视作升级
        self.assertFalse(su._version_newer_than('v1.13.0-Alpha.1', 'v1.13.0-alpha1'))
        self.assertFalse(su._version_newer_than('v1.13.0-alpha1', 'v1.13.0-Alpha.1'))

    def test_version_newer_than_dash_suffix(self):
        """-N 后缀预发布：Alpha-1 → Alpha-2"""
        su = SelfUpdater('', '', None)
        self.assertTrue(su._version_newer_than('v1.13.0-Alpha-1', 'v1.13.0-Alpha-2'))
        self.assertTrue(su._version_newer_than('v1.13.0-alpha-2', 'v1.13.0-beta-3'))
        self.assertTrue(su._version_newer_than('v1.13.0-Beta-2', 'v1.13.0-Beta.3'))
        self.assertTrue(su._version_newer_than('v1.13.0-rc-1', 'v1.13.0-Rc-3'))
        # -N 和 .N 同数字权重相等
        self.assertFalse(su._version_newer_than('v1.13.0-Alpha-1', 'v1.13.0-Alpha.1'))
        self.assertFalse(su._version_newer_than('v1.13.0-alpha.1', 'v1.13.0-Alpha-1'))

    def test_is_build_tag(self):
        """构建标签检测"""
        self.assertTrue(SelfUpdater._is_build_tag('v0.0.1-build.gb6da5ee'))
        self.assertTrue(SelfUpdater._is_build_tag('v1.10.1-9-build.gb6da5ee'))
        self.assertFalse(SelfUpdater._is_build_tag('v1.10.0'))
        self.assertFalse(SelfUpdater._is_build_tag('v1.11.0-alpha'))
        self.assertFalse(SelfUpdater._is_build_tag('v1.11.0-beta'))
        self.assertFalse(SelfUpdater._is_build_tag('v1.11.0-rc'))


class TestCheckSelfUpdate(unittest.TestCase):
    """check_self_update 测试"""

    def setUp(self):
        _suppress_logs()
        self.tmpdir = tempfile.mkdtemp()
        self.original_argv0 = sys.argv[0]
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")

        self.logger = logging.getLogger("TestSelfUpdate")
        self.logger.setLevel(logging.CRITICAL)
        self.su = SelfUpdater('', self.tmpdir, self.logger)

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_source_mode_skips(self):
        """源码模式下跳过"""
        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'release', '', self.logger)
        dm = DownloadManager('', '/tmp', self.logger)
        zm = ZipManager(self.logger)

        result = self.su.check_self_update('v1.0.0', gh, dm, zm)
        # 源码运行下始终跳过
        self.assertFalse(result)

    @mock.patch('modules.self_updater.requests.get')
    @mock.patch('modules.self_updater.SelfUpdater.detect_package_type')
    def test_build_tag_skips(self, mock_detect, mock_get):
        """构建标签版本检测到新版本但仍跳过更新"""
        mock_detect.return_value = (True, 'Nuitka')

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            'tag_name': 'v1.0.0',
            'assets': [],
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'latest', '', self.logger)
        dm = DownloadManager('', '/tmp', self.logger)
        zm = ZipManager(self.logger)

        result = self.su.check_self_update('v0.0.1-build.gb6da5ee', gh, dm, zm)
        self.assertFalse(result)

    @mock.patch('modules.self_updater.requests.get')
    @mock.patch('modules.self_updater.SelfUpdater.detect_package_type')
    def test_force_skips_build_tag_check(self, mock_detect, mock_get):
        """force=True 跳过 Build 版本检查并继续强制更新流程"""
        mock_detect.return_value = (True, 'Nuitka')
        mock_response = mock.MagicMock()
        # preview 通道用 /releases API，返回数组
        mock_response.json.return_value = [{
            'tag_name': 'v2.0.0',
            'draft': False,
            'assets': [{
                'name': 'M9A_Update_Assistant-Nuitka-v2.0.0.exe',
                'browser_download_url': 'https://url/exe',
                'digest': 'sha256:aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899',
            }],
        }]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'latest', '', self.logger)
        dm = DownloadManager('', self.tmpdir, self.logger)
        zm = ZipManager(self.logger)

        with mock.patch.object(self.su, '_check_system_environment', return_value=True):
            with mock.patch.object(self.su, '_is_build_tag', wraps=self.su._is_build_tag) as mock_is_build:
                with mock.patch.object(self.su, '_version_newer_than', return_value=True):
                    with mock.patch.object(self.su, '_fetch_current_release_sha256', return_value=''):
                        with mock.patch.object(dm, 'download_file_with_progress', return_value=True):
                            with mock.patch.object(zm, 'verify_file_sha256', return_value=True):
                                with mock.patch.object(self.su, '_replace_executable'):
                                    result = self.su.check_self_update(
                                        'v0.0.1-build.gb6da5ee', gh, dm, zm, force=True,
                                    )
                                    self.assertTrue(result)
                                    # force=True bypasses _is_build_tag check entirely
                                    mock_is_build.assert_not_called()

    @mock.patch('modules.self_updater.requests.get')
    @mock.patch('modules.self_updater.SelfUpdater.detect_package_type')
    def test_force_skips_version_comparison(self, mock_detect, mock_get):
        """force=True 跳过版本比对，同版本也会继续"""
        mock_detect.return_value = (True, 'Nuitka')
        mock_response = mock.MagicMock()
        mock_response.json.return_value = [{
            'tag_name': 'v1.0.0',
            'draft': False,
            'assets': [{
                'name': 'M9A_Update_Assistant-Nuitka-v1.0.0.exe',
                'browser_download_url': 'https://url/exe',
                'digest': 'sha256:aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899',
            }],
        }]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'latest', '', self.logger)
        dm = DownloadManager('', self.tmpdir, self.logger)
        zm = ZipManager(self.logger)

        with mock.patch.object(self.su, '_check_system_environment', return_value=True):
            with mock.patch.object(self.su, '_version_newer_than', wraps=self.su._version_newer_than) as mock_vnt:
                with mock.patch.object(self.su, '_fetch_current_release_sha256', return_value=''):
                    with mock.patch.object(dm, 'download_file_with_progress', return_value=True):
                        with mock.patch.object(zm, 'verify_file_sha256', return_value=True):
                            with mock.patch.object(self.su, '_replace_executable'):
                                result = self.su.check_self_update(
                                    'v1.0.0', gh, dm, zm, force=True,
                                )
                                self.assertTrue(result)
                                # force=True bypasses _version_newer_than
                                mock_vnt.assert_not_called()

    def test_force_without_special_args_defaults_false(self):
        """不传 force 参数时默认为 False"""
        # 直接从函数签名验证
        import inspect
        sig = inspect.signature(self.su.check_self_update)
        self.assertEqual(sig.parameters['force'].default, False)

    @mock.patch('modules.self_updater.requests.get')
    @mock.patch('modules.self_updater.SelfUpdater.detect_package_type')
    def test_already_latest(self, mock_detect, mock_get):
        """已是最新版本"""
        mock_detect.return_value = (True, 'Nuitka')

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            'tag_name': 'v1.0.0',
            'assets': [],
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'latest', '', self.logger)
        dm = DownloadManager('', '/tmp', self.logger)
        zm = ZipManager(self.logger)

        result = self.su.check_self_update('v2.0.0', gh, dm, zm)
        self.assertFalse(result)

    @mock.patch('modules.self_updater.requests.get')
    @mock.patch('modules.self_updater.SelfUpdater.detect_package_type')
    def test_new_version_no_exe(self, mock_detect, mock_get):
        """有新版本但无匹配 exe"""
        mock_detect.return_value = (True, 'Nuitka')

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            'tag_name': 'v2.0.0',
            'assets': [],
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'latest', '', self.logger)
        dm = DownloadManager('', '/tmp', self.logger)
        zm = ZipManager(self.logger)

        result = self.su.check_self_update('v1.0.0', gh, dm, zm)
        self.assertFalse(result)

    @mock.patch('modules.self_updater.requests.get')
    @mock.patch('modules.self_updater.SelfUpdater.detect_package_type')
    def test_failed_disabled_skips(self, mock_detect, mock_get):
        """状态为 failed_disabled 时跳过更新"""
        mock_detect.return_value = (True, 'Nuitka')

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            'tag_name': 'v2.0.0',
            'assets': [{
                'name': 'M9A_Update_Assistant-Nuitka-v2.0.0.exe',
                'browser_download_url': 'https://url/exe',
            }],
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        pre_state = UpdateState()
        pre_state["state"] = "failed_disabled"
        pre_state["new_version"] = "v2.0.0"
        pre_state.save()

        from modules.github_release_client import GitHubReleaseClient
        from modules.download_manager import DownloadManager
        from modules.zip_manager import ZipManager

        gh = GitHubReleaseClient('test/repo', 'latest', '', self.logger)
        dm = DownloadManager('', '/tmp', self.logger)
        zm = ZipManager(self.logger)

        result = self.su.check_self_update('v1.0.0', gh, dm, zm)
        self.assertFalse(result)
        # 验证没有实际调用 GitHub（因为被 failed_disabled 提前拦截）
        # mock_get 仍可能被调用（请求先发），但返回结果应为 False
        # 关键是状态文件未被更新为 downloaded_verified 等
        post_state = UpdateState.load()
        self.assertEqual(post_state["state"], "failed_disabled")


class TestGetExistingRetryCount(unittest.TestCase):
    """_get_existing_retry_count 测试"""

    def setUp(self):
        _suppress_logs()
        self.tmpdir = tempfile.mkdtemp()
        self.original_argv0 = sys.argv[0]
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_state_file_returns_zero(self):
        """无状态文件时返回 '0'"""
        self.assertEqual(_get_existing_retry_count(), "0")

    def test_existing_retry_count_preserved(self):
        """已有 retry_count 时返回其值"""
        state = UpdateState()
        state.set("Retry", "retry_count", "2")
        state.save()

        self.assertEqual(_get_existing_retry_count(), "2")

    def test_no_retry_count_defaults_zero(self):
        """状态文件存在但无 retry_count 时返回 '0'"""
        state = UpdateState()
        state["state"] = "rollback_done"
        state.save()

        self.assertEqual(_get_existing_retry_count(), "0")


class TestRollback(unittest.TestCase):
    """rollback 静态方法测试"""

    def setUp(self):
        _suppress_logs()
        self.tmpdir = tempfile.mkdtemp()
        self.original_argv0 = sys.argv[0]
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_state_file(self):
        """无状态文件时返回 False，不抛异常"""
        result = SelfUpdater.rollback()
        self.assertFalse(result)

    def test_no_backup_file(self):
        """状态文件存在但备份文件不存在"""
        state = UpdateState()
        state["target"] = os.path.join(self.tmpdir, "app.exe")
        state["backup_file"] = os.path.join(self.tmpdir, "nonexistent_backup.exe")
        state.save()

        result = SelfUpdater.rollback()
        self.assertFalse(result)

    def test_successful_rollback(self):
        """成功从备份恢复"""
        target = os.path.join(self.tmpdir, "app.exe")
        backup = os.path.join(self.tmpdir, "app.backup.exe")

        # 创建备份文件
        Path(backup).write_text("old version content")

        state = UpdateState()
        state["target"] = target
        state["backup_file"] = backup
        state.save()

        result = SelfUpdater.rollback()
        self.assertTrue(result)
        self.assertTrue(os.path.exists(target))
        with open(target, 'r') as f:
            self.assertEqual(f.read(), "old version content")
        self.assertFalse(os.path.exists(backup))

    def test_rollback_uses_backup_file_in_runtime_dir(self):
        """回滚应从 runtime_dir 中记录的备份文件恢复目标文件。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime" / "v1.2.3"
        target = program_dir / "app.exe"
        backup = runtime_dir / "app.backup.exe"
        logger = logging.getLogger("TestRollbackRuntimeDir")
        program_dir.mkdir(parents=True)
        runtime_dir.mkdir(parents=True)
        target.write_text("new version content", encoding="utf-8")
        backup.write_text("old version content", encoding="utf-8")

        state = UpdateState()
        state["state"] = "failed_disabled"
        state["target"] = str(target)
        state["runtime_dir"] = str(runtime_dir)
        state["backup_file"] = str(backup)
        state.save()

        result = SelfUpdater.rollback(logger)

        self.assertTrue(result)
        self.assertEqual(target.read_text(encoding="utf-8"), "old version content")
        self.assertFalse(backup.exists())


class TestReadmeSelfUpdateDocumentation(unittest.TestCase):
    """README 自更新说明测试"""

    def test_readme_documents_self_update_runtime_layout(self):
        """README 应记录自更新运行时文件布局。"""
        readme_path = Path(__file__).resolve().parents[1] / "README.md"
        content = readme_path.read_text(encoding="utf-8")

        expected_fragments = (
            "自更新运行时文件布局",
            "程序目录根部的自更新运行时文件只保留",
            "update_state.ini",
            "update.log",
            "%LOCALAPPDATA%\\M9A_Update_Assistant\\SelfUpdate\\{version}",
            "temp_folder",
            "program_dir\\SelfUpdate\\{version}",
            "runtime_dir/helper_ps1/update_ps1/lock_file/new_file/backup_file",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, content)


class TestMatchAsset(unittest.TestCase):
    """_match_asset 测试"""

    def setUp(self):
        _suppress_logs()
        self.su = SelfUpdater('', '/tmp', logging.getLogger("TestSelfUpdate"))

    def _make_release(self, assets):
        return {'assets': assets}

    def test_match_nuitka_primary(self):
        """首选 Nuitka 版本"""
        release = self._make_release([
            {
                'name': 'M9A_Update_Assistant-Nuitka-v1.11.0.exe',
                'browser_download_url': 'https://url/nuitka',
            },
            {
                'name': 'M9A_Update_Assistant-PyInstaller-v1.11.0.exe',
                'browser_download_url': 'https://url/pyinstaller',
            },
        ])
        url, name = self.su._match_asset(release, 'Nuitka')
        self.assertEqual(url, 'https://url/nuitka')
        self.assertIn('Nuitka', name)

    def test_fallback_to_pyinstaller(self):
        """Nuitka 不存在时回退到 PyInstaller"""
        release = self._make_release([
            {
                'name': 'M9A_Update_Assistant-PyInstaller-v1.11.0.exe',
                'browser_download_url': 'https://url/pyinstaller',
            },
        ])
        url, name = self.su._match_asset(release, 'Nuitka')
        self.assertEqual(url, 'https://url/pyinstaller')
        self.assertIn('PyInstaller', name)

    def test_no_matching_asset(self):
        """无匹配 asset"""
        release = self._make_release([
            {'name': 'other-file.txt', 'browser_download_url': 'https://url/other'},
        ])
        url, name = self.su._match_asset(release, 'Nuitka')
        self.assertEqual(url, '')
        self.assertEqual(name, '')

    def test_rejects_invalid_naming(self):
        """拒绝不符合命名规范的 exe"""
        release = self._make_release([
            {
                'name': 'M9A_Update_Assistant-v1.11.0.exe',
                'browser_download_url': 'https://url/bad',
            },
        ])
        url, name = self.su._match_asset(release, 'Nuitka')
        self.assertEqual(url, '')


class TestSelfUpdateVerify(unittest.TestCase):
    """self_update_verify 静态方法测试"""

    def setUp(self):
        _suppress_logs()
        self.tmpdir = tempfile.mkdtemp()
        self.original_argv0 = sys.argv[0]
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_state_file(self):
        """无状态文件返回 1"""
        code = SelfUpdater.self_update_verify()
        self.assertEqual(code, 1)

    @mock.patch('modules.self_updater.SelfUpdater._get_exe_path')
    def test_sha256_mismatch(self, mock_exe_path):
        """SHA256 不匹配返回 2"""
        exe_path = os.path.join(self.tmpdir, "test_app.exe")
        Path(exe_path).write_text("binary content")
        mock_exe_path.return_value = Path(exe_path)

        state = UpdateState()
        state["new_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
        state["new_version"] = "v9.9.9"
        state.save()

        code = SelfUpdater.self_update_verify()
        self.assertEqual(code, 2)

    @mock.patch('modules.self_updater.SelfUpdater._get_exe_path')
    def test_version_mismatch(self, mock_exe_path):
        """版本号不匹配返回 3"""
        from modules.zip_manager import ZipManager

        exe_path = os.path.join(self.tmpdir, "test_app.exe")
        Path(exe_path).write_text("binary content")
        mock_exe_path.return_value = Path(exe_path)

        actual_sha = ZipManager.calculate_sha256(exe_path)

        state = UpdateState()
        state["new_sha256"] = actual_sha
        state["new_version"] = "v9.9.9"
        state.save()

        code = SelfUpdater.self_update_verify()
        self.assertEqual(code, 3)

    @mock.patch('modules.self_updater.SelfUpdater._get_exe_path')
    def test_passes_with_valid_state(self, mock_exe_path):
        """SHA256 和版本号均匹配且核心模块可导入 → 返回 0"""
        from modules.zip_manager import ZipManager
        from modules.version import VERSION

        exe_path = os.path.join(self.tmpdir, "test_app.exe")
        Path(exe_path).write_text("binary content")
        mock_exe_path.return_value = Path(exe_path)

        actual_sha = ZipManager.calculate_sha256(exe_path)

        state = UpdateState()
        state["new_sha256"] = actual_sha
        state["new_version"] = VERSION
        state.save()

        code = SelfUpdater.self_update_verify()
        self.assertEqual(code, 0)


class TestCleanupUpdateResidue(unittest.TestCase):
    """_cleanup_update_residue 测试"""

    def setUp(self):
        _suppress_logs()
        self.logger = logging.getLogger("TestCleanup")
        self.tmpdir = tempfile.mkdtemp()
        self.original_argv0 = sys.argv[0]
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_state_file(self):
        """无状态文件时静默返回"""
        _cleanup_update_residue(self.logger)

    def test_rollback_done_cleans_state(self):
        """rollback_done 状态 → 清理状态文件"""
        state = UpdateState()
        state["state"] = "rollback_done"
        state.save()

        _cleanup_update_residue(self.logger)
        self.assertIsNone(UpdateState.load())

    def test_failed_disabled_keeps_state(self):
        """failed_disabled 状态 → 不删除状态文件（供后续跳过使用）"""
        state = UpdateState()
        state["state"] = "failed_disabled"
        state["new_version"] = "v2.0.0"
        state.save()

        _cleanup_update_residue(self.logger)
        loaded = UpdateState.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["state"], "failed_disabled")

    @mock.patch('M9A_Update_Assistant._clean_self_update_cache')
    def test_entry_cleanup_verified_calls_cache_cleanup_when_not_delete_false(self, mock_clean_cache):
        """入口清理 verified 状态时 not_delete=False 应清理自更新缓存。"""
        state = UpdateState()
        state["state"] = "verified"
        state.save()

        _cleanup_update_residue(self.logger, not_delete=False)

        mock_clean_cache.assert_called_once_with(self.logger)

    @mock.patch('M9A_Update_Assistant._clean_self_update_cache')
    def test_entry_cleanup_verified_skips_cache_cleanup_when_not_delete_true(self, mock_clean_cache):
        """入口清理 verified 状态时 not_delete=True 应跳过自更新缓存清理。"""
        state = UpdateState()
        state["state"] = "verified"
        state.save()

        _cleanup_update_residue(self.logger, not_delete=True)

        mock_clean_cache.assert_not_called()

    def test_cleanup_update_residue_removes_recorded_runtime_files(self):
        """verified 状态 → 按状态文件记录路径清理运行时残留。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        program_dir.mkdir()
        runtime_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        helper_ps1 = runtime_dir / "helper.ps1"
        update_ps1 = runtime_dir / "update.ps1"
        lock_file = runtime_dir / "update.lock"
        new_file = runtime_dir / "new_file.exe"
        backup_file = runtime_dir / "backup_file.exe"
        foreign_file = program_dir / "foreign.exe"
        update_log = program_dir / "update.log"
        sys.argv[0] = str(target)

        for path in [target, helper_ps1, update_ps1, lock_file, new_file, backup_file, foreign_file, update_log]:
            path.write_text(path.name, encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state["new_file"] = str(new_file)
        state["backup_file"] = str(backup_file)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "helper_ps1", str(helper_ps1))
        state.set("Files", "update_ps1", str(update_ps1))
        state.set("Files", "lock_file", str(lock_file))
        state.save()

        state_file = program_dir / UpdateState.STATE_FILE_NAME
        SelfUpdater._cleanup_update_residue(self.logger)

        self.assertTrue(target.exists())
        self.assertFalse(helper_ps1.exists())
        self.assertFalse(update_ps1.exists())
        self.assertFalse(lock_file.exists())
        self.assertFalse(new_file.exists())
        self.assertFalse(backup_file.exists())
        self.assertFalse(runtime_dir.exists())
        self.assertFalse(state_file.exists())
        self.assertFalse(update_log.exists())
        self.assertTrue(foreign_file.exists())

    def test_cleanup_update_residue_removes_legacy_program_dir_residue(self):
        """旧版 verified 状态 → 清理程序目录固定名称残留。"""
        program_dir = Path(self.tmpdir) / "program"
        program_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        new_file = program_dir / "M9A_Update_Assistant.new.exe"
        backup_file = program_dir / "M9A_Update_Assistant.backup.exe"
        legacy_residue = [
            program_dir / "M9A_Update_Assistant_Update_Helper.ps1",
            program_dir / "M9A_Update_Assistant_Update.ps1",
            program_dir / "update_started.lock",
            program_dir / "M9A_Update_Assistant.old.exe",
            new_file,
            backup_file,
        ]
        sys.argv[0] = str(target)

        target.write_text("target", encoding='utf-8')
        for path in legacy_residue:
            path.write_text(path.name, encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state["new_file"] = str(new_file)
        state["backup_file"] = str(backup_file)
        state.save()

        state_file = program_dir / UpdateState.STATE_FILE_NAME
        SelfUpdater._cleanup_update_residue(self.logger)

        self.assertTrue(target.exists())
        for path in legacy_residue:
            self.assertFalse(path.exists(), f"旧版残留未删除: {path}")
        self.assertFalse(state_file.exists())

    def test_cleanup_update_residue_legacy_state_ignores_recorded_external_paths(self):
        """旧版 verified 状态缺少 runtime_dir 时只清理程序目录固定名称残留。"""
        program_dir = Path(self.tmpdir) / "program"
        outside_dir = Path(self.tmpdir) / "outside"
        program_dir.mkdir()
        outside_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        legacy_residue = [
            program_dir / "M9A_Update_Assistant_Update_Helper.ps1",
            program_dir / "M9A_Update_Assistant_Update.ps1",
            program_dir / "update_started.lock",
            program_dir / "M9A_Update_Assistant.old.exe",
            program_dir / "M9A_Update_Assistant.new.exe",
            program_dir / "M9A_Update_Assistant.backup.exe",
        ]
        external_residue = [
            outside_dir / "polluted-helper.ps1",
            outside_dir / "polluted-update.ps1",
            outside_dir / "polluted.lock",
            outside_dir / "polluted-new.exe",
            outside_dir / "polluted-backup.exe",
        ]
        sys.argv[0] = str(target)

        target.write_text("target", encoding='utf-8')
        for path in legacy_residue + external_residue:
            path.write_text(path.name, encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state.set("Files", "runtime_dir", "")
        state.set("Files", "helper_ps1", str(external_residue[0]))
        state.set("Files", "update_ps1", str(external_residue[1]))
        state.set("Files", "lock_file", str(external_residue[2]))
        state["new_file"] = str(external_residue[3])
        state["backup_file"] = str(external_residue[4])
        state.save()

        state_file = program_dir / UpdateState.STATE_FILE_NAME
        SelfUpdater._cleanup_update_residue(self.logger)

        for path in legacy_residue:
            self.assertFalse(path.exists(), f"旧版固定残留未删除: {path}")
        for path in external_residue:
            self.assertTrue(path.exists(), f"不应删除状态文件记录的外部路径: {path}")
        self.assertFalse(state_file.exists())

    def test_cleanup_update_residue_skips_recorded_files_outside_runtime_dir(self):
        """verified 清理应跳过 runtime_dir 外的状态记录残留文件。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        outside_dir = Path(self.tmpdir) / "outside"
        program_dir.mkdir()
        runtime_dir.mkdir()
        outside_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        helper_ps1 = runtime_dir / "helper.ps1"
        outside_file = outside_dir / "outside.exe"
        sys.argv[0] = str(target)

        for path in [target, helper_ps1, outside_file]:
            path.write_text(path.name, encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state["new_file"] = str(outside_file)
        state["backup_file"] = str(outside_file)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "helper_ps1", str(helper_ps1))
        state.set("Files", "update_ps1", str(outside_file))
        state.set("Files", "lock_file", str(outside_file))
        state.save()

        with self.assertLogs("TestCleanup", level="WARNING") as captured:
            SelfUpdater._cleanup_update_residue(self.logger)

        self.assertFalse(helper_ps1.exists())
        self.assertTrue(outside_file.exists())
        self.assertTrue(any("跳过越界残留文件" in message for message in captured.output))

    def test_cleanup_update_residue_skips_polluted_empty_runtime_dir_without_runtime_files(self):
        """verified 清理不应删除无有效运行时文件支撑的外部空 runtime_dir。"""
        program_dir = Path(self.tmpdir) / "program"
        external_runtime_dir = Path(self.tmpdir) / "external-runtime"
        outside_dir = Path(self.tmpdir) / "outside"
        program_dir.mkdir()
        external_runtime_dir.mkdir()
        outside_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        outside_file = outside_dir / "outside.exe"
        sys.argv[0] = str(target)
        target.write_text("target", encoding='utf-8')
        outside_file.write_text("outside", encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state.set("Files", "runtime_dir", str(external_runtime_dir))
        state.set("Files", "helper_ps1", "")
        state.set("Files", "update_ps1", str(outside_file))
        state.set("Files", "lock_file", "")
        state["new_file"] = ""
        state["backup_file"] = str(outside_file)
        state.save()

        SelfUpdater._cleanup_update_residue(self.logger)

        self.assertTrue(external_runtime_dir.exists())
        self.assertTrue(outside_file.exists())

    def test_cleanup_update_residue_removes_recorded_log_file(self):
        """verified 清理应删除程序目录中的状态记录 update.log。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        program_dir.mkdir()
        runtime_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        log_file = program_dir / "update.log"
        sys.argv[0] = str(target)

        for path in [target, log_file]:
            path.write_text(path.name, encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "log_file", str(log_file))
        state.save()

        SelfUpdater._cleanup_update_residue(self.logger)

        self.assertFalse(log_file.exists())

    def test_cleanup_update_residue_skips_recorded_log_file_outside_program_dir(self):
        """verified 清理应跳过状态中指向程序目录外的 log_file。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        outside_dir = Path(self.tmpdir) / "logs"
        program_dir.mkdir()
        runtime_dir.mkdir()
        outside_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        outside_log = outside_dir / "update.log"
        allowed_log = program_dir / "update.log"
        sys.argv[0] = str(target)

        for path in [target, outside_log, allowed_log]:
            path.write_text(path.name, encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "log_file", str(outside_log))
        state.save()

        with self.assertLogs("TestCleanup", level="WARNING") as captured:
            SelfUpdater._cleanup_update_residue(self.logger)

        self.assertTrue(outside_log.exists())
        self.assertTrue(allowed_log.exists())
        self.assertTrue(any("跳过越界日志文件" in message for message in captured.output))

    def test_cleanup_update_residue_warns_when_delete_fails(self):
        """残留文件删除失败时应至少记录 warning。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        program_dir.mkdir()
        runtime_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        helper_ps1 = runtime_dir / "helper.ps1"
        sys.argv[0] = str(target)
        target.write_text("target", encoding='utf-8')
        helper_ps1.write_text("helper", encoding='utf-8')

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = str(target)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "helper_ps1", str(helper_ps1))
        state.save()

        with mock.patch.object(Path, 'unlink', side_effect=OSError("locked")):
            with self.assertLogs("TestCleanup", level="WARNING") as captured:
                SelfUpdater._cleanup_update_residue(self.logger)

        self.assertTrue(any("删除残留文件失败" in message for message in captured.output))

    def test_cleanup_update_residue_keeps_runtime_dir_when_not_verified(self):
        """非 verified 状态 → 不清理 runtime_dir、backup、状态文件。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        program_dir.mkdir()
        runtime_dir.mkdir()
        target = program_dir / "M9A_Update_Assistant.exe"
        backup_file = runtime_dir / "backup_file.exe"
        sys.argv[0] = str(target)
        target.write_text("target", encoding='utf-8')
        backup_file.write_text("backup", encoding='utf-8')

        state = UpdateState()
        state["state"] = "replacing"
        state["target"] = str(target)
        state["backup_file"] = str(backup_file)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.save()

        state_file = program_dir / UpdateState.STATE_FILE_NAME
        SelfUpdater._cleanup_update_residue(self.logger)

        self.assertTrue(runtime_dir.exists())
        self.assertTrue(backup_file.exists())
        self.assertTrue(state_file.exists())

    def test_interrupted_recovering_restores_and_keeps_cleanup_basis(self):
        """恢复备份后应清理 runtime_dir，状态转入 rollback_done 供后续明确处理。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        target = program_dir / "app.exe"
        backup_file = runtime_dir / "app.backup.exe"
        helper_ps1 = runtime_dir / "helper.ps1"
        program_dir.mkdir()
        runtime_dir.mkdir()
        backup_file.write_text("old binary", encoding='utf-8')
        helper_ps1.write_text("helper", encoding='utf-8')
        sys.argv[0] = str(target)

        state = UpdateState()
        state["state"] = "helper_started"
        state["target"] = str(target)
        state["backup_file"] = str(backup_file)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "helper_ps1", str(helper_ps1))
        state.save()

        _cleanup_update_residue(self.logger)

        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding='utf-8'), "old binary")
        self.assertFalse(runtime_dir.exists())
        loaded = UpdateState.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["state"], "rollback_done")

    def test_interrupted_recovering_rejects_root_runtime_dir(self):
        """恢复入口安全判断应拒绝盘符根目录或文件系统根目录。"""
        root_dir = Path(tempfile.gettempdir()).resolve().anchor
        self.assertTrue(root_dir)
        root_path = Path(root_dir)
        backup_file = root_path / "M9A_Update_Assistant.backup.exe"

        self.assertFalse(_is_safe_recovery_runtime_dir(root_path, backup_file))

    def test_interrupted_recovering_skips_unsafe_runtime_dir_cleanup(self):
        """恢复入口应跳过与备份文件不匹配的 runtime_dir，避免删除被污染路径。"""
        program_dir = Path(self.tmpdir) / "program"
        safe_runtime_dir = Path(self.tmpdir) / "runtime"
        unsafe_runtime_dir = Path(self.tmpdir) / "unsafe"
        target = program_dir / "app.exe"
        backup_file = safe_runtime_dir / "app.backup.exe"
        unsafe_file = unsafe_runtime_dir / "keep.txt"
        program_dir.mkdir()
        safe_runtime_dir.mkdir()
        unsafe_runtime_dir.mkdir()
        backup_file.write_text("old binary", encoding='utf-8')
        unsafe_file.write_text("do not delete", encoding='utf-8')
        sys.argv[0] = str(target)

        state = UpdateState()
        state["state"] = "helper_started"
        state["target"] = str(target)
        state["backup_file"] = str(backup_file)
        state.set("Files", "runtime_dir", str(unsafe_runtime_dir))
        state.save()

        with self.assertLogs("TestCleanup", level="WARNING") as captured:
            _cleanup_update_residue(self.logger)

        self.assertTrue(target.exists())
        self.assertTrue(unsafe_file.exists())
        self.assertTrue(unsafe_runtime_dir.exists())
        self.assertTrue(any("跳过越界运行时目录" in message for message in captured.output))

    def test_interrupted_recovering_without_safe_restore_keeps_state_and_runtime_basis(self):
        """无法安全恢复时不应删除状态，避免 runtime_dir 残留且依据丢失。"""
        program_dir = Path(self.tmpdir) / "program"
        runtime_dir = Path(self.tmpdir) / "runtime"
        target = program_dir / "app.exe"
        backup_file = runtime_dir / "app.backup.exe"
        helper_ps1 = runtime_dir / "helper.ps1"
        program_dir.mkdir()
        runtime_dir.mkdir()
        target.write_text("new binary", encoding='utf-8')
        backup_file.write_text("old binary", encoding='utf-8')
        helper_ps1.write_text("helper", encoding='utf-8')
        sys.argv[0] = str(target)

        state = UpdateState()
        state["state"] = "replacing"
        state["target"] = str(target)
        state["backup_file"] = str(backup_file)
        state.set("Files", "runtime_dir", str(runtime_dir))
        state.set("Files", "helper_ps1", str(helper_ps1))
        state.save()

        _cleanup_update_residue(self.logger)

        self.assertTrue(runtime_dir.exists())
        self.assertTrue(helper_ps1.exists())
        loaded = UpdateState.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["state"], "failed_disabled")
        self.assertEqual(loaded.get("Files", "runtime_dir"), str(runtime_dir))


class TestGeneratedPs1Scripts(unittest.TestCase):
    """验证生成的 PS1 脚本内容 — 确认 Get-SHA256 包含多路径 fallback"""

    def setUp(self):
        _suppress_logs()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _script_paths(self) -> dict[str, Path]:
        """构造生成 PS1 脚本所需的绝对路径字典。"""
        runtime_dir = Path(self.tmpdir).resolve()
        return {
            'runtime_dir': runtime_dir,
            'state_file': runtime_dir / UpdateState.STATE_FILE_NAME,
            'log_file': runtime_dir / 'update.log',
            'helper_ps1': runtime_dir / 'M9A_Update_Assistant_Update_Helper.ps1',
            'update_ps1': runtime_dir / 'M9A_Update_Assistant_Update.ps1',
            'lock_file': runtime_dir / 'update_started.lock',
        }

    def _read_generated(self, filename: str) -> str:
        """读取生成的 PS1 文件内容，缺失时立即失败。"""
        path = os.path.join(self.tmpdir, filename)
        self.assertTrue(os.path.exists(path), f"生成的 PS1 文件不存在: {path}")
        with open(path, 'r', encoding='utf-8-sig') as f:
            return f.read()

    def _assert_sha256_fallbacks(self, content: str, script_name: str) -> None:
        """断言 Get-SHA256 函数包含 .NET、Get-FileHash 与 certutil fallback"""
        self.assertIn("function Get-SHA256($filePath)", content,
                      f"{script_name} 中应包含 Get-SHA256 函数定义")
        self.assertIn("[System.IO.File]::OpenRead($filePath)", content,
                      f"{script_name} 中应先尝试 .NET 文件流")
        self.assertIn("[System.Security.Cryptography.SHA256]::Create()", content,
                      f"{script_name} 中应先尝试 .NET SHA256")
        self.assertIn("if ($sha256) { $sha256.Dispose() }", content,
                      f"{script_name} 中应释放 SHA256 对象")
        self.assertIn("if ($stream) { $stream.Dispose() }", content,
                      f"{script_name} 中应释放文件流")
        self.assertIn("Get-Command Get-FileHash -ErrorAction SilentlyContinue", content,
                      f"{script_name} 中应包含 Get-FileHash fallback")
        self.assertIn("Get-FileHash -Algorithm SHA256 -LiteralPath $filePath", content,
                      f"{script_name} 中应使用 LiteralPath 计算 Get-FileHash")
        self.assertIn("certutil.exe -hashfile", content,
                      f"{script_name} 中应包含 certutil fallback")
        self.assertIn("^[0-9A-Fa-f]{64}$", content,
                      f"{script_name} 中应解析 certutil 64 位 hex 输出")
        self.assertIn("throw \"Get-SHA256 failed:", content,
                      f"{script_name} 中应在全部失败时抛出明确错误")

    def test_sha256_fragment_module_generates_single_function(self):
        """PS1 片段模块生成单个带多路径 fallback 的 Get-SHA256 函数。"""
        from modules.ps1_fragments import generate_sha256_function_ps1

        content = generate_sha256_function_ps1()

        self._assert_sha256_fallbacks(content, "SHA256 片段")
        self.assertEqual(content.count('function Get-SHA256'), 1)
        self.assertIn('$errors = @()', content)
        self.assertIn('$LASTEXITCODE = 0', content)

    def test_ps1_fragment_module_generates_common_functions(self):
        """PS1 片段模块生成公共基础、状态与移动函数。"""
        from modules.ps1_fragments import (
            generate_common_base_functions_ps1,
            generate_common_state_functions_ps1,
            generate_move_with_retry_ps1,
        )

        base_content = generate_common_base_functions_ps1()
        state_content = generate_common_state_functions_ps1()
        move_content = generate_move_with_retry_ps1()
        combined_content = base_content + state_content + move_content

        for function_name in (
            "Normalize-IniValue",
            "Assert-NotEmpty",
            "Write-Log",
        ):
            self.assertIn(f"function {function_name}", base_content)

        for function_name in (
            "Read-IniValue",
            "Write-IniValue",
            "Set-UpdateStatus",
        ):
            self.assertIn(f"function {function_name}", state_content)

        self.assertIn("function Move-WithRetry", move_content)
        self.assertNotIn("Get-SHA256", combined_content)

    def test_ps1_fragment_module_generates_helper_unique_functions(self):
        """PS1 片段模块按职责生成 Helper 独有函数。"""
        from modules.ps1_fragments import (
            generate_helper_cleanup_functions_ps1,
            generate_helper_launch_functions_ps1,
            generate_helper_orchestration_functions_ps1,
            generate_helper_process_functions_ps1,
            generate_helper_rollback_functions_ps1,
        )

        process_content = generate_helper_process_functions_ps1()
        cleanup_content = generate_helper_cleanup_functions_ps1()
        rollback_content = generate_helper_rollback_functions_ps1()
        launch_content = generate_helper_launch_functions_ps1()
        orchestration_content = generate_helper_orchestration_functions_ps1()

        expected_functions_by_fragment = {
            process_content: (
                "Quote-Arg",
                "Start-ProcWait",
                "Wait-ProcessExit",
            ),
            cleanup_content: (
                "Remove-PathSafe",
                "Cleanup-StagedFiles",
                "Cleanup-OldInstallation",
            ),
            rollback_content: ("Restore-Backup",),
            launch_content: ("Launch-NewVersion",),
            orchestration_content: ("Run-UpdateAndVerify",),
        }
        forbidden_functions = (
            "Get-SHA256",
            "Move-WithRetry",
            "Set-UpdateStatus",
        )

        for content, expected_functions in expected_functions_by_fragment.items():
            for function_name in expected_functions:
                self.assertIn(f"function {function_name}", content)
            for function_name in forbidden_functions:
                self.assertNotIn(f"function {function_name}", content)

    def test_helper_unique_functions_only_exist_in_helper_ps1(self):
        """Helper 独有函数只写入 Helper.ps1，不写入 Update.ps1。"""
        paths = self._script_paths()
        SelfUpdater._generate_helper_ps1(paths)
        SelfUpdater._generate_update_ps1(paths)

        helper_content = self._read_generated("M9A_Update_Assistant_Update_Helper.ps1")
        update_content = self._read_generated("M9A_Update_Assistant_Update.ps1")

        for function_name in (
            "Quote-Arg",
            "Run-UpdateAndVerify",
            "Restore-Backup",
            "Launch-NewVersion",
        ):
            self.assertEqual(helper_content.count(f"function {function_name}"), 1)
            self.assertNotIn(f"function {function_name}", update_content)

    def test_helper_and_update_define_common_functions_once(self):
        """Helper.ps1 与 Update.ps1 均只定义一次公共函数。"""
        scripts = [
            (
                SelfUpdater._generate_helper_ps1,
                "M9A_Update_Assistant_Update_Helper.ps1",
            ),
            (
                SelfUpdater._generate_update_ps1,
                "M9A_Update_Assistant_Update.ps1",
            ),
        ]
        for generator, filename in scripts:
            with self.subTest(script=filename):
                generator(self._script_paths())
                content = self._read_generated(filename)
                self.assertEqual(content.count("function Get-SHA256"), 1)
                self.assertEqual(content.count("function Move-WithRetry"), 1)
                self.assertEqual(content.count("function Set-UpdateStatus"), 1)

    def test_helper_ps1_has_sha256_fallbacks(self):
        """Helper.ps1 包含多路径 SHA256 计算 fallback"""
        SelfUpdater._generate_helper_ps1(self._script_paths())
        content = self._read_generated("M9A_Update_Assistant_Update_Helper.ps1")
        self._assert_sha256_fallbacks(content, "Helper.ps1")

    def test_helper_ps1_has_get_sha256_call(self):
        """Helper.ps1 包含 Get-SHA256 $target 调用"""
        SelfUpdater._generate_helper_ps1(self._script_paths())
        content = self._read_generated("M9A_Update_Assistant_Update_Helper.ps1")
        self.assertIn("Get-SHA256 $target", content,
                      "Helper.ps1 中应包含 Get-SHA256 $target 调用")

    def test_update_ps1_has_sha256_fallbacks(self):
        """Update.ps1 包含多路径 SHA256 计算 fallback"""
        SelfUpdater._generate_update_ps1(self._script_paths())
        content = self._read_generated("M9A_Update_Assistant_Update.ps1")
        self._assert_sha256_fallbacks(content, "Update.ps1")

    def test_update_ps1_has_get_sha256_call(self):
        """Update.ps1 包含 Get-SHA256 $newFile 调用"""
        SelfUpdater._generate_update_ps1(self._script_paths())
        content = self._read_generated("M9A_Update_Assistant_Update.ps1")
        self.assertIn("Get-SHA256 $newFile", content,
                      "Update.ps1 中应包含 Get-SHA256 $newFile 调用")

    def test_generated_scripts_use_injected_absolute_paths(self):
        """生成脚本应使用注入的绝对路径，不再从脚本目录派生状态文件。"""
        paths = self._script_paths()

        SelfUpdater._generate_helper_ps1(paths)
        SelfUpdater._generate_update_ps1(paths)

        helper_content = self._read_generated("M9A_Update_Assistant_Update_Helper.ps1")
        update_content = self._read_generated("M9A_Update_Assistant_Update.ps1")
        expected_assignments = {
            'runtimeDir': paths['runtime_dir'],
            'stateFile': paths['state_file'],
            'logFile': paths['log_file'],
        }
        helper_expected = {
            **expected_assignments,
            'lockFile': paths['lock_file'],
            'updatePs1': paths['update_ps1'],
        }

        for variable_name, path in helper_expected.items():
            expected = f'${variable_name} = "{SelfUpdater._ps_quote(path)}"'
            self.assertIn(expected, helper_content)
        for variable_name, path in expected_assignments.items():
            expected = f'${variable_name} = "{SelfUpdater._ps_quote(path)}"'
            self.assertIn(expected, update_content)

        forbidden_patterns = (
            'Join-Path $scriptDir "update_state.ini"',
            'Join-Path $scriptDir "update.log"',
            'Join-Path $scriptDir "update_started.lock"',
            'Join-Path $scriptDir "M9A_Update_Assistant_Update.ps1"',
        )
        for pattern in forbidden_patterns:
            self.assertNotIn(pattern, helper_content)
            self.assertNotIn(pattern, update_content)

    def test_ps_quote_escapes_powershell_double_quoted_string_meta_characters(self):
        """PowerShell 双引号字符串中的路径应转义反引号、美元符号和双引号。"""
        path = Path('C:/Program Files/M9A`Update/$cache/quoted"name.exe')

        quoted = SelfUpdater._ps_quote(path)

        self.assertEqual(
            quoted,
            str(path).replace('`', '``').replace('$', '`$').replace('"', '`"'),
        )

    def test_generated_scripts_leave_no_placeholder_tokens(self):
        """生成脚本不应残留模板占位符。"""
        paths = self._script_paths()

        SelfUpdater._generate_helper_ps1(paths)
        SelfUpdater._generate_update_ps1(paths)

        helper_content = self._read_generated("M9A_Update_Assistant_Update_Helper.ps1")
        update_content = self._read_generated("M9A_Update_Assistant_Update.ps1")
        placeholders = (
            "__STATE_FILE__",
            "__LOG_FILE__",
            "__RUNTIME_DIR__",
            "__LOCK_FILE__",
            "__UPDATE_PS1__",
            "__COMMON_BASE_FUNCTIONS__",
            "__SHA256_FUNCTION__",
            "__COMMON_STATE_FUNCTIONS__",
            "__MOVE_WITH_RETRY_FUNCTION__",
            "__HELPER_PROCESS_FUNCTIONS__",
            "__HELPER_CLEANUP_FUNCTIONS__",
            "__HELPER_LAUNCH_FUNCTIONS__",
            "__HELPER_ROLLBACK_FUNCTIONS__",
            "__HELPER_ORCHESTRATION_FUNCTIONS__",
        )
        for placeholder in placeholders:
            self.assertNotIn(placeholder, helper_content)
            self.assertNotIn(placeholder, update_content)

    def test_generated_scripts_are_utf8_bom_with_crlf(self):
        """生成的 PS1 应使用 UTF-8 BOM 和 CRLF 换行。"""
        paths = self._script_paths()
        scripts = [
            (SelfUpdater._generate_helper_ps1, paths['helper_ps1']),
            (SelfUpdater._generate_update_ps1, paths['update_ps1']),
        ]

        for generator, path in scripts:
            with self.subTest(script=path.name):
                generator(paths)
                content = path.read_bytes()
                self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
                self.assertIn(b'\r\n', content)
                self.assertNotIn(b'\n', content.replace(b'\r\n', b''))

    def test_replace_executable_writes_runtime_paths_to_state(self):
        """替换流程应把 runtime 相关绝对路径写入 UpdateState。"""
        program_dir = Path(self.tmpdir) / 'program'
        program_dir.mkdir()
        current_exe = program_dir / 'M9A_Update_Assistant.exe'
        current_exe.write_text('old exe', encoding='utf-8')
        tmp_new = Path(self.tmpdir) / 'downloaded.exe'
        tmp_new.write_text('new exe', encoding='utf-8')
        sha_path = Path(self.tmpdir) / 'downloaded.sha256'
        sha_path.write_text('sha', encoding='ascii')
        updater = SelfUpdater('', str(Path(self.tmpdir) / 'runtime'), logging.getLogger("TestReplaceExecutable"))
        original_argv0 = sys.argv[0]
        sys.argv[0] = str(current_exe)

        class ReadyProcess:
            """模拟已启动的 helper 进程。"""

            returncode = None

            def poll(self):
                """返回 None 表示进程仍在运行。"""
                return None

            def kill(self):
                """模拟终止进程。"""
                return None

        def fake_popen(args, creationflags):
            """模拟 PowerShell 启动并创建握手锁文件。"""
            Path(args[5]).parent.joinpath('update_started.lock').write_text('', encoding='utf-8')
            return ReadyProcess()

        try:
            with mock.patch.object(SelfUpdater, '_get_exe_path', return_value=current_exe):
                with mock.patch('modules.self_updater.os.getpid', return_value=12345):
                    with mock.patch('modules.self_updater.subprocess.Popen', side_effect=fake_popen):
                        updater._replace_executable(tmp_new, sha_path, 'v2.0.0', 'oldhash', 'newhash')

            state = UpdateState.load()
            self.assertIsNotNone(state)
            paths = updater._build_update_runtime_paths(current_exe, 'v2.0.0')
            self.assertEqual(state.get('Files', 'runtime_dir'), str(paths['runtime_dir']))
            self.assertEqual(state.get('Files', 'helper_ps1'), str(paths['helper_ps1']))
            self.assertEqual(state.get('Files', 'update_ps1'), str(paths['update_ps1']))
            self.assertEqual(state.get('Files', 'lock_file'), str(paths['lock_file']))
            self.assertEqual(state.get('Files', 'log_file'), str(paths['log_file']))
            self.assertEqual(state['new_file'], str(paths['new_file']))
            self.assertEqual(state['backup_file'], str(paths['backup_file']))
            self.assertEqual(paths['new_file'].parent, paths['runtime_dir'])
            self.assertEqual(paths['backup_file'].parent, paths['runtime_dir'])
            self.assertEqual(paths['state_file'].parent, program_dir.resolve())
            self.assertEqual(paths['log_file'].parent, program_dir.resolve())
            for key in ('runtime_dir', 'helper_ps1', 'update_ps1', 'lock_file', 'log_file', 'new_file', 'backup_file'):
                self.assertTrue(Path(state.get('Files', key)).is_absolute())
        finally:
            sys.argv[0] = original_argv0
            _cleanup_state_file()

    def test_generated_get_sha256_matches_python_hashlib(self):
        """两个生成脚本的 Get-SHA256 均可处理含空格路径。"""
        if sys.platform != 'win32':
            self.skipTest("仅在 Windows 上验证 powershell.exe 生产路径")

        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("未找到 powershell.exe")

        input_dir = os.path.join(self.tmpdir, "hash input dir")
        os.makedirs(input_dir, exist_ok=True)
        test_file = os.path.join(input_dir, "hash input with space.txt")
        data = b"M9A sha256 test data\r\n\x00\xff"
        with open(test_file, 'wb') as f:
            f.write(data)
        expected_hash = hashlib.sha256(data).hexdigest()

        scripts = [
            (
                "Helper.ps1",
                SelfUpdater._generate_helper_ps1,
                "M9A_Update_Assistant_Update_Helper.ps1",
            ),
            (
                "Update.ps1",
                SelfUpdater._generate_update_ps1,
                "M9A_Update_Assistant_Update.ps1",
            ),
        ]
        for script_label, generator, filename in scripts:
            with self.subTest(script=script_label):
                generator(self._script_paths())
                content = self._read_generated(filename)
                function_match = re.search(
                    r"(?ms)^function Get-SHA256\(\$filePath\) \{.*?^\}",
                    content,
                )
                self.assertIsNotNone(
                    function_match,
                    f"应能从 {script_label} 提取 Get-SHA256 函数定义",
                )

                safe_label = script_label.replace('.', '_').lower()
                wrapper_path = os.path.join(
                    self.tmpdir,
                    f"invoke_get_sha256_{safe_label}.ps1",
                )
                wrapper_content = (
                    function_match.group(0)
                    + "\r\n$hash = Get-SHA256 -filePath $args[0]\r\n"
                    + "Write-Output $hash\r\n"
                )
                with open(
                    wrapper_path,
                    'w',
                    encoding='utf-8-sig',
                    newline='\r\n',
                ) as f:
                    f.write(wrapper_content)

                result = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        wrapper_path,
                        test_file,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

                actual_hash = result.stdout.strip()
                self.assertRegex(actual_hash, r"^[0-9a-f]{64}$")
                self.assertEqual(actual_hash, expected_hash)


def _suppress_logs():
    """抑制日志输出"""
    logging.getLogger("M9AUpdateAssistant").setLevel(logging.CRITICAL)


def _cleanup_state_file():
    """清理可能残留的 update_state.ini"""
    ini_path = Path(sys.argv[0]).resolve().with_name(UpdateState.STATE_FILE_NAME)
    try:
        ini_path.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == '__main__':
    unittest.main()
