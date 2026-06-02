#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import time
import requests

from pathlib import Path


class DownloadManager:
    """下载管理器，负责文件下载、进度显示、缓存检查"""

    def __init__(self, proxy: str, temp_folder: str, logger: logging.Logger):
        """
        初始化下载管理器

        Args:
            proxy: 代理地址
            temp_folder: 临时文件夹路径
            logger: 日志记录器
        """
        self.proxy = proxy
        self.temp_folder = temp_folder
        self.logger = logger
        self._last_progress_time = 0.0

    def print_progress(self, prefix: str, progress: float, current_mb: float, total_mb: float) -> None:
        """打印进度条到控制台，内置 200ms 节流"""
        now = time.monotonic()
        if now - self._last_progress_time < 0.2 and progress < 100.0:
            return
        self._last_progress_time = now
        print(f"\r{prefix}: {progress:.1f}% ({current_mb:.2f} MB / {total_mb:.2f} MB)", end="", flush=True)

    @staticmethod
    def clear_progress_line() -> None:
        """清除控制台当前行的进度条输出"""
        print("\r" + " " * 80 + "\r", end="", flush=True)

    def reset_progress_timer(self) -> None:
        """重置进度节流计时器"""
        self._last_progress_time = 0.0

    def download_file_with_progress(self, url: str, save_path: str) -> bool:
        """
        下载文件并显示进度

        Args:
            url: 下载 URL
            save_path: 保存路径

        Returns:
            bool: 操作是否成功
        """
        max_retries = 4
        retry_interval = 10

        for attempt in range(max_retries):
            if attempt == 0:
                self.logger.info(f"开始下载文件: {url}")
            else:
                self.logger.info(f"重试下载文件（{attempt}/{max_retries - 1}）: {url}")

            try:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)

                headers = {'User-Agent': 'M9A-Update-Assistant'}
                proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None

                with requests.get(url, headers=headers, proxies=proxies, timeout=60, stream=True) as response:
                    response.raise_for_status()

                    total_size = int(response.headers.get('Content-Length', 0))
                    downloaded_size = 0

                    if total_size > 0:
                        self.logger.info(f"获取到文件大小: {total_size / (1024 * 1024):.2f} MB")

                    with open(save_path, 'wb') as f:
                        chunk_size = 1048576
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)
                                downloaded_size += len(chunk)

                                if total_size > 0:
                                    self.print_progress("下载进度", (downloaded_size / total_size) * 100,
                                                        downloaded_size / (1024 * 1024), total_size / (1024 * 1024))

                    self.clear_progress_line()
                    self.reset_progress_timer()

                    self.logger.info(f"下载完成，文件大小: {downloaded_size / (1024 * 1024):.2f} MB，保存路径: {save_path}")
                    return True
            except requests.RequestException as e:
                self.logger.error(f"下载文件失败: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    continue
                else:
                    return False
            except Exception as e:
                self.logger.error(f"下载文件时发生错误: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    continue
                else:
                    return False

        return False
