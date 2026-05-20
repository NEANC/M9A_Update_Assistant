#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.github_release_client import GitHubReleaseClient


class TestCompilePattern(unittest.TestCase):
    """GitHubReleaseClient.compile_pattern 静态方法测试"""

    def test_basic_pattern(self):
        """基本通配符匹配"""
        rx = GitHubReleaseClient.compile_pattern('M9A-win-x86_64-v*-Lite.zip')
        self.assertIsNotNone(rx.match('M9A-win-x86_64-v3.28.3-Lite.zip'))
        self.assertIsNotNone(rx.match('M9A-win-x86_64-v1.0.0-Lite.zip'))

    def test_pattern_not_match(self):
        """不匹配其他版本"""
        rx = GitHubReleaseClient.compile_pattern('M9A-win-x86_64-v*-Lite.zip')
        self.assertIsNone(rx.match('M9A-win-x86_64-v3.28.3-PiCLI.zip'))
        self.assertIsNone(rx.match('M9A-win-x86_64-v3.28.3-MXU.zip'))
        self.assertIsNone(rx.match('other-file.zip'))

    def test_anchoring(self):
        """测试正则锚定，防止部分匹配"""
        rx = GitHubReleaseClient.compile_pattern('M9A-win-x86_64-v*-Lite.zip')
        self.assertIsNone(rx.match('prefix_M9A-win-x86_64-v3.28.3-Lite.zip'))
        self.assertIsNone(rx.match('M9A-win-x86_64-v3.28.3-Lite.zip_suffix'))

    def test_wildcard_any_char(self):
        """* 应匹配任意字符"""
        rx = GitHubReleaseClient.compile_pattern('M9A-*-*.zip')
        self.assertIsNotNone(rx.match('M9A-win-x86_64-v3.28.3-Lite.zip'))
        self.assertIsNotNone(rx.match('M9A-abc-def.zip'))
        self.assertIsNone(rx.match('M9A.zip'))

    def test_escape_special_chars(self):
        """特殊字符应被转义"""
        rx = GitHubReleaseClient.compile_pattern('file.+\\*.txt')
        self.assertIsNotNone(rx.match('file.+\\anything.txt'))
        self.assertIsNone(rx.match('fileX+\\anything.txt'))
        self.assertIsNone(rx.match('file.++\\anything.txt'))

    def test_empty_pattern(self):
        """空模式只匹配空字符串"""
        rx = GitHubReleaseClient.compile_pattern('')
        self.assertEqual(rx.pattern, '^$')

    def test_pattern_no_wildcard(self):
        """无通配符的精确匹配"""
        rx = GitHubReleaseClient.compile_pattern('M9A_Update_Assistant.exe')
        self.assertIsNotNone(rx.match('M9A_Update_Assistant.exe'))
        self.assertIsNone(rx.match('M9A_Update_Assistant-Nuitka.exe'))


class TestParseReleaseKeywords(unittest.TestCase):
    """parse_release_keywords 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestGitHub")
        self.logger.setLevel(logging.CRITICAL)
        self.client = GitHubReleaseClient('test/repo', 'release', '', self.logger)

    def test_empty_body_defaults(self):
        """空 body 返回默认关键词"""
        result = self.client.parse_release_keywords({'body': ''})
        self.assertEqual(result['cli'], 'Lite')
        self.assertEqual(result['gui'], 'Full')
        self.assertEqual(result['gui_keywords'], ['Full'])

    def test_extract_keywords(self):
        """正常提取关键词"""
        body = """## 更新内容
