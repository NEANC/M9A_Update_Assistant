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
        """1 线程请求 Pypdl 单段下载。0 已在配置解析层钳制为 1。"""
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

    def test_download_manager_does_not_import_requests(self):
        """文件下载适配层不再导入 requests。"""
        import modules.download_manager as download_manager
        self.assertFalse(hasattr(download_manager, 'requests'))

    @mock.patch('modules.download_manager.Pypdl')
    def test_pypdl_failed_empty_list_is_success(self, mock_pypdl_class):
        """Pypdl completed with failed=[] (never failed) 视为成功。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = []
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            os.unlink(path)
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertTrue(result)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_pypdl_failed_is_none_is_success(self, mock_pypdl_class):
        """Pypdl completed with failed=None 视为成功。"""
        mock_downloader = mock.MagicMock()
        mock_downloader.failed = None
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            os.unlink(path)
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertTrue(result)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @mock.patch('modules.download_manager.Pypdl')
    def test_pypdl_no_failed_attr_is_success(self, mock_pypdl_class):
        """Pypdl completed but failed 属性不存在时视为成功。"""
        mock_downloader = mock.MagicMock(spec=['start'])  # 仅暴露 start，failed 不存在
        mock_pypdl_class.return_value = mock_downloader

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
            path = f.name
        try:
            os.unlink(path)
            mock_downloader.start.side_effect = lambda *args, **kwargs: Path(path).write_bytes(b'data') or True
            result = self.dm.download_file_with_progress('https://example.com/file.bin', path)
            self.assertTrue(result)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestDownloadProgressBarFormat(unittest.TestCase):
    """下载进度条格式测试。"""

    def test_download_progress_bar_uses_postfix_speed_not_tqdm_rate(self):
        """下载进度条使用 postfix 显示实测速度。"""
        from modules.progress_bar import DOWNLOAD_BAR_FORMAT

        self.assertIn('{postfix}', DOWNLOAD_BAR_FORMAT)
        self.assertIn('{remaining}', DOWNLOAD_BAR_FORMAT)
        self.assertNotIn('{rate_fmt}', DOWNLOAD_BAR_FORMAT)

    @mock.patch('modules.progress_bar.tqdm')
    def test_create_download_progress_bar_uses_project_style(self, mock_tqdm):
        """下载进度条由 progress_bar 模块统一创建。"""
        from modules.progress_bar import DOWNLOAD_BAR_FORMAT
        from modules.progress_bar import create_download_progress_bar

        create_download_progress_bar(128, '下载 file.zip')

        mock_tqdm.assert_called_once_with(
            total=128,
            unit='B',
            unit_scale=True,
            unit_divisor=1024,
            desc='下载 file.zip',
            bar_format=DOWNLOAD_BAR_FORMAT,
            disable=False,
            leave=False,
        )

    def test_requirements_does_not_contain_pypdl(self):
        """运行依赖不再包含 Pypdl。"""
        requirements = Path('requirements.txt').read_text(encoding='utf-8')
        self.assertNotIn('pypdl', requirements.lower())
        self.assertIn('requests', requirements.lower())


if __name__ == '__main__':
    unittest.main()
