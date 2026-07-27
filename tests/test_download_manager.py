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

from modules.download_manager import DownloadManager, DownloadSegment, NetworkSpeedMeter


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

    def test_download_bar_format_does_not_use_rate_fmt(self):
        """下载进度条不使用 tqdm 推导速度字段。"""
        from modules.progress_bar import DOWNLOAD_BAR_FORMAT

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


class TestDownloadFileIntegration(unittest.TestCase):
    """download_file_with_progress 集成语义测试。"""

    def setUp(self):
        """初始化测试对象。"""
        self.logger = logging.getLogger('TestDownloadIntegration')
        self.logger.setLevel(logging.CRITICAL)

    def test_download_manager_does_not_import_pypdl(self):
        """下载模块不再导入 Pypdl。"""
        import modules.download_manager as download_manager

        self.assertFalse(hasattr(download_manager, 'Pypdl'))

    def test_download_file_uses_single_thread_fallback_when_head_has_no_range(self):
        """HEAD 不支持 Range 时集成入口使用单线程下载。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            session = FakeSession(
                head_response=FakeResponse(headers={'Content-Length': '6'}),
                get_responses=[FakeResponse(200, {'Content-Length': '6'}, [b'abc', b'def'])],
            )
            pbar = FakeProgressBar()
            dm = DownloadManager('', temp_dir, self.logger, download_threads=4)

            with mock.patch('modules.download_manager.requests.Session', return_value=session), \
                    mock.patch('modules.download_manager.create_download_progress_bar', return_value=pbar):
                result = dm.download_file_with_progress('https://example.com/file.bin', str(save_path))

            self.assertTrue(result)
            self.assertEqual(save_path.read_bytes(), b'abcdef')
            self.assertTrue(pbar.closed)
            self.assertEqual(session.get_calls[0][1]['headers']['User-Agent'], 'M9A-Update-Assistant')

    def test_download_file_uses_multithread_when_range_supported(self):
        """HEAD 支持 Range 时集成入口使用多线程下载。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            metadata_session = FakeSession(
                head_response=FakeResponse(headers={'Content-Length': '6', 'Accept-Ranges': 'bytes'}),
            )
            part_sessions = [
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 0-2/6'}, [b'abc'])]),
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 3-5/6'}, [b'def'])]),
            ]
            dm = DownloadManager('', temp_dir, self.logger, download_threads=2)

            with mock.patch('modules.download_manager.requests.Session', side_effect=[metadata_session] + part_sessions), \
                    mock.patch('modules.download_manager.create_download_progress_bar', return_value=FakeProgressBar()):
                result = dm.download_file_with_progress('https://example.com/file.bin', str(save_path))

            self.assertTrue(result)
            self.assertEqual(save_path.read_bytes(), b'abcdef')

    def test_download_file_passes_proxy_to_requests(self):
        """代理参数传递给 requests。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            session = FakeSession(
                head_response=FakeResponse(status_code=500),
                get_responses=[FakeResponse(200, {'Content-Length': '3'}, [b'abc'])],
            )
            dm = DownloadManager('socks5://127.0.0.1:10809', temp_dir, self.logger, download_threads=1)

            with mock.patch('modules.download_manager.requests.Session', return_value=session), \
                    mock.patch('modules.download_manager.create_download_progress_bar', return_value=FakeProgressBar()):
                result = dm.download_file_with_progress('https://example.com/file.bin', str(save_path))

            self.assertTrue(result)
            self.assertEqual(
                session.get_calls[0][1]['proxies'],
                {'http': 'socks5://127.0.0.1:10809', 'https': 'socks5://127.0.0.1:10809'},
            )


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

    def test_single_thread_416_file_complete_returns_true(self):
        """416 且文件完整时返回 True（早期守卫直接返回，无需发请求）。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
            temp_file.write(b'data')
        response = FakeResponse(status_code=416)
        session = FakeSession(get_responses=[response])
        pbar = FakeProgressBar()

        try:
            result = self.dm._download_single_threaded(
                session,
                'https://example.com/file.bin',
                Path(path),
                total_size=4,
                pbar=pbar,
                speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                progress_lock=Lock(),
            )

            self.assertTrue(result)
            # 文件已完整，早期守卫直接返回 True，无需发起 GET 请求
            self.assertEqual(len(session.get_calls), 0)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_single_thread_416_file_incomplete_returns_false_no_retry(self):
        """416 且文件不完整时立即返回 False 且仅发一次请求。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
            temp_file.write(b'partial')
        response = FakeResponse(status_code=416)
        # 准备 3 个响应（对应 DOWNLOAD_RETRIES），验证不会被消费
        session = FakeSession(get_responses=[response, response, response])
        pbar = FakeProgressBar()

        try:
            result = self.dm._download_single_threaded(
                session,
                'https://example.com/file.bin',
                Path(path),
                total_size=100,
                pbar=pbar,
                speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                progress_lock=Lock(),
            )

            self.assertFalse(result)
            self.assertEqual(len(session.get_calls), 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_single_thread_unknown_size_success_when_file_non_empty(self):
        """未知总大小时文件存在且非空即成功。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
        os.unlink(path)
        response = FakeResponse(status_code=200, headers={}, chunks=[b'hello', b'world'])
        session = FakeSession(get_responses=[response])
        pbar = FakeProgressBar()

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
            self.assertEqual(Path(path).read_bytes(), b'helloworld')
            self.assertGreater(Path(path).stat().st_size, 0)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_single_thread_unknown_size_416_is_failure(self):
        """未知总大小收到 416 不视为成功。"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as temp_file:
            path = temp_file.name
        os.unlink(path)
        response = FakeResponse(status_code=416)
        session = FakeSession(get_responses=[response])
        pbar = FakeProgressBar()

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

            self.assertFalse(result)
            self.assertEqual(len(session.get_calls), 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestMultithreadDownload(unittest.TestCase):
    """多线程分段下载测试。"""

    def setUp(self):
        """初始化测试对象。"""
        self.logger = logging.getLogger('TestMultithreadDownload')
        self.logger.setLevel(logging.CRITICAL)
        self.dm = DownloadManager('', '/tmp', self.logger, download_threads=2)

    def test_download_part_uses_range_and_writes_part_file(self):
        """分段下载使用 Range 并写入 part 文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            segment = DownloadSegment(index=0, start=0, end=2)
            response = FakeResponse(
                status_code=206,
                headers={'Content-Range': 'bytes 0-2/6'},
                chunks=[b'abc'],
            )
            session = FakeSession(get_responses=[response])
            pbar = FakeProgressBar()

            result = self.dm._download_part(
                session,
                'https://example.com/file.bin',
                save_path,
                segment,
                pbar,
                NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                Lock(),
            )

            self.assertTrue(result)
            self.assertEqual((Path(str(save_path) + '.part0')).read_bytes(), b'abc')
            _, kwargs = session.get_calls[0]
            self.assertEqual(kwargs['headers']['Range'], 'bytes=0-2')
            self.assertEqual(response.iter_content_chunk_sizes, [128 * 1024])

    def test_download_part_retries_when_content_range_mismatch(self):
        """Content-Range 不一致时不写入并重试。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            segment = DownloadSegment(index=0, start=0, end=2)
            bad_response = FakeResponse(
                status_code=206,
                headers={'Content-Range': 'bytes 1-3/6'},
                chunks=[b'bad'],
            )
            good_response = FakeResponse(
                status_code=206,
                headers={'Content-Range': 'bytes 0-2/6'},
                chunks=[b'abc'],
            )
            session = FakeSession(get_responses=[bad_response, good_response])

            result = self.dm._download_part(
                session,
                'https://example.com/file.bin',
                save_path,
                segment,
                FakeProgressBar(),
                NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                Lock(),
            )

            self.assertTrue(result)
            self.assertEqual((Path(str(save_path) + '.part0')).read_bytes(), b'abc')
            self.assertEqual(len(session.get_calls), 2)

    def test_merge_parts_writes_in_order_keeps_parts(self):
        """part 按顺序合并但不在 _merge_parts 内删除，删除由调用方负责。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            Path(str(save_path) + '.part0').write_bytes(b'abc')
            Path(str(save_path) + '.part1').write_bytes(b'def')
            segments = [DownloadSegment(0, 0, 2), DownloadSegment(1, 3, 5)]

            result = self.dm._merge_parts(save_path, segments)

            self.assertTrue(result)
            self.assertEqual(save_path.read_bytes(), b'abcdef')
            self.assertTrue(Path(str(save_path) + '.part0').exists())
            self.assertTrue(Path(str(save_path) + '.part1').exists())

    def test_multithread_download_combines_parts(self):
        """多线程模式完成后生成目标文件并删除 part 文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            sessions = [
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 0-2/6'}, [b'abc'])]),
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 3-5/6'}, [b'def'])]),
            ]

            with mock.patch('modules.download_manager.requests.Session', side_effect=sessions):
                result = self.dm._download_multithreaded(
                    'https://example.com/file.bin',
                    save_path,
                    total_size=6,
                    pbar=FakeProgressBar(),
                    speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                    progress_lock=Lock(),
                )

            self.assertTrue(result)
            self.assertEqual(save_path.read_bytes(), b'abcdef')
            self.assertFalse(Path(str(save_path) + '.part0').exists())
            self.assertFalse(Path(str(save_path) + '.part1').exists())
            self.assertEqual(sessions[0].get_calls[0][1]['headers']['Range'], 'bytes=0-2')
            self.assertEqual(sessions[1].get_calls[0][1]['headers']['Range'], 'bytes=3-5')

    def test_part_larger_than_segment_is_deleted_and_redownloaded(self):
        """part 大于分段长度时删除重下。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            segment = DownloadSegment(index=0, start=0, end=2)
            part_path = Path(str(save_path) + '.part0')
            part_path.write_bytes(b'too-large-data')
            self.assertGreater(part_path.stat().st_size, segment.length)

            response = FakeResponse(
                status_code=206,
                headers={'Content-Range': 'bytes 0-2/6'},
                chunks=[b'abc'],
            )
            session = FakeSession(get_responses=[response])
            pbar = FakeProgressBar()

            result = self.dm._download_part(
                session,
                'https://example.com/file.bin',
                save_path,
                segment,
                pbar,
                NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                Lock(),
            )

            self.assertTrue(result)
            self.assertEqual(part_path.read_bytes(), b'abc')
            self.assertEqual(part_path.stat().st_size, segment.length)

    def test_multithread_failure_keeps_completed_part_files(self):
        """多线程失败时保留已完成 part 文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            part0_path = Path(str(save_path) + '.part0')
            part1_path = Path(str(save_path) + '.part1')

            good_session = FakeSession(
                get_responses=[FakeResponse(206, {'Content-Range': 'bytes 0-2/6'}, [b'abc'])]
            )
            bad_session = FakeSession(
                get_responses=[FakeResponse(206, {'Content-Range': 'bytes 1-5/6'}, [b'bad'])] * 3
            )

            sessions = [good_session, bad_session]
            dm = DownloadManager('', temp_dir, self.logger, download_threads=2)

            with mock.patch('modules.download_manager.requests.Session', side_effect=sessions):
                result = dm._download_multithreaded(
                    'https://example.com/file.bin',
                    save_path,
                    total_size=6,
                    pbar=FakeProgressBar(),
                    speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                    progress_lock=Lock(),
                )

            self.assertFalse(result)
            self.assertTrue(part0_path.exists())
            self.assertEqual(part0_path.read_bytes(), b'abc')

    def test_requests_session_is_not_shared_between_part_workers(self):
        """每个分段 worker 使用独立 Session。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'file.bin'
            dm = DownloadManager('', temp_dir, self.logger, download_threads=3)

            sessions = [
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 0-1/6'}, [b'ab'])]),
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 2-3/6'}, [b'cd'])]),
                FakeSession(get_responses=[FakeResponse(206, {'Content-Range': 'bytes 4-5/6'}, [b'ef'])]),
            ]

            with mock.patch('modules.download_manager.requests.Session', side_effect=sessions) as mock_session:
                result = dm._download_multithreaded(
                    'https://example.com/file.bin',
                    save_path,
                    total_size=6,
                    pbar=FakeProgressBar(),
                    speed_meter=NetworkSpeedMeter(time_func=lambda: time.monotonic()),
                    progress_lock=Lock(),
                )

            self.assertTrue(result)
            self.assertEqual(mock_session.call_count, 3)
            for session in sessions:
                self.assertEqual(len(session.get_calls), 1)
                self.assertEqual(session.get_calls[0][1]['headers']['User-Agent'], 'M9A-Update-Assistant')


if __name__ == '__main__':
    unittest.main()
