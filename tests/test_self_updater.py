#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import unittest

from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.self_updater import SelfUpdater


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
        self.assertTrue(su._is_prerelease('v1.11.0-beta2'))
        self.assertTrue(su._is_prerelease('v1.11.0-rc'))
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


class TestCheckSelfUpdate(unittest.TestCase):
    """check_self_update 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestSelfUpdate")
        self.logger.setLevel(logging.CRITICAL)
        self.su = SelfUpdater('', '/tmp', self.logger)

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


class TestRollback(unittest.TestCase):
    """rollback 静态方法测试"""

    def test_no_backup(self):
        """无 .bak 文件"""
        # 不应抛出异常
        SelfUpdater.rollback()


if __name__ == '__main__':
    unittest.main()
