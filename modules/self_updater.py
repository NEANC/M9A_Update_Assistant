#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import subprocess
import sys
import requests

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from modules.download_manager import DownloadManager
from modules.github_release_client import GitHubReleaseClient
from modules.zip_manager import ZipManager


class SelfUpdater:
    """自更新器，负责自我更新检查、下载、替换、回滚"""

    SELF_UPDATE_REPO = "NEANC/M9A_Update_Assistant"

    def __init__(self, proxy: str, temp_folder: str, logger: logging.Logger):
        """
        初始化自更新器

        Args:
            proxy: 代理地址
            temp_folder: 临时文件夹路径
            logger: 日志记录器
        """
        self.proxy = proxy
        self.temp_folder = temp_folder
        self.logger = logger

    @staticmethod
    def detect_package_type() -> Tuple[bool, str]:
        """
        检测当前运行环境是否为打包后的可执行文件

        Returns:
            (是否为打包后程序, 打包方式名称)
        """
        is_pyinstaller = getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')
        is_nuitka = hasattr(sys, '__compiled__')
        is_py_script = sys.argv[0].endswith('.py')
        is_bundled = not is_py_script or is_pyinstaller or is_nuitka

        package_type = "Nuitka"
        if is_pyinstaller:
            package_type = "PyInstaller"

        logging.getLogger("M9AUpdateAssistant").debug(
            f"源码运行: {is_py_script}, 是否构建: {is_bundled}, 运行模式: {package_type}"
        )
        return is_bundled, package_type

    @staticmethod
    def version_to_tuple(v: str) -> Tuple[int, ...]:
        """将版本号字符串转换为元组用于比较"""
        try:
            return tuple(map(int, v.lstrip('v').split('.')))
        except Exception:
            return ()

    def check_self_update(self, current_version: str, gh_client: GitHubReleaseClient,
                           download_manager: DownloadManager,
                           zip_manager: ZipManager) -> bool:
        """
        检查并准备自身更新

        下载新版本 exe 并校验后，安排退出后自动替换。
        实际替换由 --self-update 模式完成。

        Returns:
            bool: 是否需要退出以完成更新
        """
        print(f"\n")
        self.logger.info("开始检查程序版本更新...")

        is_bundled, package_type = self.detect_package_type()
        if not is_bundled:
            self.logger.warning("当前为调试模式，跳过更新检查")
            return False

        try:
            headers = {'User-Agent': 'M9A-Update-Assistant'}
            proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None

            api_url = f"https://api.github.com/repos/{self.SELF_UPDATE_REPO}/releases/latest"
            response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
            response.raise_for_status()
            release_info = response.json()

            latest_version = release_info.get('tag_name', '')
            current_ver_tuple = self.version_to_tuple(current_version)
            latest_ver_tuple = self.version_to_tuple(latest_version)

            if current_ver_tuple and latest_ver_tuple:
                if current_ver_tuple >= latest_ver_tuple:
                    self.logger.info("当前版本已最新")
                    return False
                self.logger.info(f"检测到新版本: {latest_version}")
            else:
                self.logger.warning("版本号校验错误，跳过更新")
                return False

            assets = release_info.get('assets', [])
            exe_url = None
            exe_name = None

            primary_keyword = package_type
            secondary_keyword = "PyInstaller" if package_type == "Nuitka" else "Nuitka"

            for asset in assets:
                asset_name = asset.get('name', '')
                if primary_keyword in asset_name and asset_name.endswith('.exe'):
                    exe_url = asset.get('browser_download_url')
                    exe_name = asset_name
                    self.logger.info(f"找到 {primary_keyword} 版本: {exe_name}")
                    break

            if not exe_url:
                self.logger.info(f"未找到 {primary_keyword} 版本，尝试查找 {secondary_keyword} 版本")
                for asset in assets:
                    asset_name = asset.get('name', '')
                    if secondary_keyword in asset_name and asset_name.endswith('.exe'):
                        exe_url = asset.get('browser_download_url')
                        exe_name = asset_name
                        self.logger.info(f"找到 {secondary_keyword} 版本: {exe_name}")
                        break

            if not exe_url:
                self.logger.warning("未找到带有 Nuitka 或 PyInstaller 标签的 exe 文件")
                return False

            temp_dir = Path(self.temp_folder)
            temp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = temp_dir / "M9A_Update_Assistant_new.exe.tmp"
            sha_path = temp_dir / "M9A_Update_Assistant_new.sha256"

            expected_sha256 = gh_client.get_exe_sha256_from_body(release_info, exe_name)

            if expected_sha256:
                sha_path.write_text(expected_sha256, encoding='ascii')
                self.logger.debug(f"已保存 SHA256 校验值: {sha_path}")

            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    self.logger.info(f"重试下载自更新文件（{attempt + 1}/{max_retries}）")
                else:
                    self.logger.info(f"开始下载: {exe_url}")

                if not download_manager.download_file_with_progress(exe_url, str(tmp_path)):
                    self.logger.error("下载失败")
                    continue

                if not expected_sha256:
                    expected_sha256 = gh_client.get_exe_sha256_from_body(release_info, exe_name)

                if expected_sha256:
                    if zip_manager.verify_file_sha256(str(tmp_path), expected_sha256):
                        sha_path.write_text(expected_sha256, encoding='ascii')
                        break
                    self.logger.error("SHA256 校验失败，准备重试")
                    continue

                if attempt == max_retries - 1:
                    self.logger.error("release body 中未找到 SHA256 校验值，已重试 3 次，放弃更新")
                # will continue to next attempt

            else:
                self.logger.error("自更新下载校验失败，已达到最大重试次数，跳过更新")
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
                try:
                    sha_path.unlink()
                except Exception:
                    pass
                return False

            self.logger.info("新版本已下载并校验通过")
            script = self._generate_self_update_script()
            self.logger.info("程序将在退出后自动替换")

            subprocess.Popen(
                ['cmd', '/c', script],
                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )
            return True

        except requests.RequestException as e:
            self.logger.error(f"获取 GitHub release 信息失败: {e}")
            return False
        except Exception as e:
            self.logger.error(f"检查自身更新时出错: {e}")
            return False

    def _generate_self_update_script(self) -> str:
        """
        生成自更新 CMD 脚本，用于在程序退出后替换自身

        Returns:
            生成的 .bat 脚本路径
        """
        current_exe = sys.executable
        temp_dir = Path(self.temp_folder)
        temp_dir.mkdir(parents=True, exist_ok=True)
        script_path = temp_dir / "self_update.bat"

        script_content = (
            f'@echo off\r\n'
            f'echo 等待主进程退出...\r\n'
            f':waitloop\r\n'
            f'timeout /t 1 /nobreak >nul\r\n'
            f'tasklist /FI "PID eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul\r\n'
            f'if not errorlevel 1 goto waitloop\r\n'
            f'echo 正在启动自更新...\r\n'
            f'start "" /wait "{current_exe}" --self-update\r\n'
            f'del "%~f0"\r\n'
        )
        script_path.write_text(script_content, encoding='gbk')
        self.logger.debug(f"自更新脚本已生成: {script_path}")
        return str(script_path)

    def perform(self, zip_manager=None) -> None:
        """
        执行自身更新替换

        1. 重新校验 new.exe.tmp 的 SHA256
        2. 将当前 exe 重命名为 .bak
        3. 将 new.exe.tmp 移动为当前 exe
        4. 启动新 exe --self-update-complete
        """
        tmp_path = Path(self.temp_folder) / "M9A_Update_Assistant_new.exe.tmp"
        sha_path = Path(self.temp_folder) / "M9A_Update_Assistant_new.sha256"
        current_exe = Path(sys.executable)
        backup_exe = current_exe.with_suffix('.exe.bak')

        if not tmp_path.exists():
            self.logger.critical(f"更新文件不存在: {tmp_path}")
            sys.exit(1)

        if zip_manager and sha_path.exists():
            expected = sha_path.read_text(encoding='ascii').strip()
            self.logger.info("重新校验更新文件完整性...")
            if not zip_manager.verify_file_sha256(str(tmp_path), expected):
                self.logger.critical("更新文件校验失败，放弃更新")
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
                try:
                    sha_path.unlink()
                except Exception:
                    pass
                sys.exit(1)

        self.logger.info(f"开始替换: {current_exe}")

        try:
            if backup_exe.exists():
                backup_exe.unlink()
            current_exe.rename(backup_exe)
            self.logger.info(f"已备份: {backup_exe}")

            tmp_path.rename(current_exe)
            self.logger.info(f"已替换: {current_exe}")

            if sha_path.exists():
                sha_path.unlink()

            self.logger.info("启动新版本完成更新...")
            subprocess.Popen(
                [str(current_exe), '--self-update-complete'],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                close_fds=True,
            )
        except Exception as e:
            self.logger.critical(f"替换失败: {e}")
            if backup_exe.exists() and not current_exe.exists():
                backup_exe.rename(current_exe)
                self.logger.info("已回滚")
            sys.exit(1)

    @staticmethod
    def rollback() -> None:
        """尝试回滚自身更新"""
        logger = logging.getLogger("M9AUpdateAssistant")
        try:
            backup_exe = Path(sys.executable).with_suffix('.exe.bak')
            if backup_exe.exists():
                current_exe = Path(sys.executable)
                if current_exe.exists():
                    current_exe.unlink()
                backup_exe.rename(current_exe)
                logger.info(f"因为更新失败，将自动回滚: {current_exe}")
            else:
                logger.critical(f"未找到备份文件: {backup_exe}")
        except Exception as e:
            logger.critical(f"回滚失败: {e}")
