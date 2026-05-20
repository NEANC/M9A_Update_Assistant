#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import unittest

from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.download_manager import DownloadManager


class TestProgressHelpers(unittest.TestCase):
    """进度条辅助方法测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestDownload")
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger)

    def test_clear_progress_line(self):
        """清除进度行"""
        DownloadManager.clear_progress_line()

    def test_reset_progress_timer(self):
        """重置节流计时器"""
        self.dm._last_progress_time = 999.0
        self.dm.reset_progress_timer()
        self.assertEqual(self.dm._last_progress_time, 0.0)


class TestDownloadFile(unittest.TestCase):
    """download_file_with_progress 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestDownload")
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger)

    @mock.patch('modules.download_manager.requests.get')
    def test_successful_download(self, mock_get):
        """成功下载"""
        mock_response = mock.MagicMock()
        mock_response.headers = {'Content-Length': '12'}
        mock_response.iter_content.return_value = [b'Hello, ', b'World!']
        mock_response.raise_for_status.return_value = None
        mock_get.return_value.__enter__.return_value = mock_response

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertTrue(result)
            with open(path, 'rb') as f:
                content = f.read()
            self.assertEqual(content, b'Hello, World!')
        finally:
            os.unlink(path)

    @mock.patch('modules.download_manager.requests.get')
    def test_download_http_error(self, mock_get):
        """HTTP 错误"""
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.side_effect = __import__('requests').HTTPError('404')
        mock_get.return_value.__enter__.return_value = mock_response

        result = self.dm.download_file_with_progress('https://example.com/file.bin', '/tmp/test.bin')
        self.assertFalse(result)

    @mock.patch('modules.download_manager.requests.get')
    def test_no_content_length(self, mock_get):
        """无 Content-Length 头"""
        mock_response = mock.MagicMock()
        mock_response.headers = {}
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status.return_value = None
        mock_get.return_value.__enter__.return_value = mock_response

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertTrue(result)
        finally:
            os.unlink(path)

    @mock.patch('modules.download_manager.requests.get')
    def test_creates_parent_dir(self, mock_get):
        """自动创建父目录"""
        mock_response = mock.MagicMock()
        mock_response.headers = {'Content-Length': '4'}
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status.return_value = None
        mock_get.return_value.__enter__.return_value = mock_response

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, 'sub', 'deep', 'file.bin')
            result = self.dm.download_file_with_progress('https://example.com/file.bin', save_path)
            self.assertTrue(result)
            self.assertTrue(os.path.exists(save_path))


class TestPrintProgress(unittest.TestCase):
    """print_progress 节流测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestDownload")
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger)

    @mock.patch('builtins.print')
    def test_first_call_always_prints(self, mock_print):
        """首次调用始终打印"""
        self.dm.print_progress("测试", 50.0, 50.0, 100.0)
        mock_print.assert_called_once()

    @mock.patch('builtins.print')
    def test_throttle_within_200ms(self, mock_print):
        """200ms 内节流"""
        self.dm.print_progress("测试", 50.0, 50.0, 100.0)
        mock_print.reset_mock()
        self.dm.print_progress("测试", 51.0, 51.0, 100.0)
        mock_print.assert_not_called()

    @mock.patch('builtins.print')
    def test_always_print_at_100_percent(self, mock_print):
        """100% 始终打印"""
        self.dm.print_progress("测试", 99.0, 99.0, 100.0)
        mock_print.reset_mock()
        self.dm.print_progress("测试", 100.0, 100.0, 100.0)
        mock_print.assert_called_once()

    def test_reset_timer_allows_next_print(self):
        """重置计时器后允许打印"""
        self.dm.print_progress("测试", 50.0, 50.0, 100.0)
        self.dm._last_progress_time = 0.0
        with mock.patch('builtins.print') as mock_print:
            self.dm.print_progress("测试", 51.0, 51.0, 100.0)
            mock_print.assert_called_once()


if __name__ == '__main__':
    unittest.main()
