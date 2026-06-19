#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import shutil
import sys
import tempfile
import unittest

from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config_self_updater import UpdateState
from modules.self_updater import SelfUpdater, _get_existing_retry_count
from M9A_Update_Assistant import _cleanup_update_residue


class TestGetExePath(unittest.TestCase):
    """_get_exe_path 测试"""

    def test_returns_path(self):
        path = SelfUpdater._get_exe_path()
        self.assertIsInstance(path, Path)


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
        """force=True 跳过 Build 版本检查，直接进入版本比对"""
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

    def test_verified_cleans_residue(self):
        """verified 状态 → 清理残留文件 + 删除状态文件"""
        target = os.path.join(self.tmpdir, "M9A_Update_Assistant.exe")
        backup = os.path.join(self.tmpdir, "M9A_Update_Assistant.backup.exe")
        old_exe = os.path.join(self.tmpdir, "M9A_Update_Assistant.old.exe")
        Path(backup).write_text("backup")
        Path(old_exe).write_text("old helper")

        state = UpdateState()
        state["state"] = "verified"
        state["target"] = target
        state["backup_file"] = backup
        state.save()

        _cleanup_update_residue(self.logger)
        self.assertFalse(os.path.exists(backup))
        self.assertFalse(os.path.exists(old_exe))
        self.assertIsNone(UpdateState.load())

    def test_interrupted_recovering_restores(self):
        """helper_started 且 backup 存在且 target 不存在 → 恢复备份"""
        target = os.path.join(self.tmpdir, "app.exe")
        backup_file = os.path.join(self.tmpdir, "app.backup.exe")
        Path(backup_file).write_text("old binary")

        state = UpdateState()
        state["state"] = "helper_started"
        state["target"] = target
        state["backup_file"] = backup_file
        state.save()

        _cleanup_update_residue(self.logger)
        self.assertTrue(os.path.exists(target))
        with open(target, 'r') as f:
            self.assertEqual(f.read(), "old binary")
        self.assertIsNone(UpdateState.load())


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
