#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import os
import re
import sys
import time
import zlib
import shutil
import logging
import zipfile
import requests
import configparser

from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List


LITE_ZIP_PATTERN = 'M9A-win-x86_64-v*-Lite.zip'
FULL_ZIP_PATTERN = 'M9A-win-x86_64-v*-Full.zip'
VERSION = "v1.6.0"


def print_info():
    """打印程序的版本和版权信息，发版前手动修改。"""
    print("\n")
    print("+ " + " M9A Update Assistant ".center(60, "="), "+")
    print("||" + "".center(60, " ") + "||")
    print("||" + "M9A CLI 更新小助手".center(55, " ") + "||")
    print("||" + "本项目使用 AI 进行生成".center(51, " ") + "||")
    print("||" + "".center(60, " ") + "||")
    print("|| " + "".center(58, "-") + " ||")
    print("||" + "".center(60, " ") + "||")
    print("||" + f"Version: {VERSION}    License: WTFPL".center(60, " ") + "||")
    print("||" + "".center(60, " ") + "||")
    print("+ " + "".center(60, "=") + " +")
    print("\n")


class M9AUpdateAssistant:
    """M9A 更新类，用于处理 M9A 的更新操作"""

    def __init__(self, config_file: str = "config.ini"):
        """
        初始化 M9A 更新助手

        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        self.logger = self._setup_logger()
        self._load_config()

    def _setup_logger(self) -> logging.Logger:
        """
        设置日志记录器

        Returns:
            配置好的日志记录器
        """
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter('%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%H:%M:%S')

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        return logger

    def _generate_default_config(self) -> None:
        """
        生成默认配置文件
        """
        default_config = r"""[Paths]
# M9A 文件夹路径（多个路径用逗号分隔）
m9a_folders = Z:\M9A

# 临时文件夹路径
temp_folder = Z:\Temp\M9A-Update-Assistant

[Logs]
# 是否保存日志文件
save_enabled = false

# 最大日志文件数量（超过此数量的旧日志将被删除）
max_files = 15

[GitHub]
# GitHub 仓库地址（格式：用户名/仓库名）
repo = MAA1999/M9A

# 是否下载 Full 版本（用于提取 deps 文件夹）
# 如果 Lite 版本已包含 deps，则无需下载 Full 版本
full_download_enabled = true

# 代理服务器地址（例如：http://127.0.0.1:7890 或 socks5://127.0.0.1:1080）
# 留空表示不使用代理
proxy =

