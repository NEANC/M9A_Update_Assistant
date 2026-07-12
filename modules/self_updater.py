#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import requests

from pathlib import Path
from typing import Optional, Tuple

from modules.config_self_updater import UpdateState
from modules.download_manager import DownloadManager
from modules.github_release_client import GitHubReleaseClient
from modules.ps1_fragments import (
    generate_common_base_functions_ps1,
    generate_common_state_functions_ps1,
    generate_helper_cleanup_functions_ps1,
    generate_helper_launch_functions_ps1,
    generate_helper_orchestration_functions_ps1,
    generate_helper_process_functions_ps1,
    generate_helper_rollback_functions_ps1,
    generate_move_with_retry_ps1,
    generate_sha256_function_ps1,
)
from modules.zip_manager import ZipManager


def _get_existing_retry_count() -> str:
    """读取已存在的 update_state.ini 中的 retry_count，若无则返回 '0'"""
    existing = UpdateState.load()
    if existing:
        return existing.get("Retry", "retry_count", fallback="0")
    return "0"


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
        # 使用 sys.argv[0] 统一判断源码模式：.py 脚本即为源码运行
        # 在 PyInstaller/Nuitka 打包后，sys.argv[0] 指向 .exe，不会以 .py 结尾
        is_py_script = Path(sys.argv[0]).suffix.lower() == '.py'
        # 兜底：检查打包标识
        is_pyinstaller = getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')
        is_nuitka = hasattr(sys, '__compiled__')

        is_bundled = (not is_py_script) or is_pyinstaller or is_nuitka

        logger = logging.getLogger("M9AUpdateAssistant")

        if is_pyinstaller:
            local_package_type = "PyInstaller"
        elif is_nuitka:
            local_package_type = "Nuitka"
        else:
            local_package_type = "Nuitka"

        if is_bundled:
            logger.debug(f"运行环境: {local_package_type}")
        else:
            logger.debug(f"运行环境: 源码模式")

        return is_bundled, local_package_type

    @staticmethod
    def _is_within_directory(path: Path, directory: Path) -> bool:
        """判断 path 解析后是否位于 directory 解析后的目录内。"""
        try:
            path.resolve().relative_to(directory.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _cleanup_update_residue(logger: logging.Logger, not_delete: bool = False) -> None:
        """按状态文件记录路径清理上次成功更新后的运行时残留。"""
        state = UpdateState.load()
        if not state:
            return

        current_state = state.get("State", "state", fallback="")
        if current_state != "verified":
            return

        logger.info("清理上次更新残留文件...")
        target = state.get("Files", "target", fallback="")
        target_path = Path(target) if target else None
        runtime_dir = state.get("Files", "runtime_dir", fallback="")
        runtime_path = Path(runtime_dir) if runtime_dir else None

        cleanup_files = []
        seen_cleanup_files = set()

        def add_cleanup_file(file_path: Path) -> None:
            """添加去重后的待清理文件。"""
            resolved_path = file_path.resolve()
            if resolved_path in seen_cleanup_files:
                return
            seen_cleanup_files.add(resolved_path)
            cleanup_files.append(file_path)

        runtime_file_keys = ("helper_ps1", "update_ps1", "lock_file", "new_file", "backup_file")
        for key in runtime_file_keys:
            file_path = state.get("Files", key, fallback="")
            if not file_path:
                continue
            path = Path(file_path)
            if runtime_path and not SelfUpdater._is_within_directory(path, runtime_path):
                logger.warning(f"跳过越界残留文件: {path}")
                continue
            add_cleanup_file(path)

        if target_path:
            legacy_program_dir = target_path.parent
            legacy_file_names = (
                "M9A_Update_Assistant_Update_Helper.ps1",
                "M9A_Update_Assistant_Update.ps1",
                "update_started.lock",
                f"{target_path.stem}.old.exe",
                f"{target_path.stem}.new.exe",
                f"{target_path.stem}.backup.exe",
            )
            for file_name in legacy_file_names:
                add_cleanup_file(legacy_program_dir / file_name)

        log_file = state.get("Files", "log_file", fallback="")
        allowed_log_file = target_path.parent / "update.log" if target_path else None
        if log_file and allowed_log_file:
            recorded_log_file = Path(log_file)
            if recorded_log_file.resolve() == allowed_log_file.resolve():
                add_cleanup_file(recorded_log_file)
            else:
                logger.warning(f"跳过越界日志文件: {recorded_log_file}")
        elif allowed_log_file:
            add_cleanup_file(allowed_log_file)

        for file_path in cleanup_files:
            try:
                if file_path.exists() and file_path.is_file():
                    file_path.unlink()
                    logger.debug(f"已删除残留文件: {file_path}")
            except OSError as e:
                logger.warning(f"删除残留文件失败: {file_path}，{e}")

        if runtime_path:
            try:
                runtime_path.rmdir()
            except OSError as e:
                logger.warning(f"删除自更新运行时目录失败: {runtime_path}，{e}")

        state.delete()
        logger.info("残留文件清理完成")

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
        return bool(re.search(r'-(alpha|beta|rc)', v, re.IGNORECASE))

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
        match = re.search(r'-(alpha|beta|rc)(?:[-.]?(\d+))?', v, re.IGNORECASE)
        if not match:
            return (0, 0)
        kind = match.group(1).lower()
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

    def _match_release_by_tag(self, current_version: str,
                               headers: dict, proxies: dict) -> Optional[dict]:
        """遍历 releases 列表，按 tag_name 大小写不敏感匹配当前版本"""
        try:
            api_url = f"https://api.github.com/repos/{self.SELF_UPDATE_REPO}/releases"
            params = {'per_page': 50}
            response = requests.get(api_url, headers=headers, proxies=proxies,
                                    params=params, timeout=30)
            response.raise_for_status()
            for release in response.json():
                if release.get('draft'):
                    continue
                if release.get('tag_name', '').lower() == current_version.lower():
                    return release
        except requests.RequestException as e:
            self.logger.debug(f"遍历 releases 匹配失败: {e}")
        return None

    def _fetch_current_release_sha256(self, current_version: str,
                                       package_type: str) -> str:
        """
        从 GitHub API 获取当前版本的 exe asset 的 SHA256

        Args:
            current_version: 当前版本号（如 v1.13.0-beta）
            package_type: 打包方式（Nuitka / PyInstaller）

        Returns:
            SHA256 值，获取失败返回空字符串
        """
        try:
            api_url = (
                f"https://api.github.com/repos/{self.SELF_UPDATE_REPO}"
                f"/releases/tags/{current_version}"
            )
            headers = {'User-Agent': 'M9A-Update-Assistant'}
            proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None

            self.logger.debug(f"获取当前版本 release 信息: {api_url}")
            response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
            if response.status_code == 404:
                self.logger.debug(f"tag 名称精确匹配未找到，尝试大小写不敏感匹配...")
                release_info = self._match_release_by_tag(current_version, headers, proxies)
            else:
                response.raise_for_status()
                release_info = response.json()

            if not release_info:
                self.logger.warning(f"GitHub 上未找到当前版本 {current_version} 的 release")
                return ""

            _, exe_name = self._match_asset(release_info, package_type)
            if not exe_name:
                self.logger.warning("当前版本 exe asset 未匹配到对应文件")
                return ""

            assets = release_info.get('assets', [])
            for asset in assets:
                if asset.get('name') == exe_name:
                    digest = asset.get('digest', '')
                    if digest.startswith('sha256:'):
                        return digest[7:]
            self.logger.warning("当前版本 release 中未找到对应的 SHA256 值")
            return ""
        except requests.RequestException as e:
            self.logger.warning(f"获取当前版本 SHA256 失败: {e}")
            return ""
        except Exception as e:
            self.logger.warning(f"获取当前版本 SHA256 时出错: {e}")
            return ""

    def _check_system_environment(self) -> bool:
        """
        检查当前系统是否支持自我更新：
          - Windows 操作系统
          - PowerShell 5.1 或更高版本
        """
        if sys.platform != 'win32':
            self.logger.critical("自我更新仅支持 Windows 操作系统")
            return False

        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.Major'],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                self.logger.critical("无法获取 PowerShell 版本信息")
                return False
            major_ver = int(result.stdout.strip())
            if major_ver < 5:
                self.logger.critical(
                    f"PowerShell 版本过低: {major_ver}.x，需要 5.1 或更高版本"
                )
                return False
            self.logger.debug(f"PowerShell 版本: {major_ver}.x，环境检查通过")
        except (ValueError, subprocess.TimeoutExpired) as e:
            self.logger.critical(f"检测 PowerShell 版本失败: {e}")
            return False

        return True

    def check_self_update(self, current_version: str, gh_client: GitHubReleaseClient,
                           download_manager: DownloadManager,
                           zip_manager: ZipManager,
                           force: bool = False,
                           is_bundled: Optional[bool] = None,
                           package_type: Optional[str] = None) -> bool:
        """
        检查并准备自身更新

        Args:
            current_version: 当前版本号
            gh_client: GitHub API 客户端
            download_manager: 下载管理器
            zip_manager: ZIP 管理器
            force: 是否强制更新
            is_bundled: 外部预检测的是否为打包程序（可选，避免重复调用 detect_package_type）
            package_type: 外部预检测的打包方式（可选）

        Returns:
            bool: 是否需要退出以完成更新
        """
        self.logger.info("正在检查软件更新...")

        # ── 系统环境检查 ──
        if not self._check_system_environment():
            return False

        if is_bundled is None:
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
            if not force and self._is_build_tag(current_version):
                self.logger.info("当前为 Build 版本，跳过更新")
                return False

            if not force:
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
            else:
                self.logger.info(f"检测到 --Update-Force，将强制更新到: {latest_version} 版本")

            existing_state = UpdateState.load()
            if existing_state and existing_state.get("State", "state", fallback="") == "failed_disabled":
                failed_ver = existing_state["new_version"]
                if failed_ver == latest_version:
                    self.logger.info(f"版本 {latest_version} 存在更新失败记录，跳过更新")
                    return False
                self.logger.debug(f"新版本 {latest_version} 与失败记录版本 {failed_ver} 不同，清除失败状态继续更新")
                existing_state.delete()

            exe_url, exe_name = self._match_asset(release_info, package_type)
            if not exe_url:
                return False

            temp_dir = Path(self.temp_folder)
            cache_dir = temp_dir / "UpdateCache" / "installs" / latest_version
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_dir / f"M9A_Update_Assistant-{latest_version}.exe"
            sha_path = cache_dir / f"M9A_Update_Assistant-{latest_version}.sha256"

            new_sha256 = gh_client.get_asset_sha256(release_info, exe_name)
            if not new_sha256:
                new_sha256 = gh_client.get_exe_sha256_from_body(release_info, exe_name)

            if not new_sha256:
                self.logger.critical("Github API 中未找到 SHA256 校验值，跳过更新")
                return False

            old_sha256 = self._fetch_current_release_sha256(current_version, package_type)
            if old_sha256:
                current_exe = self._get_exe_path()
                actual_current = ZipManager.calculate_sha256(str(current_exe))
                if actual_current != old_sha256:
                    self.logger.critical("当前程序 SHA256 与 GitHub 记录不一致，放弃更新")
                    self.logger.warning(f"GitHub: {old_sha256}")
                    self.logger.warning(f"本地:   {actual_current}")
                    return False
                self.logger.info("当前版本 SHA256 校验通过")
            else:
                self.logger.warning("未能从 GitHub 获取当前版本 SHA256，跳过自身完整性校验")

            sha_path.write_text(new_sha256, encoding='ascii')
            self.logger.debug(f"已保存 SHA256 校验值: {sha_path}")

            # ── 检查缓存：若已存在对应版本文件，优先用 GitHub API 的 SHA256 校验 ──
            if tmp_path.exists():
                cached_valid = zip_manager.verify_file_sha256(str(tmp_path), new_sha256)
                if cached_valid:
                    self.logger.info(f"缓存文件校验通过，跳过下载: {tmp_path}")
                else:
                    self.logger.warning(f"缓存文件 SHA256 校验失败，将重新下载: {tmp_path}")
                    tmp_path.unlink(missing_ok=True)
                    sha_path.unlink(missing_ok=True)
            else:
                cached_valid = False

            if not cached_valid:
                max_retries = 3
                for attempt in range(max_retries):
                    file_name = Path(exe_url).name
                    if attempt > 0:
                        self.logger.info(f"重试下载更新文件（{attempt + 1}/{max_retries}）: {file_name}")
                    else:
                        self.logger.info(f"开始下载更新文件: {file_name}")
                    self.logger.debug(f"下载 URL: {exe_url}")

                    if not download_manager.download_file_with_progress(exe_url, str(tmp_path)):
                        self.logger.error("下载失败")
                        continue

                    if zip_manager.verify_file_sha256(str(tmp_path), new_sha256):
                        break
                    self.logger.error("SHA256 校验失败，准备重试")
                else:
                    self.logger.critical("软件更新下载校验失败，已达到最大重试次数，跳过更新")
                    tmp_path.unlink(missing_ok=True)
                    sha_path.unlink(missing_ok=True)
                    return False

                self.logger.info("新版本已下载并校验通过")

            self._replace_executable(tmp_path, sha_path, latest_version,
                                      old_sha256, new_sha256)
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

    def _get_update_runtime_dir(self, exe_path: Path, new_version: str) -> Path:
        """按生产路径策略获取指定版本的自更新运行时目录。"""
        return self._build_update_runtime_paths(exe_path, new_version)['runtime_dir']

    def _get_update_file_paths(
            self,
            exe_path: Path,
            new_version: str) -> dict[str, Path]:
        """构建自更新运行时文件路径，委托生产布局策略派生路径。"""
        current_exe = Path(exe_path).resolve()
        paths = self._build_update_runtime_paths(current_exe, new_version)
        return paths

    def _resolve_runtime_dir(self, program_dir: Path, new_version: str) -> Path:
        """
        解析并创建最终自更新运行时目录。

        temp_folder 配置优先，LOCALAPPDATA 仅在 temp_folder 为空时使用；LOCALAPPDATA
        最终目录创建失败时才回退到 program_dir/SelfUpdate/new_version。
        状态文件和日志文件由调用方继续保留在 program_dir。
        """
        program_path = Path(program_dir).resolve()
        if self.temp_folder:
            runtime_dir = Path(self.temp_folder).resolve() / new_version
            runtime_dir.mkdir(parents=True, exist_ok=True)
            return runtime_dir

        localappdata = os.environ.get('LOCALAPPDATA')
        if localappdata:
            runtime_dir = Path(localappdata) / 'M9A_Update_Assistant' / 'SelfUpdate' / new_version
            try:
                runtime_dir.mkdir(parents=True, exist_ok=True)
                return runtime_dir
            except OSError as e:
                self.logger.debug(f"创建 LOCALAPPDATA 自更新目录失败，回退到程序目录: {e}")

        runtime_dir = program_path / 'SelfUpdate' / new_version
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return runtime_dir

    def _build_update_runtime_paths(self, current_exe: Path, new_version: str) -> dict[str, Path]:
        """
        构建自更新运行时路径字典。

        runtime_dir 按 temp_folder、LOCALAPPDATA、program_dir fallback 的顺序解析；
        update_state.ini 与 update.log 保留在 program_dir，供替换和回滚流程复用。
        """
        exe_path = Path(current_exe).resolve()
        program_dir = exe_path.parent
        runtime_dir = self._resolve_runtime_dir(program_dir, new_version)
        temp_folder = runtime_dir.parent
        return {
            'program_dir': program_dir,
            'state_file': program_dir / UpdateState.STATE_FILE_NAME,
            'log_file': program_dir / 'update.log',
            'temp_folder': temp_folder,
            'runtime_dir': runtime_dir,
            'helper_ps1': runtime_dir / "M9A_Update_Assistant_Update_Helper.ps1",
            'update_ps1': runtime_dir / "M9A_Update_Assistant_Update.ps1",
            'lock_file': runtime_dir / "update_started.lock",
            'new_file': runtime_dir / f"{exe_path.stem}.new.exe",
            'backup_file': runtime_dir / f"{exe_path.stem}.backup.exe",
        }

    @staticmethod
    def _ps_quote(path: Path) -> str:
        """转义 PowerShell 双引号字符串中的路径内容。"""
        return str(path).replace('`', '``').replace('$', '`$').replace('"', '`"')

    @staticmethod
    def _generate_helper_ps1(paths: dict[str, Path]) -> None:
        """
        生成 M9A_Update_Assistant_Update_Helper.ps1

        Args:
            paths: 自更新运行时绝对路径字典
        """
        runtime_dir = SelfUpdater._ps_quote(paths['runtime_dir'])
        state_file = SelfUpdater._ps_quote(paths['state_file'])
        log_file = SelfUpdater._ps_quote(paths['log_file'])
        lock_file = SelfUpdater._ps_quote(paths['lock_file'])
        update_ps1 = SelfUpdater._ps_quote(paths['update_ps1'])
        ps1_content = textwrap.dedent(r"""
            <#
            .SYNOPSIS
                M9A_Update_Assistant_Update_Helper
            .DESCRIPTION
                等待主进程退出 → 调用 update.ps1 替换 → 验证新版 → 提交或回滚
            #>
            param([int]$ParentPid)

            $scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
            $scriptName = Split-Path -Leaf $MyInvocation.MyCommand.Path
            $scriptTag  = ($scriptName -split '_')[-1]
            $runtimeDir = "__RUNTIME_DIR__"
            $stateFile = "__STATE_FILE__"
            $logFile = "__LOG_FILE__"
            $lockFile = "__LOCK_FILE__"
            $updatePs1 = "__UPDATE_PS1__"

            try { New-Item -Path $lockFile -ItemType File -Force | Out-Null } catch {}

            __COMMON_BASE_FUNCTIONS__

            __SHA256_FUNCTION__

            __COMMON_STATE_FUNCTIONS__

            __MOVE_WITH_RETRY_FUNCTION__

            __HELPER_PROCESS_FUNCTIONS__

            __HELPER_CLEANUP_FUNCTIONS__

            __HELPER_LAUNCH_FUNCTIONS__

            __HELPER_ROLLBACK_FUNCTIONS__

            __HELPER_ORCHESTRATION_FUNCTIONS__

            Run-UpdateAndVerify $ParentPid
        """).lstrip("\n")

        common_base_functions = generate_common_base_functions_ps1().rstrip()
        sha256_function = generate_sha256_function_ps1().rstrip()
        common_state_functions = generate_common_state_functions_ps1().rstrip()
        move_with_retry_function = generate_move_with_retry_ps1().rstrip()
        helper_process_functions = generate_helper_process_functions_ps1().rstrip()
        helper_cleanup_functions = generate_helper_cleanup_functions_ps1().rstrip()
        helper_launch_functions = generate_helper_launch_functions_ps1().rstrip()
        helper_rollback_functions = generate_helper_rollback_functions_ps1().rstrip()
        helper_orchestration_functions = generate_helper_orchestration_functions_ps1().rstrip()
        ps1_content = ps1_content.replace("__RUNTIME_DIR__", runtime_dir)
        ps1_content = ps1_content.replace("__STATE_FILE__", state_file)
        ps1_content = ps1_content.replace("__LOG_FILE__", log_file)
        ps1_content = ps1_content.replace("__LOCK_FILE__", lock_file)
        ps1_content = ps1_content.replace("__UPDATE_PS1__", update_ps1)
        ps1_content = ps1_content.replace("__COMMON_BASE_FUNCTIONS__", common_base_functions)
        ps1_content = ps1_content.replace("__SHA256_FUNCTION__", sha256_function)
        ps1_content = ps1_content.replace("__COMMON_STATE_FUNCTIONS__", common_state_functions)
        ps1_content = ps1_content.replace("__MOVE_WITH_RETRY_FUNCTION__", move_with_retry_function)
        ps1_content = ps1_content.replace("__HELPER_PROCESS_FUNCTIONS__", helper_process_functions)
        ps1_content = ps1_content.replace("__HELPER_CLEANUP_FUNCTIONS__", helper_cleanup_functions)
        ps1_content = ps1_content.replace("__HELPER_LAUNCH_FUNCTIONS__", helper_launch_functions)
        ps1_content = ps1_content.replace("__HELPER_ROLLBACK_FUNCTIONS__", helper_rollback_functions)
        ps1_content = ps1_content.replace("__HELPER_ORCHESTRATION_FUNCTIONS__", helper_orchestration_functions)

        script_path = paths['helper_ps1']
        with open(script_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
            f.write(ps1_content)

    @staticmethod
    def _generate_update_ps1(paths: dict[str, Path]) -> None:
        """
        生成 M9A_Update_Assistant_Update.ps1

        Args:
            paths: 自更新运行时绝对路径字典
        """
        runtime_dir = SelfUpdater._ps_quote(paths['runtime_dir'])
        state_file = SelfUpdater._ps_quote(paths['state_file'])
        log_file = SelfUpdater._ps_quote(paths['log_file'])
        ps1_content = textwrap.dedent(r"""
            <#
            .SYNOPSIS
                M9A_Update_Assistant_Update
            .DESCRIPTION
                替换 app.exe 为新版本：app.exe → app.backup.exe, app.new.exe → app.exe
            #>

            $scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
            $scriptName = Split-Path -Leaf $MyInvocation.MyCommand.Path
            $scriptTag  = ($scriptName -split '_')[-1]
            $runtimeDir = "__RUNTIME_DIR__"
            $stateFile = "__STATE_FILE__"
            $logFile = "__LOG_FILE__"

            __COMMON_BASE_FUNCTIONS__

            __SHA256_FUNCTION__

            __COMMON_STATE_FUNCTIONS__

            __MOVE_WITH_RETRY_FUNCTION__

            try {
                Set-UpdateStatus "replacing" "read_state" "读取更新状态文件" 35 "INFO"

                $target    = Read-IniValue "Files" "target"
                $newFile   = Read-IniValue "Files" "new_file"
                $backup    = Read-IniValue "Files" "backup_file"
                $newSha256 = Read-IniValue "Version" "new_sha256"

                Assert-NotEmpty "Files.target" $target
                Assert-NotEmpty "Files.new_file" $newFile
                Assert-NotEmpty "Files.backup_file" $backup
                if ($target -eq $newFile -or $target -eq $backup -or $newFile -eq $backup) {
                    throw "invalid file paths: target/new_file/backup_file must be different"
                }

                Set-UpdateStatus "replacing" "check_new_file" "检查新版本文件是否存在: $newFile" 40 "INFO"
                if (!(Test-Path -LiteralPath $newFile)) {
                    throw "new file not found: $newFile"
                }

                if ($newSha256) {
                    Set-UpdateStatus "replacing" "verify_new_file_hash" "校验新版本文件 SHA256" 45 "INFO"
                    $actual = Get-SHA256 $newFile
                    if ($actual -ne $newSha256.ToLowerInvariant()) {
                        throw "new file SHA256 mismatch: expected $newSha256, got $actual"
                    }
                }

                if (Test-Path -LiteralPath $backup) {
                    Set-UpdateStatus "replacing" "remove_old_backup" "删除旧备份文件: $backup" 50 "INFO"
                    Remove-Item -LiteralPath $backup -Force -ErrorAction Stop
                }

                if (Test-Path -LiteralPath $target) {
                    Set-UpdateStatus "replacing" "move_target_to_backup" "备份当前程序: $target -> $backup" 55 "INFO"
                    Move-WithRetry $target $backup 60
                }

                Set-UpdateStatus "replacing" "move_new_to_target" "替换为新版本: $newFile -> $target" 60 "INFO"
                Move-WithRetry $newFile $target 60

                Set-UpdateStatus "replacing" "replace_done" "文件替换完成" 65 "INFO"
                exit 0
            } catch {
                Set-UpdateStatus "failed_disabled" "replace_failed" "文件替换失败: $($_.Exception.Message)" 100 "ERROR"
                Write-Error $_.Exception.Message
                exit 1
            }
        """).lstrip("\n")

        common_base_functions = generate_common_base_functions_ps1().rstrip()
        sha256_function = generate_sha256_function_ps1().rstrip()
        common_state_functions = generate_common_state_functions_ps1().rstrip()
        move_with_retry_function = generate_move_with_retry_ps1().rstrip()
        ps1_content = ps1_content.replace("__RUNTIME_DIR__", runtime_dir)
        ps1_content = ps1_content.replace("__STATE_FILE__", state_file)
        ps1_content = ps1_content.replace("__LOG_FILE__", log_file)
        ps1_content = ps1_content.replace("__COMMON_BASE_FUNCTIONS__", common_base_functions)
        ps1_content = ps1_content.replace("__SHA256_FUNCTION__", sha256_function)
        ps1_content = ps1_content.replace("__COMMON_STATE_FUNCTIONS__", common_state_functions)
        ps1_content = ps1_content.replace("__MOVE_WITH_RETRY_FUNCTION__", move_with_retry_function)

        script_path = paths['update_ps1']
        with open(script_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
            f.write(ps1_content)

    def _replace_executable(self, tmp_path: Path, sha_path: Path,
                             new_version: str, old_sha256: str,
                             new_sha256: str) -> None:
        """
        准备替换：生成 helper.ps1 / update.ps1 → 写 INI 状态文件 → 启动 PowerShell → 握手退出

        Args:
            tmp_path: 已下载并通过 SHA256 校验的新版本文件
            sha_path: SHA256 校验值文件
            new_version: 新版本号
            old_sha256: 旧版本 SHA256（来自 GitHub API）
            new_sha256: 新版本 SHA256
        """
        current_exe = self._get_exe_path()
        paths = self._build_update_runtime_paths(current_exe, new_version)
        paths['runtime_dir'].mkdir(parents=True, exist_ok=True)
        new_exe = paths['new_file']
        backup_exe = paths['backup_file']

        shutil.copy2(tmp_path, new_exe)
        self.logger.info(f"新版本已暂存: {new_exe}")

        try:
            from modules.version import VERSION as old_version
        except ImportError:
            old_version = ""

        state = UpdateState()
        state["state"] = "downloaded_verified"
        state["target"] = str(current_exe)
        state["new_file"] = str(new_exe)
        state["backup_file"] = str(backup_exe)
        state.set("Files", "runtime_dir", str(paths['runtime_dir']))
        state.set("Files", "helper_ps1", str(paths['helper_ps1']))
        state.set("Files", "update_ps1", str(paths['update_ps1']))
        state.set("Files", "lock_file", str(paths['lock_file']))
        state.set("Files", "log_file", str(paths['log_file']))
        state["old_version"] = old_version
        state["new_version"] = new_version
        state["old_sha256"] = old_sha256
        state["new_sha256"] = new_sha256
        state.set("Retry", "retry_count", _get_existing_retry_count())
        state.set("Retry", "max_retry", "3")
        state.save()

        self._generate_helper_ps1(paths)
        self._generate_update_ps1(paths)
        self.logger.info(f"已生成更新脚本到目录: {paths['runtime_dir']}")

        state.transition("helper_started")

        self.logger.info("启动更新进程...")
        lock_file = paths['lock_file']
        helper_ps1 = paths['helper_ps1']
        if lock_file.exists():
            lock_file.unlink()

        proc = subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(helper_ps1),
                "-ParentPid", str(os.getpid()),
            ],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )

        deadline = time.time() + 15
        while time.time() < deadline:
            if lock_file.exists():
                return
            if proc.poll() is not None:
                raise RuntimeError(
                    f"启动更新脚本失败：helper.ps1 异常退出，退出码 {proc.returncode}"
                )
            time.sleep(0.1)

        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError("启动更新脚本失败：helper.ps1 未在 15 秒内就绪")

    @staticmethod
    def self_update_verify() -> int:
        """
        新版程序健康检查

        优先从命令行参数 --expected-sha256 / --expected-version 获取期望值，
        若无则回退到 update_state.ini 读取。

        Returns:
            0 表示验证通过，非 0 表示失败
        """
        logger = logging.getLogger("M9AUpdateAssistant")

        expected_sha256 = ""
        new_version = ""
        try:
            sha_idx = sys.argv.index("--expected-sha256")
            expected_sha256 = sys.argv[sha_idx + 1]
            ver_idx = sys.argv.index("--expected-version")
            new_version = sys.argv[ver_idx + 1]
        except (ValueError, IndexError):
            state = UpdateState.load()
            if state:
                expected_sha256 = state["new_sha256"]
                new_version = state["new_version"]

        if not expected_sha256:
            logger.critical("在 GitHub API 中未找到 SHA256，无法验证")
            return 1

        current_exe = SelfUpdater._get_exe_path()
        actual_sha256 = ZipManager.calculate_sha256(str(current_exe))

        if expected_sha256 and actual_sha256 != expected_sha256:
            logger.critical(
                f"SHA256 不匹配: \n"
                f"GitHub: {expected_sha256}\n"
                f"本地:   {actual_sha256}"
            )
            return 2

        from modules.version import VERSION as actual_version
        if new_version and actual_version != new_version:
            logger.critical(
                f"版本号不匹配: \n"
                f"GitHub: {new_version}\n"
                f"本地:   {actual_version}")
            return 3

        try:
            from modules.config_manager import ConfigManager
            from modules.github_release_client import GitHubReleaseClient
            from modules.download_manager import DownloadManager
        except ImportError as e:
            logger.critical(f"核心模块导入失败: {e}")
            return 4

        logger.info("新版验证全部通过")
        return 0

    @staticmethod
    def rollback(logger: Optional[logging.Logger] = None) -> bool:
        """
        从 INI 状态文件读取 backup_file 路径，恢复旧版

        Returns:
            恢复是否成功
        """
        logger = logger or logging.getLogger("M9AUpdateAssistant")
        state = UpdateState.load()
        if not state:
            logger.critical("未找到更新状态文件，无法回滚")
            return False

        backup_file = Path(state["backup_file"])
        target = Path(state["target"])

        if not backup_file.exists():
            logger.critical(f"备份文件不存在: {backup_file}")
            return False

        try:
            if target.exists():
                target.unlink()
            backup_file.rename(target)
            logger.info(f"已回滚: {target}")
            state.transition("rollback_done")
            return True
        except OSError as e:
            logger.critical(f"回滚失败: {e}")
            return False

    @staticmethod
    def clean_self_update_cache(temp_folder: str, logger: logging.Logger) -> None:
        """
        清理自更新缓存目录 UpdateCache

        Args:
            temp_folder: 临时文件夹路径
            logger: 日志记录器
        """
        cache_dir = Path(temp_folder) / "UpdateCache"
        if not cache_dir.exists():
            return
        try:
            shutil.rmtree(cache_dir)
            logger.info(f"已清理自更新缓存: {cache_dir}")
        except OSError as e:
            logger.warning(f"清理自更新缓存失败: {e}")
