#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import unittest

from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.download_manager import DownloadManager


class TestDownloadFile(unittest.TestCase):
    """download_file_with_progress 测试"""

    def setUp(self):
        self.logger = logging.getLogger("TestDownload")
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger)

    @staticmethod
    def _make_tqdm_mock(mock_tqdm_class):
        """构造带 format_dict 的 tqdm mock，使其能正常进出上下文"""
        mock_pbar = mock.MagicMock()
        mock_pbar.format_dict = {'rate': 0}
        mock_pbar.__enter__.return_value = mock_pbar
        mock_tqdm_class.return_value = mock_pbar
        mock_tqdm_class.format_sizeof = mock.MagicMock(return_value="0B")
        return mock_pbar

    @mock.patch('modules.progress_bar.tqdm')
    @mock.patch('modules.download_manager.requests.get')
    def test_successful_download(self, mock_get, mock_tqdm):
        """成功下载"""
        self._make_tqdm_mock(mock_tqdm)
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

    @mock.patch('modules.progress_bar.tqdm')
    @mock.patch('modules.download_manager.requests.get')
    def test_download_http_error(self, mock_get, mock_tqdm):
        """HTTP 错误"""
        self._make_tqdm_mock(mock_tqdm)
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.side_effect = __import__('requests').HTTPError('404')
        mock_get.return_value.__enter__.return_value = mock_response

        result = self.dm.download_file_with_progress('https://example.com/file.bin', '/tmp/test.bin')
        self.assertFalse(result)

    @mock.patch('modules.progress_bar.tqdm')
    @mock.patch('modules.download_manager.requests.get')
    def test_no_content_length(self, mock_get, mock_tqdm):
        """无 Content-Length 头"""
        self._make_tqdm_mock(mock_tqdm)
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

    @mock.patch('modules.progress_bar.tqdm')
    @mock.patch('modules.download_manager.requests.get')
    def test_creates_parent_dir(self, mock_get, mock_tqdm):
        """自动创建父目录"""
        self._make_tqdm_mock(mock_tqdm)
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


if __name__ == '__main__':
    unittest.main()
