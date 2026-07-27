#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import time

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import requests

from modules.progress_bar import create_download_progress_bar, format_ok, format_error

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
DOWNLOAD_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
DOWNLOAD_RETRIES = 3
CHUNK_SIZE = 128 * 1024
USER_AGENT = 'M9A-Update-Assistant'


@dataclass(frozen=True)
class DownloadMetadata:
    """下载元数据。"""

    total_size: int
    supports_range: bool


@dataclass(frozen=True)
class DownloadSegment:
    """下载分段闭区间。"""

    index: int
    start: int
    end: int

    @property
    def length(self) -> int:
        """返回分段字节长度。"""
        return self.end - self.start + 1


class NetworkSpeedMeter:
    """统计网络层 chunk 到达速度。"""

    def __init__(self, time_func=time.monotonic):
        """初始化速度统计器。"""
        self.time_func = time_func
        self.started_at = time_func()
        self.samples = deque()
        self.total_bytes = 0

    def update(self, byte_count: int) -> float:
        """记录网络收到的字节数并返回 bytes/s。"""
        now = self.time_func()
        self.total_bytes += byte_count
        self.samples.append((now, byte_count))
        while self.samples and now - self.samples[0][0] > 1:
            self.samples.popleft()
        window_bytes = sum(size for _, size in self.samples)
        window_seconds = now - self.samples[0][0] if self.samples else 0
        if window_seconds > 0:
            return window_bytes / window_seconds
        elapsed = max(now - self.started_at, 0.001)
        return self.total_bytes / elapsed


class DownloadManager:
    """下载管理器，负责文件下载与缓存检查"""

    def __init__(self, proxy: str, temp_folder: str, logger: logging.Logger,
                 download_threads: int = 4):
        """
        初始化下载管理器

        Args:
            proxy: 代理地址
            temp_folder: 临时文件夹路径
            logger: 日志记录器
            download_threads: 下载线程数
        """
        self.proxy = proxy
        self.temp_folder = temp_folder
        self.logger = logger
        self.download_threads = download_threads

    def _build_proxies(self):
        """构建 requests 代理参数。"""
        if not self.proxy:
            return None
        return {'http': self.proxy, 'https': self.proxy}

    def _get_download_metadata(self, session, url: str) -> DownloadMetadata:
        """通过 HEAD 获取下载元数据。"""
        try:
            response = session.head(
                url,
                headers={'User-Agent': USER_AGENT},
                timeout=DOWNLOAD_TIMEOUT,
                proxies=self._build_proxies(),
                allow_redirects=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.debug(f"HEAD 探测失败，降级单线程下载: {exc}")
            return DownloadMetadata(total_size=0, supports_range=False)

        try:
            total_size = int(response.headers.get('Content-Length', '0'))
        except ValueError:
            total_size = 0
        supports_range = response.headers.get('Accept-Ranges', '').lower() == 'bytes'
        return DownloadMetadata(total_size=max(total_size, 0), supports_range=supports_range)

    def _split_segments(self, total_size: int, threads: int) -> list[DownloadSegment]:
        """将文件大小拆分为闭区间分段。"""
        segment_count = min(max(threads, 1), total_size)
        base_size, remainder = divmod(total_size, segment_count)
        segments = []
        start = 0
        for index in range(segment_count):
            length = base_size + (1 if index < remainder else 0)
            end = start + length - 1
            segments.append(DownloadSegment(index, start, end))
            start = end + 1
        return segments

    def _format_speed(self, bytes_per_second: float) -> str:
        """格式化网络下载速度。"""
        if bytes_per_second < 1024:
            return f"{bytes_per_second:.2f}B/s"
        if bytes_per_second < 1024 * 1024:
            return f"{bytes_per_second / 1024:.2f}KiB/s"
        return f"{bytes_per_second / 1024 / 1024:.2f}MiB/s"

    def download_file_with_progress(self, url: str, save_path: str) -> bool:
        """使用 Pypdl 下载文件并保持现有返回值语义。

        Args:
            url: 下载 URL。
            save_path: 保存路径。

        Returns:
            bool: 操作是否成功。
        """
        file_name = Path(url).name
        self.logger.info(f"开始下载文件: {file_name}")
        self.logger.debug(f"下载 URL: {url}")

        try:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            threads = self.download_threads if isinstance(self.download_threads, int) else 4
            multisegment = threads >= 2
            segments = threads if multisegment else 1

            downloader = Pypdl()
            kwargs = {
                'url': url,
                'file_path': save_path,
                'segments': segments,
                'multisegment': multisegment,
                'retries': 0,
                'display': True,
                'headers': {'User-Agent': 'M9A-Update-Assistant'},
            }
            if self.proxy:
                kwargs['proxy'] = self.proxy

            downloader.start(**kwargs)

            failed = getattr(downloader, 'failed', None)
            if failed:
                message = f"Pypdl 下载存在失败项: {failed}"
                self.logger.error(message)
                print(format_error(f"下载 {file_name}", message))
                return False

            if not Path(save_path).exists():
                message = f"下载完成但目标文件不存在: {save_path}"
                self.logger.error(message)
                print(format_error(f"下载 {file_name}", message))
                return False

            downloaded_size = Path(save_path).stat().st_size
            print(format_ok("下载", file_name, save_path, downloaded_size))
            self.logger.debug(f"下载完成，文件大小: {downloaded_size / (1024 * 1024):.2f} MB，保存路径: {save_path}")
            return True
        except Exception as e:
            self.logger.error(f"下载文件时发生错误: {e}")
            print(format_error(f"下载 {file_name}", str(e)))
            return False
