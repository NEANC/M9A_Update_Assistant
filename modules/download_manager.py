#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging

from pathlib import Path

from pypdl import Pypdl

from modules.progress_bar import format_ok, format_error


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
