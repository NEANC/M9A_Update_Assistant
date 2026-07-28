#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import hashlib
import logging
import os
import sys
import tempfile
import unittest
import zipfile

from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.zip_manager import ZipManager
from modules.github_release_client import GitHubReleaseClient


class TestCalculateSha256(unittest.TestCase):
    """calculate_sha256 测试"""

    def test_known_content(self):
        """已知内容的 SHA256"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            f.write(b'Hello, World!')
            path = f.name
        try:
            expected = hashlib.sha256(b'Hello, World!').hexdigest()
            actual = ZipManager.calculate_sha256(path)
            self.assertEqual(actual, expected)
        finally:
            os.unlink(path)

    def test_empty_file(self):
        """空文件 SHA256"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            expected = hashlib.sha256(b'').hexdigest()
            actual = ZipManager.calculate_sha256(path)
            self.assertEqual(actual, expected)
        finally:
            os.unlink(path)

    def test_large_file(self):
        """大文件 SHA256（测试分块读取）"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            data = os.urandom(100000)
            f.write(data)
            path = f.name
        try:
            expected = hashlib.sha256(data).hexdigest()
            actual = ZipManager.calculate_sha256(path)
            self.assertEqual(actual, expected)
        finally:
            os.unlink(path)


class TestVerifyZipIntegrity(unittest.TestCase):
    """verify_zip_integrity 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestZip")
        self.logger.setLevel(logging.CRITICAL)
        self.gh_client = GitHubReleaseClient('test/repo', 'release', '', self.logger)
        self.zip_mgr = ZipManager(self.logger)

    def test_valid_zip(self):
        """有效 ZIP 文件"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as f:
            with zipfile.ZipFile(f, 'w') as zf:
                zf.writestr('test.txt', 'hello')
            path = f.name
        try:
            result = self.zip_mgr.verify_zip_integrity(
                path, {'assets': []}, 'test.zip', self.gh_client,
            )
            self.assertTrue(result)
        finally:
            os.unlink(path)

    def test_invalid_zip(self):
        """无效 ZIP 文件"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as f:
            f.write(b'not a zip file')
            path = f.name
        try:
            result = self.zip_mgr.verify_zip_integrity(
                path, {'assets': []}, 'test.zip', self.gh_client,
            )
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_nonexistent_file(self):
        """不存在的文件"""
        result = self.zip_mgr.verify_zip_integrity(
            '/nonexistent/path.zip', {'assets': []}, 'test.zip', self.gh_client,
        )
        self.assertFalse(result)

    def test_with_valid_sha256(self):
        """带有效 SHA256 的 ZIP"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as f:
            with zipfile.ZipFile(f, 'w') as zf:
                zf.writestr('test.txt', 'hello')
            path = f.name
        try:
            actual = ZipManager.calculate_sha256(path)
            release = {'assets': [
                {'name': 'test.zip', 'digest': f'sha256:{actual}'},
            ]}
            result = self.zip_mgr.verify_zip_integrity(
                path, release, 'test.zip', self.gh_client,
            )
            self.assertTrue(result)
        finally:
            os.unlink(path)

    def test_with_mismatched_sha256(self):
        """SHA256 不匹配"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as f:
            with zipfile.ZipFile(f, 'w') as zf:
                zf.writestr('test.txt', 'hello')
            path = f.name
        try:
            release = {'assets': [
                {'name': 'test.zip', 'digest': 'sha256:0000000000000000000000000000000000000000000000000000000000000000'},
            ]}
            result = self.zip_mgr.verify_zip_integrity(
                path, release, 'test.zip', self.gh_client,
            )
            self.assertFalse(result)
        finally:
            os.unlink(path)