# Release 版本选择
# release: 使用最新正式版（https://github.com/MAA1999/M9A/releases）
# latest: 使用带有 latest 标签的版本（https://github.com/MAA1999/M9A/releases/latest）
release_version = release
"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                f.write(default_config)
            print(f"已生成默认配置文件: {self.config_file}")
            print("请修改配置文件后重新运行程序。")
            sys.exit(0)
        except IOError as e:
            print(f"生成配置文件失败: {e}")
            sys.exit(1)

    def _load_config(self) -> None:
        """加载配置文件"""
        if not os.path.exists(self.config_file):
            print(f"配置文件 {self.config_file} 不存在，将生成默认配置文件")
            self._generate_default_config()

        self.config.read(self.config_file, encoding='utf-8')

        m9a_folders_str = self.config.get('Paths', 'm9a_folders')

        if m9a_folders_str:
            self.m9a_folders = [folder.strip() for folder in m9a_folders_str.split(',') if folder.strip()]
        else:
            self.m9a_folders = []

        temp_folder_config = self.config.get('Paths', 'temp_folder', fallback='Temp')

        if temp_folder_config == 'Temp':
            # 使用程序目录的 Temp 文件夹
            self.temp_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Temp')
        else:
            self.temp_folder = temp_folder_config
        self.lite_zip_pattern = LITE_ZIP_PATTERN
        self.full_zip_pattern = FULL_ZIP_PATTERN
        self.log_max_files = self.config.getint('Logs', 'max_files', fallback=15)
        self.log_save_enabled = self.config.getboolean('Logs', 'save_enabled', fallback=True)

        self.github_repo = self.config.get('GitHub', 'repo', fallback='MAA1999/M9A')
        self.github_release_version = self.config.get('GitHub', 'release_version', fallback='release')
        self.github_proxy = self.config.get('GitHub', 'proxy', fallback='').strip()
        self.github_full_download_enabled = self.config.getboolean('GitHub', 'full_download_enabled', fallback=True)

        if self.github_proxy:
            self.logger.info(f"已配置代理: {self.github_proxy}")
        else:
            self.logger.info("未配置代理，若遇到网络问题请配置代理")

        self.logger.info(f"Release 版本: {self.github_release_version}")

        if self.log_save_enabled:
            self._setup_file_logger()

    def validate_config(self) -> bool:
        """
        验证配置文件是否合法

        Returns:
            bool: 配置是否合法
        """
        # 验证 m9a_folders
        if not self.m9a_folders:
            self.logger.error("配置错误: M9A 文件夹路径未配置")
            self.logger.error("请在配置文件中设置 m9a_folders 字段")
            return False

        # 验证 m9a_folders 中的路径
        for folder in self.m9a_folders:
            if not os.path.exists(folder):
                self.logger.warning(f"M9A 文件夹路径不存在: {folder}")
                self.logger.warning("程序将尝试创建该文件夹")

        # 验证 temp_folder
        try:
            temp_path = Path(self.temp_folder)
            temp_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"临时文件夹路径: {self.temp_folder}")
        except Exception as e:
            self.logger.error(f"临时文件夹路径错误: {e}")
            return False

        # 验证 GitHub 配置
        if not self.github_repo:
            self.logger.error("配置错误: GitHub 仓库地址未配置")
            return False

        if self.github_release_version not in ['release', 'latest']:
            self.logger.error(f"配置错误: 未知的 Release 版本类型: {self.github_release_version}")
            return False

        self.logger.info("配置验证通过")
        return True

    def _setup_file_logger(self) -> None:
        """设置文件日志记录器"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        log_file = log_dir / f"M9A_Update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%H:%M:%S')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        self.logger.info(f"日志文件已创建: {log_file}")

    def _cleanup_old_logs(self) -> None:
        """
        清理多余的日志文件
        只保留最近的 N 个日志文件，不处理过期文件
        """
        if not self.log_save_enabled:
            return

        log_dir = Path("logs")
        if not log_dir.exists():
            return

        log_files = list(log_dir.glob("M9A_Update_*.log"))
        
        if len(log_files) <= self.log_max_files:
            return

        # 按修改时间排序（旧的在前）
        log_files.sort(key=lambda x: x.stat().st_mtime)

        # 删除多余的文件
        files_to_delete = log_files[:-self.log_max_files]
        for log_file in files_to_delete:
            try:
                log_file.unlink()
                self.logger.info(f"已删除多余的日志文件: {log_file}")
            except Exception as e:
                self.logger.warning(f"删除日志文件 {log_file} 失败: {e}")

    def backup_config(self, m9a_folder: str) -> bool:
        """
        备份 config 文件夹到临时文件夹

        将 M9A 文件夹中的 config 文件夹复制到临时文件夹，以便在更新完成后恢复配置。

        Args:
            m9a_folder: M9A 文件夹路径

        Returns:
            bool: 操作是否成功

        Raises:
            IOError: 文件操作错误
            OSError: 操作系统错误
            shutil.Error: shutil 模块操作错误
        """
        m9a_config_path = Path(m9a_folder) / "config"
        # 为每个 M9A 路径创建独立的备份目录
        # 使用路径的哈希值作为目录名，确保唯一性
        path_hash = zlib.crc32(m9a_folder.encode()) & 0xffffffff
        temp_config_path = Path(self.temp_folder) / f"config_{path_hash:08x}"

        if not m9a_config_path.exists():
            self.logger.warning(f"M9A 文件夹中的 config 文件夹不存在: {m9a_config_path}")
            return False

        try:
            Path(self.temp_folder).mkdir(parents=True, exist_ok=True)

            # 使用 dirs_exist_ok=True 简化代码，避免先删除再复制
            shutil.copytree(m9a_config_path, temp_config_path, dirs_exist_ok=True)
            self.logger.info(f"config 文件夹已备份到: {temp_config_path}")
            return True
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"备份 config 文件夹失败: {e}")
            return False

    def clean_m9a_folder(self, m9a_folder: str) -> bool:
        """
        清理 M9A 文件夹中的所有文件

        删除 M9A 文件夹中的所有文件和子文件夹，为解压新的版本做准备。
        如果 M9A 文件夹不存在，则创建它。

        Args:
            m9a_folder: M9A 文件夹路径

        Returns:
            bool: 操作是否成功

        Raises:
            IOError: 文件操作错误
            OSError: 操作系统错误
            shutil.Error: shutil 模块操作错误
        """

        m9a_path = Path(m9a_folder)

        if not m9a_path.exists():
            self.logger.info(f"M9A 文件夹不存在，正在创建: {m9a_path}")
            try:
                m9a_path.mkdir(parents=True, exist_ok=True)
                return True
            except (IOError, OSError) as e:
                self.logger.error(f"创建 M9A 文件夹失败: {e}")
                return False

        try:
            for item in m9a_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

            self.logger.info(f"M9A 文件夹已清理: {m9a_path}")
            return True
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"清理 M9A 文件夹失败: {e}")
            return False

    def extract_zip_with_progress(self, zip_path: str, extract_to: str) -> bool:
        """
        解压 ZIP 文件并显示进度

        解压指定的 ZIP 文件到目标路径，并在解压过程中显示进度信息。

        Args:
            zip_path: ZIP 文件路径
            extract_to: 解压目标路径

        Returns:
            bool: 操作是否成功

        Raises:
            zipfile.BadZipFile: ZIP 文件无效
            IOError: 文件操作错误
            OSError: 操作系统错误
        """

        if not os.path.exists(zip_path):
            self.logger.error(f"ZIP 文件不存在: {zip_path}")
            return False

        try:
            Path(extract_to).mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                file_list = zip_ref.namelist()
                total_size = sum(file.file_size for file in zip_ref.filelist)

                self.logger.info(f"开始解压: {zip_path}")
                self.logger.info(f"文件数量: {len(file_list)}, 总大小: {total_size / (1024 * 1024):.2f} MB")

                extracted_size = 0
                for file_info in zip_ref.infolist():
                    zip_ref.extract(file_info, extract_to)
                    extracted_size += file_info.file_size
                    progress = (extracted_size / total_size) * 100
                    extracted_mb = extracted_size / (1024 * 1024)
                    total_mb = total_size / (1024 * 1024)
                    print(f"\r解压进度: {progress:.1f}% ({extracted_mb:.2f} MB / {total_mb:.2f} MB)", end="", flush=True)

                # 清理进度条输出行
                print("\r" + " " * 80 + "\r", end="", flush=True)

            self.logger.info(f"解压完成: {zip_path} -> {extract_to}")
            return True
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"解压 ZIP 文件失败: {e}")
            return False

    def restore_config(self, m9a_folder: str) -> bool:
        """
        将 config 回写到 M9A 文件夹

        将临时文件夹中的 config 文件夹复制回 M9A 文件夹，恢复之前备份的配置。

        Args:
            m9a_folder: M9A 文件夹路径

        Returns:
            bool: 操作是否成功

        Raises:
            IOError: 文件操作错误
            OSError: 操作系统错误
            shutil.Error: shutil 模块操作错误
        """

        # 使用路径的哈希值找到对应的备份目录
        path_hash = zlib.crc32(m9a_folder.encode()) & 0xffffffff
        temp_config_path = Path(self.temp_folder) / f"config_{path_hash:08x}"
        m9a_config_path = Path(m9a_folder) / "config"

        if not temp_config_path.exists():
            self.logger.warning(f"临时文件夹中的 config 文件夹不存在: {temp_config_path}")
            return False

        try:
            # 使用 dirs_exist_ok=True 简化代码，避免先删除再复制
            self.logger.info(f"config 文件夹正在回写：{temp_config_path} -> {m9a_config_path}")
            shutil.copytree(temp_config_path, m9a_config_path, dirs_exist_ok=True)
            self.logger.info(f"config 文件夹已回写到: {m9a_config_path}")
            return True
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"回写 config 文件夹失败: {e}")
            return False

    def clean_temp_folder(self) -> bool:
        """
        清理临时文件夹

        删除临时文件夹及其所有内容，释放磁盘空间。

        Returns:
            bool: 操作是否成功

        Raises:
            PermissionError: 权限被拒绝
            IOError: 文件操作错误
            OSError: 操作系统错误
            shutil.Error: shutil 模块操作错误
        """

        temp_path = Path(self.temp_folder)

        if not temp_path.exists():
            self.logger.warning(f"临时文件夹不存在: {temp_path}")
            return False

        try:
            shutil.rmtree(temp_path)
            self.logger.info(f"临时文件夹已清理: {temp_path}")
            return True
        except PermissionError as e:
            self.logger.error(f"清理临时文件夹失败: 权限被拒绝 - {e}")
            return False
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"清理临时文件夹失败: {e}")
            return False

    def check_lite_zip_has_deps(self, zip_path: str) -> bool:
        """
        检查 Lite ZIP 文件中是否包含 deps 文件夹

        检查指定的 Lite ZIP 文件中是否包含 deps 文件夹，以确定是否需要从 Full ZIP 文件中提取 deps 文件夹。

        Args:
            zip_path: Lite ZIP 文件路径

        Returns:
            bool: 是否包含 deps 文件夹

        Raises:
            zipfile.BadZipFile: ZIP 文件无效
            IOError: 文件操作错误
            OSError: 操作系统错误
        """

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_name in zip_ref.namelist():
                    if file_name.startswith('deps/'):
                        self.logger.info(f"Lite ZIP 文件中存在 deps 文件夹")
                        return True
                self.logger.warning(f"Lite ZIP 文件中不包含 deps 文件夹")
                return False
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"检查 Lite ZIP 文件失败: {e}")
            return False

    def get_latest_release_info(self) -> Optional[Dict]:
        """
        获取 GitHub 最新 release 信息

        从 GitHub API 获取指定仓库的最新 release 信息。
        根据 release_version 配置选择不同的 API 端点：
        - release: 使用 /releases 端点，返回最新的正式版
        - latest: 使用 /releases/latest 端点，返回带有 latest 标签的版本

        Returns:
            Dict: release 信息字典，如果获取失败则返回 None

        Raises:
            requests.RequestException: 网络请求错误
        """

        try:
            headers = {'User-Agent': 'M9A-Update-Assistant'}
            proxies = {'http': self.github_proxy, 'https': self.github_proxy} if self.github_proxy else None

            if self.github_release_version == 'release':
                api_url = f"https://api.github.com/repos/{self.github_repo}/releases"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                response.raise_for_status()
                releases = response.json()
                if not releases:
                    self.logger.error("未找到任何 release")
                    return None
                release_info = releases[0]
            elif self.github_release_version == 'latest':
                api_url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                response.raise_for_status()
                release_info = response.json()
            else:
                self.logger.error(f"未知的 release_version: {self.github_release_version}")
                return None

            self.logger.info(f"获取到最新版本: {release_info.get('tag_name', 'Unknown')}")
            return release_info
        except requests.RequestException as e:
            self.logger.error(f"获取 GitHub release 信息失败: {e}")
            return None
        except Exception as e:
            self.logger.error(f"获取 GitHub release 信息时发生错误: {e}")
            return None

    def find_download_url(self, release_info: Dict, pattern: str) -> Optional[str]:
        """
        从 release 信息中查找匹配的下载链接

        Args:
            release_info: GitHub release 信息
            pattern: 文件名匹配模式

        Returns:
            下载 URL，如果未找到则返回 None
        """
        assets = release_info.get('assets', [])

        for asset in assets:
            asset_name = asset.get('name', '')
            if re.match(pattern.replace('*', r'[\d.\-a-zA-Z]+'), asset_name):
                return asset.get('browser_download_url')

        return None

    def download_file_with_progress(self, url: str, save_path: str) -> bool:
        """
        下载文件并显示进度

        从指定的 URL 下载文件到保存路径，并在下载过程中显示进度信息。
        如果下载失败，会自动重试指定次数。

        Args:
            url: 下载 URL
            save_path: 保存路径

        Returns:
            bool: 操作是否成功

        Raises:
            requests.RequestException: 网络请求错误
            IOError: 文件操作错误
            OSError: 操作系统错误
        """

        max_retries = 4
        retry_interval = 10  # 10秒

        for attempt in range(max_retries):
            # 第一次尝试不显示重试计数，后续尝试显示重试计数
            if attempt == 0:
                self.logger.info(f"开始下载文件: {url}")
            else:
                self.logger.info(f"重试下载文件（{attempt}/{max_retries - 1}）: {url}")
            
            try:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)

                headers = {'User-Agent': 'M9A-Update-Assistant'}
                proxies = {'http': self.github_proxy, 'https': self.github_proxy} if self.github_proxy else None

                with requests.get(url, headers=headers, proxies=proxies, timeout=60, stream=True) as response:
                    response.raise_for_status()  # 抛出 HTTP 错误
                    
                    total_size = int(response.headers.get('Content-Length', 0))
                    downloaded_size = 0

                    if total_size > 0:
                        self.logger.info(f"获取到文件大小: {total_size / (1024 * 1024):.2f} MB")

                    with open(save_path, 'wb') as f:
                        chunk_size = 8192
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)
                                downloaded_size += len(chunk)

                                if total_size > 0:
                                    progress = (downloaded_size / total_size) * 100
                                    downloaded_mb = downloaded_size / (1024 * 1024)
                                    total_mb = total_size / (1024 * 1024)
                                    print(f"\r下载进度: {progress:.1f}% ({downloaded_mb:.2f} MB / {total_mb:.2f} MB)", end="", flush=True)

                    # 清理进度条输出行
                    print("\r" + " " * 80 + "\r", end="", flush=True)

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

    def download_latest_release(self) -> Optional[List[str]]:
        """
        下载最新版本的 Lite 和 Full ZIP 文件

        Returns:
            下载的文件路径列表，如果下载失败则返回 None
        """
        release_info = self.get_latest_release_info()
        if not release_info:
            return None

        tag_name = release_info.get('tag_name', 'latest')
        download_dir = Path(self.temp_folder) / "ZIP"
        download_dir.mkdir(parents=True, exist_ok=True)

        lite_url = self.find_download_url(release_info, self.lite_zip_pattern)
        full_url = self.find_download_url(release_info, self.full_zip_pattern)

        # 检查临时文件夹中是否存在最新版本的压缩包
        downloaded_files = []
        version_pattern = tag_name.replace('v', '')
        
        # 检查 Lite ZIP
        if lite_url:
            lite_filename = Path(lite_url).name
            lite_save_path = download_dir / lite_filename
            
            # 检查临时文件夹中是否存在对应版本的 Lite ZIP
            lite_files = list(download_dir.glob(LITE_ZIP_PATTERN.replace('*', version_pattern)))
            if lite_files:
                self.logger.info(f"临时文件夹中已存在最新版本的 Lite ZIP: {lite_files[0]}")
                downloaded_files.append(str(lite_files[0]))
            else:
                # 下载 Lite ZIP
                if self.download_file_with_progress(lite_url, str(lite_save_path)):
                    downloaded_files.append(str(lite_save_path))
                else:
                    self.logger.critical("下载 Lite ZIP 失败")
                    return None
        else:
            self.logger.error(f"未找到匹配的 Lite ZIP 文件: {self.lite_zip_pattern}")
            return None

        # 检查是否需要下载 Full ZIP
        need_full_download = True
        
        # 检查 Lite ZIP 是否包含 deps 文件夹
        lite_zip_path = downloaded_files[0]
        if self.check_lite_zip_has_deps(lite_zip_path):
            need_full_download = False
            self.logger.info("Lite ZIP 已包含 deps 文件夹，跳过 Full ZIP 下载")
        elif not self.github_full_download_enabled:
            need_full_download = False
            self.logger.info("配置中禁用了 Full 版本下载，跳过 Full ZIP 下载")

        # 检查 Full ZIP
        if need_full_download and full_url:
            full_filename = Path(full_url).name
            full_save_path = download_dir / full_filename
            
            # 检查临时文件夹中是否存在对应版本的 Full ZIP
            full_files = list(download_dir.glob(FULL_ZIP_PATTERN.replace('*', version_pattern)))
            if full_files:
                self.logger.info(f"临时文件夹中已存在最新版本的 Full ZIP: {full_files[0]}")
                downloaded_files.append(str(full_files[0]))
            else:
                # 下载 Full ZIP
                if self.download_file_with_progress(full_url, str(full_save_path)):
                    downloaded_files.append(str(full_save_path))
                else:
                    self.logger.critical("下载 Full ZIP 失败")
                    return None
        elif not need_full_download:
            self.logger.info("跳过 Full ZIP 下载")

        return downloaded_files

    def _calculate_sha256(self, file_path: str) -> str:
        """
        计算文件的 SHA256 哈希值

        Args:
            file_path: 文件路径

        Returns:
            SHA256 哈希值（小写十六进制）
        """
        import hashlib
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _get_asset_sha256(self, release_info: Dict, asset_name: str) -> Optional[str]:
        """
        从 GitHub release 信息中获取资产的 SHA256 哈希值

        Args:
            release_info: GitHub release 信息
            asset_name: 资产文件名

        Returns:
            SHA256 哈希值，如果未找到则返回 None
        """
        # 从 assets 列表中查找对应的资产
        assets = release_info.get('assets', [])
        for asset in assets:
            if asset.get('name') == asset_name:
                digest = asset.get('digest', '')
                if digest.startswith('sha256:'):
                    # 提取 SHA256 哈希值（移除 'sha256:' 前缀）
                    return digest[7:]
        
        # 尝试从 release body 中查找 SHA256 哈希值（备用方案）
        body = release_info.get('body', '')
        lines = body.split('\n')
        
        for line in lines:
            if asset_name in line and 'sha256' in line.lower():
                # 尝试提取哈希值
                import re
                match = re.search(r'[0-9a-f]{64}', line.lower())
                if match:
                    return match.group(0)
        
        return None

    def download_latest_release(self) -> Optional[List[str]]:
        """
        下载最新版本的 Lite 和 Full ZIP 文件

        Returns:
            下载的文件路径列表，如果下载失败则返回 None
        """
        release_info = self.get_latest_release_info()
        if not release_info:
            return None

        tag_name = release_info.get('tag_name', 'latest')
        download_dir = Path(self.temp_folder) / "ZIP"
        download_dir.mkdir(parents=True, exist_ok=True)

        lite_url = self.find_download_url(release_info, self.lite_zip_pattern)
        full_url = self.find_download_url(release_info, self.full_zip_pattern)

        # 检查临时文件夹中是否存在最新版本的压缩包
        downloaded_files = []
        version_pattern = tag_name.replace('v', '')
        
        # 检查 Lite ZIP
        if lite_url:
            lite_filename = Path(lite_url).name
            lite_save_path = download_dir / lite_filename
            
            # 检查临时文件夹中是否存在对应版本的 Lite ZIP
            lite_files = list(download_dir.glob(LITE_ZIP_PATTERN.replace('*', version_pattern)))
            if lite_files:
                self.logger.info(f"临时文件夹中已存在最新版本的 Lite ZIP: {lite_files[0]}")
                
                # 校验 Lite ZIP 文件的完整性
                lite_zip_path = str(lite_files[0])
                if not self._verify_zip_integrity(lite_zip_path, release_info, lite_filename):
                    self.logger.error("Lite ZIP 文件校验失败，将重新下载")
                    lite_files = []  # 强制重新下载
                else:
                    downloaded_files.append(lite_zip_path)
            
            if not lite_files:
                # 下载 Lite ZIP
                if self.download_file_with_progress(lite_url, str(lite_save_path)):
                    # 校验下载的 Lite ZIP 文件
                    if not self._verify_zip_integrity(str(lite_save_path), release_info, lite_filename):
                        self.logger.critical("下载的 Lite ZIP 文件校验失败")
                        return None
                    downloaded_files.append(str(lite_save_path))
                else:
                    self.logger.critical("下载 Lite ZIP 失败")
                    return None
        else:
            self.logger.error(f"未找到匹配的 Lite ZIP 文件: {self.lite_zip_pattern}")
            return None

        # 检查是否需要下载 Full ZIP
        need_full_download = True
        
        # 检查 Lite ZIP 是否包含 deps 文件夹
        lite_zip_path = downloaded_files[0]
        if self.check_lite_zip_has_deps(lite_zip_path):
            need_full_download = False
            self.logger.info("Lite ZIP 已包含 deps 文件夹，跳过 Full ZIP 下载")
        elif not self.github_full_download_enabled:
            need_full_download = False
            self.logger.info("配置中禁用了 Full 版本下载，跳过 Full ZIP 下载")

        # 检查 Full ZIP
        if need_full_download and full_url:
            full_filename = Path(full_url).name
            full_save_path = download_dir / full_filename
            
            # 检查临时文件夹中是否存在对应版本的 Full ZIP
            full_files = list(download_dir.glob(FULL_ZIP_PATTERN.replace('*', version_pattern)))
            if full_files:
                self.logger.info(f"临时文件夹中已存在最新版本的 Full ZIP: {full_files[0]}")
                
                # 校验 Full ZIP 文件的完整性
                full_zip_path = str(full_files[0])
                if not self._verify_zip_integrity(full_zip_path, release_info, full_filename):
                    self.logger.error("Full ZIP 文件校验失败，将重新下载")
                    full_files = []  # 强制重新下载
                else:
                    downloaded_files.append(full_zip_path)
            
            if not full_files:
                # 下载 Full ZIP
                if self.download_file_with_progress(full_url, str(full_save_path)):
                    # 校验下载的 Full ZIP 文件
                    if not self._verify_zip_integrity(str(full_save_path), release_info, full_filename):
                        self.logger.critical("下载的 Full ZIP 文件校验失败")
                        return None
                    downloaded_files.append(str(full_save_path))
                else:
                    self.logger.critical("下载 Full ZIP 失败")
                    return None
        elif not need_full_download:
            self.logger.info("跳过 Full ZIP 下载")

        return downloaded_files

    def _verify_zip_integrity(self, zip_path: str, release_info: Dict, zip_filename: str) -> bool:
        """
        验证 ZIP 文件的完整性

        Args:
            zip_path: ZIP 文件路径
            release_info: GitHub release 信息
            zip_filename: ZIP 文件名

        Returns:
            文件是否完整
        """
        try:
            # 检查文件是否为有效的 ZIP 文件
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # 尝试读取 ZIP 文件内容以验证完整性
                zip_ref.namelist()
            
            # 尝试使用 SHA256 校验
            expected_sha256 = self._get_asset_sha256(release_info, zip_filename)
            if expected_sha256:
                actual_sha256 = self._calculate_sha256(zip_path)
                if actual_sha256 != expected_sha256:
                    self.logger.error(f"SHA256 校验失败:")
                    self.logger.warning(f"GitHub: {expected_sha256}")
                    self.logger.warning(f"本地:   {actual_sha256}")
                    return False
                else:
                    self.logger.info(f"SHA256 校验成功")
                    self.logger.info(f"GitHub: {expected_sha256}")
                    self.logger.info(f"本地:   {actual_sha256}")

            else:
                self.logger.info("未找到 SHA256 校验值，仅验证文件格式")
            
            return True
        except zipfile.BadZipFile:
            self.logger.error(f"无效的 ZIP 文件: {zip_path}")
            return False
        except (IOError, OSError) as e:
            self.logger.error(f"验证 ZIP 文件失败: {e}")
            return False

    def extract_deps_from_full_zip(self, full_zip_path: Optional[str] = None, m9a_folder: Optional[str] = None) -> bool:
        """
        从 Full ZIP 文件中提取 deps 文件夹到 M9A 文件夹

        Args:
            full_zip_path: Full ZIP 文件路径，如果为 None 则从当前目录查找
            m9a_folder: M9A 文件夹路径，如果为 None 则使用默认路径

        Returns:
            操作是否成功
        """
        if full_zip_path and os.path.exists(full_zip_path):
            full_zip_file = Path(full_zip_path)
        else:
            pattern = self.full_zip_pattern.replace('*', r'[\d.]+')
            full_zip_regex = re.compile(pattern)

            search_dirs = [Path(self.temp_folder), Path.cwd()]
            full_zip_files = []

            for search_dir in search_dirs:
                if search_dir.exists():
                    full_zip_files.extend([f for f in search_dir.glob(FULL_ZIP_PATTERN) if full_zip_regex.match(f.name)])

            if not full_zip_files:
                self.logger.warning(f"未找到匹配的 Full ZIP 文件: {self.full_zip_pattern}")
                return False

            full_zip_file = full_zip_files[0]

        self.logger.info(f"找到 Full ZIP 文件: {full_zip_file}")

        try:
            m9a_path = Path(m9a_folder) if m9a_folder else Path(self.m9a_folders[0])
            m9a_path.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(full_zip_file, 'r') as zip_ref:
                deps_files = [f for f in zip_ref.namelist() if f.startswith('deps/')]

                if not deps_files:
                    self.logger.warning(f"ZIP 文件中未找到 deps 文件夹: {full_zip_file}")
                    return False

                total_size = sum(zip_ref.getinfo(f).file_size for f in deps_files)

                self.logger.info(f"开始提取 deps 文件夹: {len(deps_files)} 个文件, 总大小: {total_size / (1024 * 1024):.2f} MB")

                extracted_size = 0
                for file_name in deps_files:
                    file_info = zip_ref.getinfo(file_name)
                    zip_ref.extract(file_info, m9a_path)
                    extracted_size += file_info.file_size
                    progress = (extracted_size / total_size) * 100
                    extracted_mb = extracted_size / (1024 * 1024)
                    total_mb = total_size / (1024 * 1024)
                    print(f"\r提取 deps: {progress:.1f}% ({extracted_mb:.2f} MB / {total_mb:.2f} MB)", end="", flush=True)

                # 清理进度条输出行
                print("\r" + " " * 80 + "\r", end="", flush=True)

            self.logger.info(f"deps 文件夹已提取到: {m9a_path}")
            return True
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"提取 deps 文件夹失败: {e}")
            return False

    def find_lite_zip(self) -> Optional[str]:
        """
        查找匹配的 Lite ZIP 文件

        Returns:
            找到的 ZIP 文件路径，如果未找到则返回 None
        """
        pattern = self.lite_zip_pattern.replace('*', r'[\d.]+')
        lite_zip_regex = re.compile(pattern)

        # 搜索目录：临时文件夹和当前目录
        search_dirs = [Path(self.temp_folder), Path.cwd()]
        lite_zip_files = []

        for search_dir in search_dirs:
            if search_dir.exists():
                lite_zip_files.extend([f for f in search_dir.glob(LITE_ZIP_PATTERN) if lite_zip_regex.match(f.name)])

        if not lite_zip_files:
            self.logger.warning(f"未找到匹配的 Lite ZIP 文件: {self.lite_zip_pattern}")
            return None

        return str(lite_zip_files[0])

    def run_update(self) -> bool:
        """
        执行完整的更新流程

        执行完整的 M9A 更新流程，包括：
        1. 从 GitHub 获取最新版本信息
        2. 下载 Lite 和 Full ZIP 文件
        3. 对每个 M9A 执行：
           - 备份配置文件
           - 清理 M9A 文件夹
           - 解压 Lite ZIP 文件
           - 恢复配置文件
           - 从 Full ZIP 文件中提取 deps 文件夹（如果需要）
        4. 清理临时文件夹

        Returns:
            bool: 操作是否成功
        """

        lite_zip = None
        full_zip = None

        # 尝试从 GitHub 下载最新版本
        self.logger.info("正在从 GitHub 获取最新版本...")
        downloaded_files = self.download_latest_release()
        if downloaded_files:
            for file_path in downloaded_files:
                if 'Lite' in file_path:
                    lite_zip = file_path
                elif 'Full' in file_path:
                    full_zip = file_path
        else:
            self.logger.warning("从 GitHub 下载失败，尝试使用本地文件")

        if not lite_zip:
            lite_zip = self.find_lite_zip()
            if not lite_zip:
                self.logger.critical("未找到 Lite ZIP 文件，更新终止")
                return False

        self.logger.info(f"使用 Lite ZIP 文件: {lite_zip}")

        # 检查是否需要提取 deps 文件夹
        need_extract_deps = True
        if self.check_lite_zip_has_deps(lite_zip):
            need_extract_deps = False
            self.logger.info("Lite ZIP 已包含 deps 文件夹，跳过 deps 提取")
        elif not full_zip:
            need_extract_deps = False
            self.logger.info("未找到 Full ZIP 文件，跳过 deps 提取")

        # 遍历所有 M9A
        all_success = True
        for index, m9a_folder in enumerate(self.m9a_folders, 1):
            print(f"\n")
            self.logger.info(f"开始更新第 {index}/{len(self.m9a_folders)} 个 M9A: {m9a_folder}")

            # 备份 config（如果存在）
            config_backup_successful = self.backup_config(m9a_folder)
            if not config_backup_successful:
                self.logger.info("config 文件夹不存在或备份失败，将跳过备份和回写步骤")

            if not self.clean_m9a_folder(m9a_folder):
                self.logger.critical(f"清理 M9A 文件夹失败: {m9a_folder}")
                all_success = False
                continue

            if not self.extract_zip_with_progress(lite_zip, m9a_folder):
                self.logger.critical(f"解压 Lite ZIP 失败: {m9a_folder}")
                all_success = False
                continue

            # 回写 config（如果之前备份成功）
            if config_backup_successful:
                if not self.restore_config(m9a_folder):
                    self.logger.critical(f"回写 config 失败: {m9a_folder}")
                    all_success = False
                    continue

            # 提取 deps 文件夹
            if need_extract_deps:
                if not self.extract_deps_from_full_zip(full_zip, m9a_folder):
                    self.logger.critical(f"提取 deps 文件夹失败: {m9a_folder}")
                    all_success = False
                    continue

            self.logger.info(f"M9A 更新完成: {m9a_folder}")

        # 所有 M9A 更新完成后，清理临时文件夹
        if not self.clean_temp_folder():
            self.logger.warning("无法清理临时文件夹")

        self._cleanup_old_logs()

        if all_success:
            self.logger.info("所有 M9A 完成更新")
        else:
            self.logger.warning("部分 M9A 更新失败")

        return all_success


def main():
    """主函数"""
    try:
        print_info()
        assistant = M9AUpdateAssistant()
        
        # 验证配置
        if not assistant.validate_config():
            assistant.logger.critical("错误的配置，请修改配置文件后重新运行。")
            sys.exit(1)
        
        success = assistant.run_update()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.critical("捕获到Ctrl+C，终止运行")
        sys.exit(0)
    except Exception as e:
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.critical(f"程序执行出错: {e}")
        raise


if __name__ == "__main__":
    main()
