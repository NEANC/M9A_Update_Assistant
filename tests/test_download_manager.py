#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import tempfile
import time
import unittest

from pathlib import Path
from threading import Lock
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.download_manager import DownloadManager, NetworkSpeedMeter


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


class FakeResponse:
    """requests 响应替身。"""

    def __init__(self, status_code=200, headers=None, chunks=None, error=None):
        """初始化响应替身。"""
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = chunks or []
        self.error = error
        self.iter_content_chunk_sizes = []

    def __enter__(self):
        """进入上下文。"""
        return self

    def __exit__(self, exc_type, exc, traceback):
        """退出上下文。"""
        return False

    def raise_for_status(self):
        """模拟 HTTP 状态检查。"""
        if self.error:
            raise self.error
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def iter_content(self, chunk_size):
        """流式返回 chunk。"""
        self.iter_content_chunk_sizes.append(chunk_size)
        for chunk in self.chunks:
            yield chunk


class FakeSession:
    """requests Session 替身。"""

    def __init__(self, head_response=None, get_responses=None):
        """初始化 Session 替身。"""
        self.head_response = head_response
        self.get_responses = list(get_responses or [])
        self.head_calls = []
        self.get_calls = []

    def head(self, url, **kwargs):
        """记录 HEAD 调用并返回响应。"""
        self.head_calls.append((url, kwargs))
        return self.head_response

    def get(self, url, **kwargs):
        """记录 GET 调用并返回响应。"""
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def close(self):
        """记录关闭操作，无实际操作。"""
        pass


class TestDownloadManagerHelpers(unittest.TestCase):
    """DownloadManager 下载 helper 测试。"""

    def setUp(self):
        """初始化测试对象。"""
        self.logger = logging.getLogger('TestDownloadHelpers')
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger, download_threads=4)

    def test_build_proxies_returns_none_when_proxy_empty(self):
        """空代理返回 None。"""
        self.assertIsNone(self.dm._build_proxies())

    def test_build_proxies_returns_http_and_https_proxy(self):
        """非空代理同时用于 http 和 https。"""
        dm = DownloadManager('socks5://127.0.0.1:10809', '/tmp', self.logger)
        self.assertEqual(
            dm._build_proxies(),
            {'http': 'socks5://127.0.0.1:10809', 'https': 'socks5://127.0.0.1:10809'},
        )

    def test_get_download_metadata_reads_length_and_range(self):
        """HEAD 元数据读取总大小与 Range 支持。"""
        session = FakeSession(
            head_response=FakeResponse(
                headers={'Content-Length': '10', 'Accept-Ranges': 'bytes'},
            )
        )

        metadata = self.dm._get_download_metadata(session, 'https://example.com/file.bin')

        self.assertEqual(metadata.total_size, 10)
        self.assertTrue(metadata.supports_range)
        _, kwargs = session.head_calls[0]
        self.assertEqual(kwargs['timeout'], (15, 60))
        self.assertEqual(kwargs['headers']['User-Agent'], 'M9A-Update-Assistant')

    def test_get_download_metadata_falls_back_when_head_fails(self):
        """HEAD 失败时返回未知大小并禁用 Range。"""
        session = FakeSession(head_response=FakeResponse(status_code=500))

        metadata = self.dm._get_download_metadata(session, 'https://example.com/file.bin')

        self.assertEqual(metadata.total_size, 0)
        self.assertFalse(metadata.supports_range)

    def test_split_segments_distributes_remainder_to_front(self):
        """分段闭区间覆盖完整文件。"""
        segments = self.dm._split_segments(10, 3)
        self.assertEqual([(s.start, s.end) for s in segments], [(0, 3), (4, 6), (7, 9)])

    def test_format_speed_uses_binary_units(self):
        """网络速度按 B/s、KiB/s、MiB/s 格式化。"""
        self.assertEqual(self.dm._format_speed(512), '512.00B/s')
        self.assertEqual(self.dm._format_speed(2048), '2.00KiB/s')
        self.assertEqual(self.dm._format_speed(2 * 1024 * 1024), '2.00MiB/s')


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