class TestVerifyExeSha256(unittest.TestCase):
    """verify_exe_sha256 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestZip")
        self.logger.setLevel(logging.CRITICAL)
        self.gh_client = GitHubReleaseClient('test/repo', 'release', '', self.logger)
        self.zip_mgr = ZipManager(self.logger)

    def test_match_body(self):
        """从 body 提取校验成功"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as f:
            f.write(b'test binary content')
            path = f.name
        try:
            actual = ZipManager.calculate_sha256(path)
            release = {'body': f"test.exe sha256:{actual}"}
            result = self.zip_mgr.verify_exe_sha256(path, release, 'test.exe', self.gh_client)
            self.assertTrue(result)
        finally:
            os.unlink(path)

    def test_no_body_skip(self):
        """无 body 中 SHA256 时跳过校验"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as f:
            f.write(b'test')
            path = f.name
        try:
            result = self.zip_mgr.verify_exe_sha256(path, {'body': ''}, 'test.exe', self.gh_client)
            self.assertTrue(result)  # 跳过即通过
        finally:
            os.unlink(path)

    def test_mismatch_body(self):
        """SHA256 不匹配"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as f:
            f.write(b'real content')
            path = f.name
        try:
            release = {'body': 'test.exe sha256:0000000000000000000000000000000000000000000000000000000000000000'}
            result = self.zip_mgr.verify_exe_sha256(path, release, 'test.exe', self.gh_client)
            self.assertFalse(result)
        finally:
            os.unlink(path)

    def test_no_body_strict(self):
        """allow_fallback=False 时无 SHA256 应失败"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.exe') as f:
            f.write(b'test')
            path = f.name
        try:
            result = self.zip_mgr.verify_exe_sha256(
                path, {'body': ''}, 'test.exe', self.gh_client, allow_fallback=False,
            )
            self.assertFalse(result)
        finally:
            os.unlink(path)


class TestVerifyFileSha256(unittest.TestCase):
    """verify_file_sha256 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestZip")
        self.logger.setLevel(logging.CRITICAL)
        self.zip_mgr = ZipManager(self.logger)

    def test_match(self):
        """SHA256 匹配"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            f.write(b'data to verify')
            path = f.name
        try:
            expected = ZipManager.calculate_sha256(path)
            self.assertTrue(self.zip_mgr.verify_file_sha256(path, expected))
        finally:
            os.unlink(path)

    def test_mismatch(self):
        """SHA256 不匹配"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            f.write(b'real data')
            path = f.name
        try:
            self.assertFalse(self.zip_mgr.verify_file_sha256(
                path, '0000000000000000000000000000000000000000000000000000000000000000',
            ))
        finally:
            os.unlink(path)


class TestRemovedDepsMethods(unittest.TestCase):
    """deps 相关方法移除测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestZip")
        self.logger.setLevel(logging.CRITICAL)
        self.zip_mgr = ZipManager(self.logger)

    def test_deps_methods_removed(self):
        """ZipManager 不再提供 deps 检查或提取方法"""
        self.assertFalse(hasattr(self.zip_mgr, 'check_lite_zip_has_deps'))
        self.assertFalse(hasattr(self.zip_mgr, 'extract_deps_from_full_zip'))


class TestExtractZip(unittest.TestCase):
    """extract_zip_with_progress 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestZip")
        self.logger.setLevel(logging.CRITICAL)
        self.zip_mgr = ZipManager(self.logger)

    def test_extract_basic(self):
        """基本解压"""
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test.zip')
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('file1.txt', 'content1')
                zf.writestr('sub/file2.txt', 'content2')

            extract_dir = os.path.join(tmpdir, 'out')
            result = self.zip_mgr.extract_zip_with_progress(zip_path, extract_dir)
            self.assertTrue(result)
            self.assertTrue(os.path.exists(os.path.join(extract_dir, 'file1.txt')))
            self.assertTrue(os.path.exists(os.path.join(extract_dir, 'sub/file2.txt')))

    @mock.patch('modules.zip_manager.create_progress_bar')
    def test_extract_progress_bar_desc_includes_zip_name(self, mock_create_progress_bar):
        """解压进度条描述包含 ZIP 文件名。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test.zip')
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('file1.txt', 'content1')

            extract_dir = os.path.join(tmpdir, 'out')
            mock_pbar = mock_create_progress_bar.return_value.__enter__.return_value

            result = self.zip_mgr.extract_zip_with_progress(zip_path, extract_dir)

            self.assertTrue(result)
            mock_create_progress_bar.assert_called_once_with(
                total=len('content1'),
                desc='解压 test.zip',
            )
            mock_pbar.update.assert_called_once_with(len('content1'))

    def test_extract_nonexistent_zip(self):
        """解压不存在的文件"""
        result = self.zip_mgr.extract_zip_with_progress('/nonexistent.zip', '/tmp/out')
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