PiCLI = 命令行版
MXU = 图形界面版
MFAA = 图形界面版
"""
        result = self.client.parse_release_keywords({'body': body})
        self.assertEqual(result['cli'], 'PiCLI')
        self.assertEqual(result['gui'], 'MFAA')
        self.assertEqual(result['gui_keywords'], ['MXU', 'MFAA'])

    def test_only_cli_keyword(self):
        """只有命令行版关键词"""
        body = "PiCLI = 命令行版"
        result = self.client.parse_release_keywords({'body': body})
        self.assertEqual(result['cli'], 'PiCLI')
        self.assertEqual(result['gui'], 'Full')
        self.assertEqual(result['gui_keywords'], ['Full'])


class TestFindDownloadUrl(unittest.TestCase):
    """find_download_url 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestGitHub")
        self.logger.setLevel(logging.CRITICAL)
        self.client = GitHubReleaseClient('test/repo', 'release', '', self.logger)

    def _make_release(self, assets):
        return {'assets': assets}

    def test_basic_match(self):
        """基本文件匹配"""
        release = self._make_release([
            {'name': 'M9A-win-x86_64-v3.28.3-Lite.zip', 'browser_download_url': 'https://url/lite', 'size': 100},
        ])
        url = self.client.find_download_url(release, 'M9A-win-x86_64-v*-Lite.zip')
        self.assertEqual(url, 'https://url/lite')

    def test_no_match(self):
        """无匹配文件"""
        release = self._make_release([
            {'name': 'other-file.txt', 'browser_download_url': 'https://url/other', 'size': 100},
        ])
        url = self.client.find_download_url(release, 'M9A-win-x86_64-v*-Lite.zip')
        self.assertIsNone(url)

    def test_select_smallest(self):
        """选择最小文件"""
        release = self._make_release([
            {'name': 'M9A-win-x86_64-v3.28.3-MXU.zip', 'browser_download_url': 'https://url/mxu', 'size': 500},
            {'name': 'M9A-win-x86_64-v3.28.3-MFAA.zip', 'browser_download_url': 'https://url/mfaa', 'size': 200},
            {'name': 'M9A-win-x86_64-v3.28.3-PiCLI.zip', 'browser_download_url': 'https://url/picli', 'size': 300},
        ])
        url = self.client.find_download_url(
            release, 'M9A-win-x86_64-v*-*.zip', select_smallest=True,
        )
        self.assertEqual(url, 'https://url/mfaa')

    def test_exclude_patterns(self):
        """排除指定模式"""
        release = self._make_release([
            {'name': 'M9A-win-x86_64-v3.28.3-PiCLI.zip', 'browser_download_url': 'https://url/picli', 'size': 100},
            {'name': 'M9A-win-x86_64-v3.28.3-MXU.zip', 'browser_download_url': 'https://url/mxu', 'size': 500},
        ])
        url = self.client.find_download_url(
            release, 'M9A-win-x86_64-v*-*.zip',
            select_smallest=True,
            exclude_patterns=['M9A-win-x86_64-v*-PiCLI.zip'],
        )
        self.assertEqual(url, 'https://url/mxu')


class TestGetAssetSha256(unittest.TestCase):
    """get_asset_sha256 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestGitHub")
        self.logger.setLevel(logging.CRITICAL)
        self.client = GitHubReleaseClient('test/repo', 'release', '', self.logger)

    def test_digest_field(self):
        """从 asset.digest 字段读取"""
        release = {'assets': [
            {'name': 'test.zip', 'digest': 'sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890'},
        ]}
        result = self.client.get_asset_sha256(release, 'test.zip')
        self.assertEqual(result, 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890')

    def test_body_fallback(self):
        """回退到 body 解析"""
        body = """## Checksums
test.zip sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
"""
        release = {'assets': [], 'body': body}
        result = self.client.get_asset_sha256(release, 'test.zip')
        self.assertEqual(result, '1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef')

    def test_not_found(self):
        """未找到"""
        release = {'assets': [], 'body': ''}
        result = self.client.get_asset_sha256(release, 'missing.zip')
        self.assertIsNone(result)


class TestGetExeSha256FromBody(unittest.TestCase):
    """get_exe_sha256_from_body 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestGitHub")
        self.logger.setLevel(logging.CRITICAL)
        self.client = GitHubReleaseClient('test/repo', 'release', '', self.logger)

    def test_find_in_body(self):
        body = """## Checksums
M9A_Update_Assistant-Nuitka-v1.10.0.exe sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789
"""
        result = self.client.get_exe_sha256_from_body(
            {'body': body}, 'M9A_Update_Assistant-Nuitka-v1.10.0.exe',
        )
        self.assertEqual(result, 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789')

    def test_not_found(self):
        result = self.client.get_exe_sha256_from_body({'body': ''}, 'nonexistent.exe')
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
