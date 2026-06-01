#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import hashlib
import logging
import re
import shutil
import subprocess
import sys
import requests

from pathlib import Path
from typing import Tuple

from modules.download_manager import DownloadManager
from modules.github_release_client import GitHubReleaseClient
from modules.zip_manager import ZipManager


class SelfUpdater:
    """自更新器，负责自我更新检查、下载、替换、回滚"""

    SELF_UPDATE_REPO = "NEANC/M9A_Update_Assistant"
    ASSET_PATTERN = re.compile(r'^M9A_Update_Assistant-(Nuitka|PyInstaller)-v[\d.]+.*\.exe$')

    def __init__(self, proxy: str, temp_folder: str, logger: logging.Logger,
                 self_update_channel: str = 'preview'):
        """
        初始化自更新器

        Args:
            proxy: 代理地址
            temp_folder: 临时文件夹路径
            logger: 日志记录器
            self_update_channel: 自我更新版本通道 ('preview', 'stable')
        """
        self.proxy = proxy
        self.temp_folder = temp_folder
        self.logger = logger
        self.self_update_channel = self_update_channel

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

        logging.getLogger("M9AUpdateAssistant").debug(f"当前运行模式: {package_type}")
        return is_bundled, package_type

    @staticmethod
    def version_to_tuple(v: str) -> Tuple[int, ...]:
        """将版本号字符串转换为元组用于比较，兼容 vX.Y.Z 和 vX.Y.Z-prerelease.N"""
        try:
            v = v.lstrip('v').split('-')[0]
            core = tuple(map(int, v.split('.')))
            return core
        except Exception:
            return ()

    def _is_prerelease(self, v: str) -> bool:
        """检查版本号是否为预发布版本"""
        return bool(re.search(r'-(alpha|beta|rc)', v))

    def _version_newer_than(self, current: str, latest: str) -> bool:
        """
        比较版本号，latest 是否比 current 新

        预发布 → 正式版始终视为升级
        alpha < beta < rc < stable
        alpha.1 < alpha.2, beta.1 < beta.2, rc.1 < rc.2
        """
        cur_tuple = self.version_to_tuple(current)
        lat_tuple = self.version_to_tuple(latest)
        if not cur_tuple or not lat_tuple:
            return False

        if cur_tuple < lat_tuple:
            return True
        if cur_tuple > lat_tuple:
            return False

        cur_pre = self._is_prerelease(current)
        lat_pre = self._is_prerelease(latest)

        if not cur_pre and lat_pre:
            return False
        if cur_pre and not lat_pre:
            return True
        if cur_pre and lat_pre:
            cur_weight = self._prerelease_weight(current)
            lat_weight = self._prerelease_weight(latest)
            return lat_weight > cur_weight
        return False

    @staticmethod
    def _prerelease_weight(v: str) -> Tuple[int, int]:
        """返回预发布权重：alpha=(1, N), beta=(2, N), rc=(3, N)，缺数字时 N=0"""
        WEIGHT_MAP = {'alpha': 1, 'beta': 2, 'rc': 3}
        match = re.search(r'-(alpha|beta|rc)(?:\.(\d+))?', v)
        if not match:
            return (0, 0)
        kind = match.group(1)
        num = int(match.group(2)) if match.group(2) else 0
        return (WEIGHT_MAP.get(kind, 0), num)

    @staticmethod
    def _is_build_tag(v: str) -> bool:
        """检查版本号是否为构建版本（如 v0.0.1-build.gXXXXXX 或 v1.11.5-beta.5-2-build.ae83e00）"""
        return bool(re.search(r'-build\b', v))

    def _resolve_channel(self) -> str:
        """解析通道配置，兼容旧值 release→preview, latest→stable"""
        if self.self_update_channel in ('preview', 'release'):
            return 'preview'
        if self.self_update_channel in ('stable', 'latest'):
            return 'stable'
        return 'preview'

    def check_self_update(self, current_version: str, gh_client: GitHubReleaseClient,
                           download_manager: DownloadManager,
                           zip_manager: ZipManager) -> bool:
        """
        检查并准备自身更新

        Returns:
            bool: 是否需要退出以完成更新
        """
        self.logger.info("开始检查软件版本...")

        is_bundled, package_type = self.detect_package_type()
        if not is_bundled:
            self.logger.warning("当前为调试模式，跳过更新检查")
            return False

        try:
            headers = {'User-Agent': 'M9A-Update-Assistant'}
            proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None

            channel = self._resolve_channel()
            if channel == 'preview':
                api_url = f"https://api.github.com/repos/{self.SELF_UPDATE_REPO}/releases"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                response.raise_for_status()
                releases = response.json()
                releases = [r for r in releases if not r.get('draft')]
                if not releases:
                    self.logger.error("未找到任何有效的 release")
                    return False
                release_info = releases[0]
            else:
                api_url = f"https://api.github.com/repos/{self.SELF_UPDATE_REPO}/releases/latest"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                response.raise_for_status()
                release_info = response.json()

            latest_version = release_info.get('tag_name', '')
            if not latest_version:
                self.logger.error("未能获取版本号")
                return False

            self.logger.debug(f"远程版本: {latest_version} (通道: {channel})")
            if self._is_build_tag(current_version):
                self.logger.info("当前为 Build 版本，跳过更新")
                return False

            if self._version_newer_than(current_version, latest_version):
                self.logger.info(f"检测到新版本: {latest_version}")
            else:
                cur_tuple = self.version_to_tuple(current_version)
                lat_tuple = self.version_to_tuple(latest_version)
                if cur_tuple and lat_tuple:
                    self.logger.info("当前版本已最新")
                else:
                    self.logger.error("版本号校验错误，跳过更新")
                return False

            exe_url, exe_name = self._match_asset(release_info, package_type)
            if not exe_url:
                return False

            temp_dir = Path(self.temp_folder)
            temp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = temp_dir / "M9A_Update_Assistant_new.exe.tmp"
            sha_path = temp_dir / "M9A_Update_Assistant_new.sha256"

            expected_sha256 = gh_client.get_asset_sha256(release_info, exe_name)
            if not expected_sha256:
                expected_sha256 = gh_client.get_exe_sha256_from_body(release_info, exe_name)

            if not expected_sha256:
                self.logger.critical("Github API 中未找到 SHA256 校验值，跳过更新")
                return False

            sha_path.write_text(expected_sha256, encoding='ascii')
            self.logger.debug(f"已保存 SHA256 校验值: {sha_path}")

            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    self.logger.info(f"重试下载更新文件（{attempt + 1}/{max_retries}）")
                else:
                    self.logger.debug(f"开始下载: {exe_url}")

                if not download_manager.download_file_with_progress(exe_url, str(tmp_path)):
                    self.logger.error("下载失败")
                    continue

                if zip_manager.verify_file_sha256(str(tmp_path), expected_sha256):
                    break
                self.logger.error("SHA256 校验失败，准备重试")

            else:
                self.logger.critical("软件更新下载校验失败，已达到最大重试次数，跳过更新")
                tmp_path.unlink(missing_ok=True)
                sha_path.unlink(missing_ok=True)
                return False

            self.logger.info("新版本已下载并校验通过")
            self.logger.warning("软件将在退出后自动替换")

            self._replace_executable(tmp_path, sha_path, zip_manager)
            return True

        except requests.RequestException as e:
            self.logger.critical(f"获取 GitHub release 信息失败: {e}")
            return False
        except Exception as e:
            self.logger.critical(f"检查软件更新时出错: {e}")
            return False

    def _match_asset(self, release_info, package_type: str) -> Tuple[str, str]:
        """严格匹配 asset：正则校验命名格式，Nuitka 优先，PyInstaller 回退"""
        primary_keyword = package_type
        secondary_keyword = "PyInstaller" if package_type == "Nuitka" else "Nuitka"
        assets = release_info.get('assets', [])

        candidates = []
        for asset in assets:
            asset_name = asset.get('name', '')
            if self.ASSET_PATTERN.match(asset_name):
                candidates.append(asset_name)
                self.logger.debug(f"候选 asset: {asset_name} ({asset.get('size', 0) / (1024*1024):.2f} MB)")

        for asset in assets:
            asset_name = asset.get('name', '')
            if self.ASSET_PATTERN.match(asset_name) and primary_keyword in asset_name:
                self.logger.info(f"找到 {primary_keyword} 版本: {asset_name}")
                return asset.get('browser_download_url', ''), asset_name

        for asset in assets:
            asset_name = asset.get('name', '')
            if self.ASSET_PATTERN.match(asset_name) and secondary_keyword in asset_name:
                self.logger.info(f"未找到 {primary_keyword} 版本，降级使用 {secondary_keyword} 版本: {asset_name}")
                return asset.get('browser_download_url', ''), asset_name

        self.logger.critical("未找到符合命名规范的 exe 文件")
        return '', ''

    @staticmethod
    def _get_exe_path() -> Path:
        """
        获取当前可执行文件的真实路径
        sys.argv[0] 在所有打包模式下均指向用户双击的真实 exe
        """
        argv_exe = Path(sys.argv[0]).resolve()
        if argv_exe.suffix.lower() == '.exe':
            return argv_exe
        return Path(sys.executable).resolve()

    def _replace_executable(self, tmp_path: Path, sha_path: Path,
                             zip_manager=None) -> None:
        """
        合并的 exe 替换逻辑：供 check_self_update() 和 --self-update 共用

        1. SHA256 二次校验
        2. 同盘暂存 → 原子替换
        3. 带版本号备份
        4. 启动新 exe --self-update-complete
        """
        current_exe = self._get_exe_path()
        backup_exe = current_exe.with_name(f"{current_exe.name}.bak")

        if not tmp_path.exists():
            raise RuntimeError(f"更新文件不存在: {tmp_path}")

        if sha_path.exists():
            expected = sha_path.read_text(encoding='ascii').strip()
            self.logger.info("重新校验更新文件完整性...")
            if zip_manager:
                ok = zip_manager.verify_file_sha256(str(tmp_path), expected)
            else:
                actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
                ok = (actual == expected)
            if not ok:
                self.logger.critical("更新文件校验失败，放弃更新")
                tmp_path.unlink(missing_ok=True)
                sha_path.unlink(missing_ok=True)
                raise RuntimeError("SHA256 校验失败")

        staged_path = current_exe.with_name(f"{current_exe.name}.new")
        shutil.copy2(tmp_path, staged_path)

        try:
            if backup_exe.exists():
                backup_exe.unlink()
            current_exe.rename(backup_exe)
            self.logger.info(f"已备份原程序: {backup_exe}")

            staged_path.rename(current_exe)
            self.logger.info(f"已替换为新程序: {current_exe}")

            tmp_path.unlink(missing_ok=True)
            sha_path.unlink(missing_ok=True)

            subprocess.Popen(
                [str(current_exe), '--self-update-complete'],
                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
            )
        except Exception:
            self.logger.critical("替换失败")
            if backup_exe.exists() and not current_exe.exists():
                backup_exe.rename(current_exe)
                self.logger.info("已回滚")
            raise

    @staticmethod
    def rollback() -> None:
        """尝试回滚自身更新"""
        logger = logging.getLogger("M9AUpdateAssistant")
        try:
            current_exe = SelfUpdater._get_exe_path()
            backup_exe = current_exe.with_name(f"{current_exe.name}.bak")
            if backup_exe.exists():
                if current_exe.exists():
                    current_exe.unlink()
                backup_exe.rename(current_exe)
                logger.info(f"因为更新失败，将自动回滚: {current_exe}")
            else:
                logger.critical(f"未找到备份文件: {backup_exe}")
        except Exception as e:
            logger.critical(f"回滚失败: {e}")
