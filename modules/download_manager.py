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

    def _extract_total_size_from_get_response(self, response, existing_size: int) -> int:
        """从 GET 响应头推导完整文件大小。"""
        content_range = response.headers.get('Content-Range', '')
        if content_range.startswith('bytes ') and '/' in content_range:
            total_part = content_range.rsplit('/', 1)[1]
            if total_part.isdigit():
                return int(total_part)
        content_length = response.headers.get('Content-Length')
        if response.status_code == 200 and content_length and content_length.isdigit():
            return int(content_length)
        if response.status_code == 206 and content_length and content_length.isdigit():
            return existing_size + int(content_length)
        return 0

    def _update_progress(self, pbar, progress_lock: Lock, speed_meter: NetworkSpeedMeter,
                         byte_count: int) -> None:
        """更新进度条和网络速度。"""
        with progress_lock:
            speed = speed_meter.update(byte_count)
            pbar.update(byte_count)
            pbar.set_postfix_str(self._format_speed(speed), refresh=False)
            pbar.refresh()

    def _download_single_threaded(self, session, url: str, target_path: Path,
                                  total_size: int, pbar, speed_meter: NetworkSpeedMeter,
                                  progress_lock: Lock) -> bool:
        """单线程下载，支持续传和重试。

        Args:
            session: requests Session。
            url: 下载 URL。
            target_path: 目标文件路径。
            total_size: 已知总大小（0 表示未知）。
            pbar: tqdm 进度条实例。
            speed_meter: 网速统计器。
            progress_lock: 进度更新锁。

        Returns:
            bool: 是否下载成功。
        """
        target = Path(target_path)

        # 已知总大小且文件已完整
        if total_size > 0 and target.exists() and target.stat().st_size == total_size:
            return True

        existing_size = 0
        if target.exists():
            existing_size = target.stat().st_size

        # 本地文件大于已知总大小，覆盖重下
        if total_size > 0 and existing_size >= total_size:
            existing_size = 0

        headers = {'User-Agent': USER_AGENT}
        if existing_size > 0:
            headers['Range'] = f'bytes={existing_size}-'

        for attempt in range(DOWNLOAD_RETRIES):
            try:
                response = session.get(
                    url,
                    headers=headers,
                    timeout=DOWNLOAD_TIMEOUT,
                    proxies=self._build_proxies(),
                    stream=True,
                )

                if response.status_code == 206:
                    response.raise_for_status()
                    # 从 Content-Range 推导真实 total 并更新进度条
                    real_total = self._extract_total_size_from_get_response(
                        response, existing_size,
                    )
                    if real_total > 0:
                        with progress_lock:
                            pbar.total = real_total
                            pbar.refresh()
                    # 续传追加
                    with open(target, 'ab') as f:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                self._update_progress(pbar, progress_lock, speed_meter, len(chunk))

                    if total_size == 0:
                        return target.exists() and target.stat().st_size > 0
                    return True

                elif response.status_code == 200:
                    response.raise_for_status()
                    # 覆盖从头下载（进度条复位需在锁保护下）
                    content_length = response.headers.get('Content-Length')
                    with progress_lock:
                        pbar.n = 0
                        if content_length and content_length.isdigit():
                            pbar.total = int(content_length)
                        pbar.refresh()

                    with open(target, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                self._update_progress(pbar, progress_lock, speed_meter, len(chunk))

                    if total_size == 0:
                        return target.exists() and target.stat().st_size > 0
                    return True

                elif response.status_code == 416:
                    # 416 不在 raise_for_status 覆盖范围，需手动处理
                    if total_size > 0 and target.exists() and target.stat().st_size == total_size:
                        return True
                    # 416 且文件不完整，相同 Range 头必然再次 416，不重试
                    return False

                else:
                    response.raise_for_status()

            except requests.RequestException as exc:
                self.logger.debug(f"下载尝试 {attempt + 1} 失败: {exc}")

            if attempt < DOWNLOAD_RETRIES - 1:
                time.sleep(1)

        return False

    def download_file_with_progress(self, url: str, save_path: str) -> bool:
        """下载文件并显示进度条。

        Args:
            url: 下载 URL。
            save_path: 保存路径。

        Returns:
            bool: 操作是否成功。
        """
        file_name = Path(url).name
        self.logger.info(f"开始下载文件: {file_name}")
        self.logger.debug(f"下载 URL: {url}")

        pbar = None
        try:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)

            metadata_session = requests.Session()
            metadata = self._get_download_metadata(metadata_session, url)
            metadata_session.close()

            target = Path(save_path)
            existing_size = target.stat().st_size if target.exists() else 0
            pbar_total = metadata.total_size if metadata.total_size > 0 else existing_size
            pbar = create_download_progress_bar(
                total=pbar_total,
                desc=f"下载 {file_name}",
            )
            pbar.n = existing_size
            pbar.refresh()

            speed_meter = NetworkSpeedMeter()
            progress_lock = Lock()
            download_session = requests.Session()

            success = self._download_single_threaded(
                download_session,
                url,
                target,
                metadata.total_size,
                pbar,
                speed_meter,
                progress_lock,
            )
            download_session.close()

            if success:
                downloaded_size = target.stat().st_size
                print(format_ok("下载", file_name, save_path, downloaded_size))
                self.logger.debug(
                    f"下载完成，文件大小: {downloaded_size / (1024 * 1024):.2f} MB，"
                    f"保存路径: {save_path}"
                )
                return True
            else:
                print(format_error(f"下载 {file_name}", "下载失败"))
                return False
        except Exception as e:
            self.logger.error(f"下载文件时发生错误: {e}")
            print(format_error(f"下载 {file_name}", str(e)))
            return False
        finally:
            if pbar is not None:
                pbar.close()
