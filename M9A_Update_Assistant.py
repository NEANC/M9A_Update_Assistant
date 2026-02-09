#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import configparser
import logging
import os
import re
import shutil
import sys
import urllib.request
import urllib.error
import zipfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List


LITE_ZIP_PATTERN = 'M9A-win-x86_64-v*-Lite.zip'
FULL_ZIP_PATTERN = 'M9A-win-x86_64-v*-Full.zip'
VERSION = "v1.0.0"


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
# M9A 文件夹路径
m9a_folder = Z:\M9A

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
"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                f.write(default_config)
            print(f"已生成默认配置文件: {self.config_file}")
            print("请修改配置文件后重新运行程序。")
            sys.exit(0)
        except Exception as e:
            print(f"生成配置文件失败: {e}")
            sys.exit(1)

    def _load_config(self) -> None:
        """加载配置文件"""
        if not os.path.exists(self.config_file):
            print(f"配置文件 {self.config_file} 不存在，将生成默认配置文件")
            self._generate_default_config()

        self.config.read(self.config_file, encoding='utf-8')

        self.m9a_folder = self.config.get('Paths', 'm9a_folder', fallback='Z:\\M9A')
        self.temp_folder = self.config.get('Paths', 'temp_folder', fallback='Z:\\Temp\\M9A-Update-Assistant')
        self.lite_zip_pattern = LITE_ZIP_PATTERN
        self.full_zip_pattern = FULL_ZIP_PATTERN
        self.log_max_files = self.config.getint('Logs', 'max_files', fallback=15)
        self.log_save_enabled = self.config.getboolean('Logs', 'save_enabled', fallback=True)

        self.github_repo = self.config.get('GitHub', 'repo', fallback='MAA1999/M9A')
        self.github_api_url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        self.github_proxy = self.config.get('GitHub', 'proxy', fallback='').strip()
        self.github_full_download_enabled = self.config.getboolean('GitHub', 'full_download_enabled', fallback=True)

        if self.github_proxy:
            self.logger.info(f"已配置代理: {self.github_proxy}")
        else:
            self.logger.info("未配置代理，若遇到网络问题请配置代理")

        if self.log_save_enabled:
            self._setup_file_logger()

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

    def backup_config(self) -> bool:
        """
        备份 config 文件夹到临时文件夹

        Returns:
            操作是否成功
        """
        m9a_config_path = Path(self.m9a_folder) / "config"
        temp_config_path = Path(self.temp_folder) / "config"

        if not m9a_config_path.exists():
            self.logger.warning(f"M9A 文件夹中的 config 文件夹不存在: {m9a_config_path}")
            return False

        try:
            Path(self.temp_folder).mkdir(parents=True, exist_ok=True)

            if temp_config_path.exists():
                shutil.rmtree(temp_config_path)

            shutil.copytree(m9a_config_path, temp_config_path)
            self.logger.info(f"config 文件夹已备份到: {temp_config_path}")
            return True
        except Exception as e:
            self.logger.error(f"备份 config 文件夹失败: {e}")
            return False

    def clean_m9a_folder(self) -> bool:
        """
        清理 M9A 文件夹中的所有文件

        Returns:
            操作是否成功
        """
        m9a_path = Path(self.m9a_folder)

        if not m9a_path.exists():
            self.logger.info(f"M9A 文件夹不存在，正在创建: {m9a_path}")
            try:
                m9a_path.mkdir(parents=True, exist_ok=True)
                return True
            except Exception as e:
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
        except Exception as e:
            self.logger.error(f"清理 M9A 文件夹失败: {e}")
            return False

    def extract_zip_with_progress(self, zip_path: str, extract_to: str) -> bool:
        """
        解压 ZIP 文件并显示进度

        Args:
            zip_path: ZIP 文件路径
            extract_to: 解压目标路径

        Returns:
            操作是否成功
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
        except Exception as e:
            self.logger.error(f"解压 ZIP 文件失败: {e}")
            return False

    def restore_config(self) -> bool:
        """
        将 config 回写到 M9A 文件夹

        Returns:
            操作是否成功
        """
        temp_config_path = Path(self.temp_folder) / "config"
        m9a_config_path = Path(self.m9a_folder) / "config"

        if not temp_config_path.exists():
            self.logger.warning(f"临时文件夹中的 config 文件夹不存在: {temp_config_path}")
            return False

        try:
            if m9a_config_path.exists():
                shutil.rmtree(m9a_config_path)

            shutil.copytree(temp_config_path, m9a_config_path)
            self.logger.info(f"config 文件夹已回写到: {m9a_config_path}")
            return True
        except Exception as e:
            self.logger.error(f"回写 config 文件夹失败: {e}")
            return False

    def clean_temp_folder(self) -> bool:
        """
        清理临时文件夹

        Returns:
            操作是否成功
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
        except Exception as e:
            self.logger.error(f"清理临时文件夹失败: {e}")
            return False

    def check_lite_zip_has_deps(self, zip_path: str) -> bool:
        """
        检查 Lite ZIP 文件中是否包含 deps 文件夹

        Args:
            zip_path: Lite ZIP 文件路径

        Returns:
            是否包含 deps 文件夹
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_name in zip_ref.namelist():
                    if file_name.startswith('deps/'):
                        self.logger.info(f"Lite ZIP 文件中包含 deps 文件夹: {zip_path}")
                        return True
                self.logger.info(f"Lite ZIP 文件中不包含 deps 文件夹: {zip_path}")
                return False
        except Exception as e:
            self.logger.error(f"检查 Lite ZIP 文件失败: {e}")
            return False

    def get_latest_release_info(self) -> Optional[Dict]:
        """
        获取 GitHub 最新 release 信息

        Returns:
            release 信息字典，如果获取失败则返回 None
        """
        try:
            req = urllib.request.Request(self.github_api_url)
            req.add_header('User-Agent', 'M9A-Update-Assistant')

            proxy_handler = None
            if self.github_proxy:
                proxy_handler = urllib.request.ProxyHandler({'http': self.github_proxy, 'https': self.github_proxy})

            opener = urllib.request.build_opener(proxy_handler) if proxy_handler else urllib.request.build_opener()
            urllib.request.install_opener(opener)

            with opener.open(req, timeout=30) as response:
                data = response.read().decode('utf-8')
                import json
                release_info = json.loads(data)

                self.logger.info(f"获取到最新版本: {release_info.get('tag_name', 'Unknown')}")
                return release_info
        except urllib.error.URLError as e:
            self.logger.error(f"获取 GitHub release 信息失败: {e}")
            return None
        except json.JSONDecodeError as e:
            self.logger.error(f"解析 GitHub release 信息失败: {e}")
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
            if re.match(pattern.replace('*', r'[\d.]+'), asset_name):
                return asset.get('browser_download_url')
        
        return None

    def download_file_with_progress(self, url: str, save_path: str) -> bool:
        """
        下载文件并显示进度

        Args:
            url: 下载 URL
            save_path: 保存路径

        Returns:
            操作是否成功
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

                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'M9A-Update-Assistant')

                proxy_handler = None
                if self.github_proxy:
                    proxy_handler = urllib.request.ProxyHandler({'http': self.github_proxy, 'https': self.github_proxy})

                opener = urllib.request.build_opener(proxy_handler) if proxy_handler else urllib.request.build_opener()

                with opener.open(req, timeout=60) as response:
                    total_size = int(response.getheader('Content-Length', 0))
                    downloaded_size = 0

                    if total_size > 0:
                        self.logger.info(f"文件大小: {total_size / (1024 * 1024):.2f} MB")

                    with open(save_path, 'wb') as f:
                        chunk_size = 8192
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded_size += len(chunk)

                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                downloaded_mb = downloaded_size / (1024 * 1024)
                                total_mb = total_size / (1024 * 1024)
                                print(f"\r下载进度: {progress:.1f}% ({downloaded_mb:.2f} MB / {total_mb:.2f} MB)", end="", flush=True)

                    # 清理进度条输出行
                    print("\r" + " " * 80 + "\r", end="", flush=True)

                    self.logger.info(f"下载完成: {save_path}")
                    return True
            except urllib.error.URLError as e:
                self.logger.error(f"下载文件失败: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    # 重置 opener 以断开连接
                    urllib.request.install_opener(urllib.request.build_opener())
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
        download_dir = Path(self.temp_folder)
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
            lite_files = list(download_dir.glob(f"M9A-win-x86_64-v{version_pattern}-Lite.zip"))
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
            full_files = list(download_dir.glob(f"M9A-win-x86_64-v{version_pattern}-Full.zip"))
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
        download_dir = Path(self.temp_folder)
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
            lite_files = list(download_dir.glob(f"M9A-win-x86_64-v{version_pattern}-Lite.zip"))
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
            full_files = list(download_dir.glob(f"M9A-win-x86_64-v{version_pattern}-Full.zip"))
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
                    self.logger.error(f"SHA256 校验失败:\n"
                                      f"GitHub:{expected_sha256}\n"
                                      f"本地:{actual_sha256}")
                    return False
                else:
                    self.logger.info(f"SHA256 校验成功\n"
                                     f"GitHub:{expected_sha256}\n"
                                     f"本地:{actual_sha256}")
            else:
                self.logger.info("未找到 SHA256 校验值，仅验证文件格式")
            
            return True
        except zipfile.BadZipFile:
            self.logger.error(f"无效的 ZIP 文件: {zip_path}")
            return False
        except Exception as e:
            self.logger.error(f"验证 ZIP 文件失败: {e}")
            return False

    def extract_deps_from_full_zip(self, full_zip_path: Optional[str] = None) -> bool:
        """
        从 Full ZIP 文件中提取 deps 文件夹到 M9A 文件夹

        Args:
            full_zip_path: Full ZIP 文件路径，如果为 None 则从当前目录查找

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
                    full_zip_files.extend([f for f in search_dir.glob("M9A-win-x86_64-v*-Full.zip") if full_zip_regex.match(f.name)])

            if not full_zip_files:
                self.logger.warning(f"未找到匹配的 Full ZIP 文件: {self.full_zip_pattern}")
                return False

            full_zip_file = full_zip_files[0]

        self.logger.info(f"找到 Full ZIP 文件: {full_zip_file}")

        try:
            m9a_path = Path(self.m9a_folder)
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
        except Exception as e:
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
                lite_zip_files.extend([f for f in search_dir.glob("M9A-win-x86_64-v*-Lite.zip") if lite_zip_regex.match(f.name)])

        if not lite_zip_files:
            self.logger.warning(f"未找到匹配的 Lite ZIP 文件: {self.lite_zip_pattern}")
            return None

        return str(lite_zip_files[0])

    def run_update(self) -> bool:
        """
        执行完整的更新流程

        Returns:
            操作是否成功
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

        # 备份 config（如果存在）
        config_backup_successful = self.backup_config()
        if not config_backup_successful:
            self.logger.info("config 文件夹不存在或备份失败，将跳过备份和回写步骤")

        if not self.clean_m9a_folder():
            self.logger.critical("清理 M9A 文件夹失败，更新终止")
            return False

        if not self.extract_zip_with_progress(lite_zip, self.m9a_folder):
            self.logger.critical("解压 Lite ZIP 失败，更新终止")
            return False

        # 回写 config（如果之前备份成功）
        if config_backup_successful:
            if not self.restore_config():
                self.logger.critical("回写 config 失败")
                return False

        # 检查是否需要提取 deps 文件夹
        need_extract_deps = True
        if self.check_lite_zip_has_deps(lite_zip):
            need_extract_deps = False
            self.logger.info("Lite ZIP 已包含 deps 文件夹，跳过 deps 提取")
        elif not full_zip:
            need_extract_deps = False
            self.logger.info("未找到 Full ZIP 文件，跳过 deps 提取")

        # 提取 deps 文件夹
        if need_extract_deps:
            if not self.extract_deps_from_full_zip(full_zip):
                self.logger.critical("提取 deps 文件夹失败")
                return False

        if not self.clean_temp_folder():
            self.logger.warning("无法清理临时文件夹")

        self._cleanup_old_logs()

        self.logger.info("M9A 完成更新")

        return True


def main():
    """主函数"""
    try:
        print_info()
        assistant = M9AUpdateAssistant()
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