class FakeProgressBar:
    """tqdm 进度条替身。"""

    def __init__(self):
        """初始化进度条替身。"""
        self.n = 0
        self.total = None
        self.updates = []
        self.postfixes = []
        self.closed = False
        self.refresh_count = 0

    def update(self, value):
        """记录进度增量。"""
        self.updates.append(value)
        self.n += value

    def set_postfix_str(self, value, refresh=False):
        """记录 postfix。"""
        self.postfixes.append((value, refresh))

    def refresh(self):
        """记录刷新。"""
        self.refresh_count += 1

    def close(self):
        """记录关闭。"""
        self.closed = True


class TestSingleThreadDownload(unittest.TestCase):
    """单线程下载测试。"""

    def setUp(self):
        """初始化测试对象。"""
        self.logger = logging.getLogger('TestSingleThreadDownload')
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger, download_threads=1)

    def test_single_thread_download_writes_stream_and_updates_progress(self):
        """单线程从头下载时流式写入并更新进度条。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
        os.unlink(path)
        response = FakeResponse(status_code=200, headers={'Content-Length': '6'}, chunks=[b'abc', b'def'])
        session = FakeSession(get_responses=[response])
        pbar = FakeProgressBar()

        try:
            result = self.dm._download_single_threaded(
                session,
                'https://example.com/file.bin',
                Path(path),
                total_size=6,
                pbar=pbar,
                speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                progress_lock=Lock(),
            )

            self.assertTrue(result)
            self.assertEqual(Path(path).read_bytes(), b'abcdef')
            self.assertEqual(pbar.updates, [3, 3])
            self.assertEqual(response.iter_content_chunk_sizes, [128 * 1024])
            _, kwargs = session.get_calls[0]
            self.assertTrue(kwargs['stream'])
            self.assertEqual(kwargs['timeout'], (15, 60))
            self.assertNotIn('Range', kwargs['headers'])
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_single_thread_resume_appends_on_206(self):
        """单线程收到 206 时追加续传。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
            temp_file.write(b'abc')
        response = FakeResponse(
            status_code=206,
            headers={'Content-Range': 'bytes 3-5/6'},
            chunks=[b'def'],
        )
        session = FakeSession(get_responses=[response])
        pbar = FakeProgressBar()
        pbar.n = 3

        try:
            result = self.dm._download_single_threaded(
                session,
                'https://example.com/file.bin',
                Path(path),
                total_size=6,
                pbar=pbar,
                speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                progress_lock=Lock(),
            )

            self.assertTrue(result)
            self.assertEqual(Path(path).read_bytes(), b'abcdef')
            _, kwargs = session.get_calls[0]
            self.assertEqual(kwargs['headers']['Range'], 'bytes=3-')
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_single_thread_200_overwrites_existing_file_and_resets_progress(self):
        """服务端忽略 Range 返回 200 时覆盖重下。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
            temp_file.write(b'old')
        response = FakeResponse(status_code=200, headers={'Content-Length': '3'}, chunks=[b'new'])
        session = FakeSession(get_responses=[response])
        pbar = FakeProgressBar()
        pbar.n = 3

        try:
            result = self.dm._download_single_threaded(
                session,
                'https://example.com/file.bin',
                Path(path),
                total_size=0,
                pbar=pbar,
                speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                progress_lock=Lock(),
            )

            self.assertTrue(result)
            self.assertEqual(Path(path).read_bytes(), b'new')
            self.assertEqual(pbar.n, 3)
            self.assertEqual(pbar.total, 3)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_single_thread_closes_progress_when_integrated_download_fails(self):
        """集成入口失败时关闭进度条。"""
        pbar = FakeProgressBar()
        response = FakeResponse(status_code=500)
        session = FakeSession(head_response=FakeResponse(status_code=500), get_responses=[response, response, response])

        with mock.patch('modules.download_manager.requests.Session', return_value=session), \
                mock.patch('modules.download_manager.create_download_progress_bar', return_value=pbar):
            result = self.dm.download_file_with_progress('https://example.com/file.bin', 'missing-dir/file.bin')

        self.assertFalse(result)
        self.assertTrue(pbar.closed)


if __name__ == '__main__':
    unittest.main()
