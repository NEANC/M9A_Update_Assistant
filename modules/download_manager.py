#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import time
import requests

from pathlib import Path

from modules.progress_bar import (
    tqdm, BAR_FORMAT,
    format_ok, format_error,
)


class DownloadManager:
    """下载管理器，负责文件下载与缓存检查"""

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
            file_name = Path(url).name
            if attempt == 0:
                self.logger.info(f"开始下载文件: {file_name}")
                self.logger.debug(f"下载 URL: {url}")
            else:
                self.logger.info(f"重试下载文件（{attempt}/{max_retries - 1}）: {file_name}")

            try:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)

                headers = {'User-Agent': 'M9A-Update-Assistant'}
                proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None

                with requests.get(url, headers=headers, proxies=proxies, timeout=60, stream=True) as response:
                    response.raise_for_status()

                    total_size = int(response.headers.get('Content-Length', 0))
                    downloaded_size = 0

                    if total_size > 0:
                        self.logger.debug(f"获取到文件大小: {total_size / (1024 * 1024):.2f} MB")

                    with open(save_path, 'wb') as f:
                        chunk_size = 1048576
                        with tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024,
                                   desc=f"下载 {file_name}", bar_format=BAR_FORMAT,
                                   disable=total_size <= 0, leave=False) as pbar:
                            try:
                                for chunk in response.iter_content(chunk_size=chunk_size):
                                    if chunk:
                                        f.write(chunk)
                                        chunk_len = len(chunk)
                                        downloaded_size += chunk_len
                                        if total_size > 0:
                                            pbar.update(chunk_len)
                            except Exception:
                                pbar.leave = True  # 错误时保留进度条
                                raise

                    # ── 完成提示（亮绿色） ──
                    print(format_ok("下载", file_name, save_path, downloaded_size))

                    self.logger.debug(f"下载完成，文件大小: {downloaded_size / (1024 * 1024):.2f} MB，保存路径: {save_path}")
                    return True
            except requests.RequestException as e:
                self.logger.error(f"下载文件失败: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    continue
                else:
                    print(format_error(f"下载 {file_name}", str(e)))
                    return False
            except Exception as e:
                self.logger.error(f"下载文件时发生错误: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    continue
                else:
                    print(format_error(f"下载 {file_name}", str(e)))
                    return False

        return False
