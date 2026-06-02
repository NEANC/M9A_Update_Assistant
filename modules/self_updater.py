#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import re
import shutil
import subprocess
import sys
import requests

from pathlib import Path
from typing import Tuple

from modules.config_self_updater import UpdateState
from modules.download_manager import DownloadManager
from modules.github_release_client import GitHubReleaseClient
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
        match = re.search(r'-(alpha|beta|rc)(?:\.?(\d+))?', v)
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

        existing_state = UpdateState.load()
        if existing_state and existing_state.get("State", "state", fallback="") == "failed_disabled":
            failed_ver = existing_state["new_version"]
            self.logger.info(f"版本 {failed_ver} 之前验证失败，跳过自动更新")
            return False

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

            self._replace_executable(tmp_path, sha_path, zip_manager, latest_version)
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

    @staticmethod
    def _wait_for_parent_exit(parent_pid: int, timeout: int = 30) -> bool:
        """
        等待父进程退出

        Args:
            parent_pid: 父进程 PID
            timeout: 超时秒数

        Returns:
            父进程是否在超时前退出
        """
        import ctypes

        logger = logging.getLogger("M9AUpdateAssistant")
        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if not handle:
            logger.warning(f"无法打开父进程句柄 (PID={parent_pid})，等待 3 秒后继续")
            import time
            time.sleep(3)
            return True

        logger.info(f"等待父进程退出 (PID={parent_pid}, 超时={timeout}s)...")
        result = kernel32.WaitForSingleObject(handle, timeout * 1000)
        kernel32.CloseHandle(handle)

        if result == WAIT_OBJECT_0:
            logger.info("父进程已退出")
            return True
        logger.warning(f"等待父进程超时 (结果={result})")
        return False

    @staticmethod
    def _backup_and_replace(state: "UpdateState") -> bool:
        """
        执行文件备份和替换：app.exe → app.backup.exe, app.new.exe → app.exe

        Args:
            state: 更新状态对象

        Returns:
            操作是否成功
        """
        logger = logging.getLogger("M9AUpdateAssistant")
        target = Path(state["target"])
        new_file = Path(state["new_file"])
        backup_file = Path(state["backup_file"])

        if not new_file.exists():
            error_msg = f"新版本文件不存在: {new_file}"
            state["last_error"] = error_msg
            state.save()
            logger.critical(error_msg)
            return False

        try:
            logger.info(f"备份旧版: {target} → {backup_file}")
            shutil.move(str(target), str(backup_file))

            logger.info(f"部署新版: {new_file} → {target}")
            shutil.move(str(new_file), str(target))

            return True
        except OSError as e:
            error_msg = str(e)
            state["last_error"] = error_msg
            state.save()
            logger.critical(f"文件替换失败: {e}")
            if not target.exists() and backup_file.exists():
                shutil.move(str(backup_file), str(target))
                logger.info("已尝试恢复旧版")
            return False

    @staticmethod
    def _verify_new_version(state: "UpdateState") -> bool:
        """
        启动新版程序进行健康检查

        Args:
            state: 更新状态对象

        Returns:
            验证是否通过
        """
        logger = logging.getLogger("M9AUpdateAssistant")
        target = state["target"]
        logger.info("启动新版验证...")

        try:
            result = subprocess.run(
                [target, "--self-update-verify"],
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                logger.info("新版验证通过")
                return True
            logger.warning(f"新版验证失败，退出码: {result.returncode}")
            state["last_error"] = f"新版验证退出码: {result.returncode}"
            state.save()
            return False
        except subprocess.TimeoutExpired:
            logger.critical("新版验证超时")
            state["last_error"] = "新版验证超时"
            state.save()
            return False
        except OSError as e:
            logger.critical(f"启动新版失败: {e}")
            state["last_error"] = str(e)
            state.save()
            return False

    @staticmethod
    def _restore_from_backup(state: "UpdateState") -> bool:
        """
        从备份恢复旧版程序

        Args:
            state: 更新状态对象

        Returns:
            恢复是否成功
        """
        logger = logging.getLogger("M9AUpdateAssistant")
        target = Path(state["target"])
        backup_file = Path(state["backup_file"])

        if target.exists():
            try:
                target.unlink()
            except OSError as e:
                logger.error(f"删除损坏的新版失败: {e}")
                state["last_error"] = str(e)
                state.save()
                return False

        if not backup_file.exists():
            msg = "备份文件不存在，无法恢复"
            logger.critical(msg)
            state["last_error"] = msg
            state.save()
            return False

        try:
            shutil.move(str(backup_file), str(target))
            logger.info(f"已恢复旧版: {target}")
            return True
        except OSError as e:
            logger.critical(f"恢复旧版失败: {e}")
            state["last_error"] = str(e)
            state.save()
            return False

    @staticmethod
    def helper_main(parent_pid: int) -> None:
        """
        app.old.exe 的入口函数

        等待旧版退出 → 替换 → 验证 → 提交或回滚

        Args:
            parent_pid: 旧版进程 PID
        """
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.info("更新助手已启动，等待主进程退出...")

        if not SelfUpdater._wait_for_parent_exit(parent_pid):
            logger.critical("等待主进程退出超时，放弃更新")
            state = UpdateState.load()
            if state:
                state["last_error"] = "等待主进程退出超时"
                state.transition("failed_disabled")
            sys.exit(1)

        state = UpdateState.load()
        if not state:
            logger.critical("未找到更新状态文件，更新中止")
            sys.exit(1)

        state.transition("replacing")

        if not SelfUpdater._backup_and_replace(state):
            logger.critical("文件替换失败")
            sys.exit(1)

        state.transition("pending_new_verify")

        if SelfUpdater._verify_new_version(state):
            state.transition("verified")
            logger.info("新版验证通过，启动新版程序...")
            subprocess.Popen(
                [state["target"]],
                creationflags=subprocess.DETACHED_PROCESS,
            )
        else:
            logger.warning("新版验证失败，正在回滚...")
            state.transition("rollback")
            SelfUpdater._restore_from_backup(state)

            retry_count = int(state.get("Retry", "retry_count", fallback="0"))
            max_retry = int(state.get("Retry", "max_retry", fallback="3"))
            retry_count += 1
            state.set("Retry", "retry_count", str(retry_count))

            if retry_count < max_retry:
                state.transition("rollback_done")
                logger.info(f"重试更新 ({retry_count}/{max_retry})...")
                subprocess.Popen(
                    [state["target"], "--retry-update"],
                    creationflags=subprocess.DETACHED_PROCESS,
                )
            else:
                state.transition("failed_disabled")
                logger.critical(f"更新失败，已达最大重试次数 ({max_retry})")
                subprocess.Popen(
                    [state["target"], "--update-failed"],
                    creationflags=subprocess.DETACHED_PROCESS,
                )

    @staticmethod
    def self_update_verify() -> int:
        """
        新版程序健康检查

        Returns:
            0 表示验证通过，非 0 表示失败
        """
        logger = logging.getLogger("M9AUpdateAssistant")

        state = UpdateState.load()
        if not state:
            logger.critical("未找到更新状态文件")
            return 1

        expected_sha256 = state["expected_sha256"]
        new_version = state["new_version"]

        current_exe = SelfUpdater._get_exe_path()
        actual_sha256 = ZipManager.calculate_sha256(str(current_exe))

        if expected_sha256 and actual_sha256 != expected_sha256:
            logger.critical(
                f"SHA256 不匹配: 期望 {expected_sha256[:16]}..., 实际 {actual_sha256[:16]}..."
            )
            return 2

        import M9A_Update_Assistant as app_module
        if new_version and app_module.VERSION != new_version:
            logger.critical(f"版本号不匹配: 期望 {new_version}, 实际 {app_module.VERSION}")
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

    def _replace_executable(self, tmp_path: Path, sha_path: Path,
                             zip_manager: ZipManager, new_version: str = "") -> None:
        """
        准备替换：复制自身为 helper → 写 INI 状态文件 → 启动 helper → 返回

        Args:
            tmp_path: 已下载的临时新版本文件
            sha_path: SHA256 校验值文件
            zip_manager: ZipManager 实例（用于二次校验）
            new_version: 新版本号
        """
        current_exe = self._get_exe_path()
        stem = current_exe.stem
        helper_exe = current_exe.with_name(f"{stem}.old.exe")
        new_exe = current_exe.with_name(f"{stem}.new.exe")
        backup_exe = current_exe.with_name(f"{stem}.backup.exe")

        expected = ""
        if sha_path.exists():
            expected = sha_path.read_text(encoding='ascii').strip()
            self.logger.info("重新校验更新文件完整性...")
            if not zip_manager.verify_file_sha256(str(tmp_path), expected):
                self.logger.critical("更新文件校验失败，放弃更新")
                tmp_path.unlink(missing_ok=True)
                sha_path.unlink(missing_ok=True)
                raise RuntimeError("SHA256 校验失败")

        shutil.copy2(tmp_path, new_exe)
        self.logger.info(f"新版本已暂存: {new_exe}")

        shutil.copy2(current_exe, helper_exe)
        self.logger.info(f"更新助手已准备: {helper_exe}")

        try:
            import M9A_Update_Assistant as app_module
            old_version = app_module.VERSION
        except ImportError:
            old_version = ""

        state = UpdateState()
        state["state"] = "downloaded_verified"
        state["target"] = str(current_exe)
        state["new_file"] = str(new_exe)
        state["backup_file"] = str(backup_exe)
        state["helper_file"] = str(helper_exe)
        state["old_version"] = old_version
        state["new_version"] = new_version
        state["expected_sha256"] = expected
        state.set("Retry", "retry_count", _get_existing_retry_count())
        state.set("Retry", "max_retry", "3")
        state.save()

        state.transition("helper_started")

        self.logger.info("启动更新助手进程...")
        self.logger.warning("软件将在退出后自动替换")
        subprocess.Popen(
            [str(helper_exe), '--update-helper', '--parent-pid', str(os.getpid())],
            creationflags=subprocess.DETACHED_PROCESS,
        )

        tmp_path.unlink(missing_ok=True)
        sha_path.unlink(missing_ok=True)

    @staticmethod
    def rollback() -> bool:
        """
        从 INI 状态文件读取 backup_file 路径，恢复旧版

        Returns:
            恢复是否成功
        """
        logger = logging.getLogger("M9AUpdateAssistant")
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
