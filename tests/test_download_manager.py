#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import unittest

from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.download_manager import DownloadManager


class TestDownloadFile(unittest.TestCase):
    """download_file_with_progress 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestDownload")
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger)

    @mock.patch('modules.download_manager.Pypdl')
    def test_successful_download_uses_pypdl(self, mock_pypdl_class):
        """成功下载时通过 Pypdl 下载文件。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = []
        mock_downloader.size = 12
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            os.unlink(path)
            def create_file(*args, **kwargs):
                with open(path, 'wb') as output:
                    output.write(b'Hello, World!')
                return True
            mock_downloader.start.side_effect = create_file

            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertTrue(result)
            mock_downloader.start.assert_called_once()
            _, kwargs = mock_downloader.start.call_args
            self.assertEqual(kwargs['url'], 'https://example.com/file.bin')
            self.assertEqual(kwargs['file_path'], path)
            self.assertEqual(kwargs['retries'], 0)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_single_thread_parameters(self, mock_pypdl_class):
        """0 和 1 线程请求 Pypdl 单段下载。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = []
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name

        try:
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            dm = DownloadManager('', '/tmp', self.logger, download_threads=1)
            self.assertTrue(dm.download_file_with_progress('https://example.com/file.bin', path))
            _, kwargs = mock_downloader.start.call_args
            self.assertFalse(kwargs['multisegment'])
            self.assertEqual(kwargs['segments'], 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_multi_thread_parameters(self, mock_pypdl_class):
        """2 及以上线程请求 Pypdl 多分段下载。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = []
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name

        try:
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            dm = DownloadManager('', '/tmp', self.logger, download_threads=8)
            self.assertTrue(dm.download_file_with_progress('https://example.com/file.bin', path))
            _, kwargs = mock_downloader.start.call_args
            self.assertTrue(kwargs['multisegment'])
            self.assertEqual(kwargs['segments'], 8)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_proxy_and_user_agent_are_passed_to_pypdl(self, mock_pypdl_class):
        """代理与 User-Agent 按 Pypdl 参数传递。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = []
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name

        try:
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            dm = DownloadManager('socks5://127.0.0.1:10809', '/tmp', self.logger, download_threads=4)
            self.assertTrue(dm.download_file_with_progress('https://example.com/file.bin', path))
            _, kwargs = mock_downloader.start.call_args
            self.assertEqual(kwargs['proxy'], 'socks5://127.0.0.1:10809')
            self.assertEqual(kwargs['headers']['User-Agent'], 'M9A-Update-Assistant')
            self.assertNotIn('proxies', kwargs)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_pypdl_exception_returns_false(self, mock_pypdl_class):
        """Pypdl 异常时返回 False。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.start.side_effect = RuntimeError('download failed')
        mock_pypdl_class.return_value = mock_downloader

        result = self.dm.download_file_with_progress('https://example.com/file.bin', '/tmp/test.bin')
        self.assertFalse(result)

    @mock.patch('modules.download_manager.Pypdl')
    def test_pypdl_failed_items_returns_false(self, mock_pypdl_class):
        """Pypdl 完成但 failed 非空时返回 False。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = ['segment failed']
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name

        try:
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertFalse(result)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_missing_target_file_returns_false(self, mock_pypdl_class):
        """Pypdl 完成但目标文件不存在时返回 False。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = []
        mock_pypdl_class.return_value = mock_downloader

        result = self.dm.download_file_with_progress('https://example.com/file.bin', '/tmp/missing-test.bin')
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
