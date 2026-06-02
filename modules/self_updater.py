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
                self.logger.warning(f"GitHub 上未找到当前版本 {current_version} 的 release")
                return ""
            response.raise_for_status()
            release_info = response.json()

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
            self.logger.warning("当前版本 release 中未找到 SHA256 digest")
            return ""
        except requests.RequestException as e:
            self.logger.warning(f"获取当前版本 SHA256 失败: {e}")
            return ""
        except Exception as e:
            self.logger.warning(f"获取当前版本 SHA256 时出错: {e}")
            return ""

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
            tmp_path = cache_dir / f"M9A_Update_Assistant-{latest_version}.exe.tmp"
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
                    self.logger.warning(f"GitHub: {old_sha256[:16]}...")
                    self.logger.warning(f"本地:   {actual_current[:16]}...")
                    return False
                self.logger.info("当前版本 SHA256 校验通过")
            else:
                self.logger.warning("未能从 GitHub 获取当前版本 SHA256，跳过自身完整性校验")

            sha_path.write_text(new_sha256, encoding='ascii')
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

    @staticmethod
    def _generate_helper_ps1(script_dir: Path) -> None:
        """
        生成 M9A_Update_Assistant_Update_Helper.ps1

        Args:
            script_dir: 脚本输出目录（与 exe 同目录）
        """
        ps1_content = textwrap.dedent(r"""
            <#
            .SYNOPSIS
                M9A_Update_Assistant_Update_Helper
            .DESCRIPTION
                等待主进程退出 → 调用 update.ps1 替换 → 验证新版 → 提交或回滚
            #>
            param([int]$ParentPid)

            $ErrorActionPreference = "Stop"
            $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
            $stateFile = Join-Path $scriptDir "update_state.ini"
            $lockFile  = Join-Path $scriptDir "update_started.lock"
            $logFile   = Join-Path $scriptDir "update.log"
            $updatePs1 = Join-Path $scriptDir "M9A_Update_Assistant_Update.ps1"

            function Write-Log($msg) {
                $line = "$(Get-Date -Format o) $msg"
                Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
            }

            function Read-IniValue($section, $key) {
                $content = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8
                $sectionEsc = [regex]::Escape("[$section]")
                $keyEsc = [regex]::Escape($key)
                $pattern = "(?ms)^$sectionEsc.*?^$keyEsc\s*=\s*(.*?)\s*$"
                if ($content -match $pattern) { return $matches[1] }
                return ""
            }

            function Write-IniValue($section, $key, $value) {
                $content = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8
                $sectionEsc = [regex]::Escape("[$section]")
                $keyEsc = [regex]::Escape($key)
                $pattern = "(?ms)(^$sectionEsc.*?^$keyEsc\s*=\s*).*?(\s*$)"
                $newContent = $content -replace $pattern, "`${1}$value`${2}"
                $tmp = "$stateFile.tmp"
                $newContent | Set-Content -LiteralPath $tmp -Encoding UTF8
                Move-Item -LiteralPath $tmp -Destination $stateFile -Force
            }

            function Restore-Backup($reason) {
                Write-Log "rollback: $reason"
                try {
                    $target  = Read-IniValue "Files" "target"
                    $backup  = Read-IniValue "Files" "backup_file"
                    $newFile = Read-IniValue "Files" "new_file"

                    if (Test-Path -LiteralPath $target) {
                        Remove-Item -LiteralPath $target -Force
                    }
                    if (Test-Path -LiteralPath $backup) {
                        Move-Item -LiteralPath $backup -Destination $target -Force
                        Write-IniValue "State" "state" "rollback_done"
                        Write-IniValue "State" "last_error" $reason

                        $retry = [int](Read-IniValue "Retry" "retry_count")
                        $max   = [int](Read-IniValue "Retry" "max_retry")
                        $retry++
                        Write-IniValue "Retry" "retry_count" "$retry"

                        if ($retry -lt $max) {
                            Start-Process -FilePath $target -ArgumentList "--retry-update"
                        } else {
                            Write-IniValue "State" "state" "failed_disabled"
                            Start-Process -FilePath $target -ArgumentList "--update-failed"
                        }
                        exit 1
                    }
                    Write-IniValue "State" "state" "failed_disabled"
                    Write-IniValue "State" "last_error" "backup not found: $backup"
                    exit 2
                } catch {
                    Write-IniValue "State" "state" "failed_disabled"
                    Write-IniValue "State" "last_error" $_.Exception.Message
                    exit 3
                }
            }

            try {
                New-Item -LiteralPath $lockFile -ItemType File -Force | Out-Null
                Write-IniValue "State" "state" "helper_started"
                Write-Log "helper started"

                if ($ParentPid -gt 0) {
                    try { Wait-Process -Id $ParentPid -Timeout 60 }
                    catch {
                        $p = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
                        if ($p) { throw "parent still alive: $ParentPid" }
                    }
                }

                Write-IniValue "State" "state" "replacing"
                Write-Log "running update.ps1"

                $updateProc = Start-Process -FilePath "powershell.exe" -ArgumentList @(
                    "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", $updatePs1
                ) -Wait -PassThru

                if ($updateProc.ExitCode -ne 0) {
                    Restore-Backup "update.ps1 failed: $($updateProc.ExitCode)"
                }

                $target    = Read-IniValue "Files" "target"
                $newSha256 = Read-IniValue "Version" "new_sha256"
                $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
                if ($actual -ne $newSha256.ToLowerInvariant()) {
                    Restore-Backup "target hash mismatch after replace"
                }

                Write-IniValue "State" "state" "pending_new_verify"
                Write-Log "starting new exe verify"

                $newVersion = Read-IniValue "Version" "new_version"
                $verifyProc = Start-Process -FilePath $target -ArgumentList @(
                    "--self-update-verify",
                    "--expected-sha256", $newSha256,
                    "--expected-version", $newVersion
                ) -Wait -PassThru

                if ($verifyProc.ExitCode -ne 0) {
                    Restore-Backup "verify failed: $($verifyProc.ExitCode)"
                }

                Write-IniValue "State" "state" "verified"
                Write-Log "verified, starting normal app"
                Start-Process -FilePath $target
                exit 0
            } catch {
                Restore-Backup $_.Exception.Message
            }
        """).lstrip("\n")

        script_path = script_dir / "M9A_Update_Assistant_Update_Helper.ps1"
        script_path.write_text(ps1_content, encoding='utf-8')

    @staticmethod
    def _generate_update_ps1(script_dir: Path) -> None:
        """
        生成 M9A_Update_Assistant_Update.ps1

        Args:
            script_dir: 脚本输出目录（与 exe 同目录）
        """
        ps1_content = textwrap.dedent(r"""
            <#
            .SYNOPSIS
                M9A_Update_Assistant_Update
            .DESCRIPTION
                替换 app.exe 为新版本：app.exe → app.backup.exe, app.new.exe → app.exe
                不做 SHA256 校验，校验由 helper.ps1 负责
            #>

            $ErrorActionPreference = "Stop"
            $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
            $stateFile = Join-Path $scriptDir "update_state.ini"

            function Read-IniValue($section, $key) {
                $content = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8
                $sectionEsc = [regex]::Escape("[$section]")
                $keyEsc = [regex]::Escape($key)
                $pattern = "(?ms)^$sectionEsc.*?^$keyEsc\s*=\s*(.*?)\s*$"
                if ($content -match $pattern) { return $matches[1] }
                return ""
            }

            try {
                $target  = Read-IniValue "Files" "target"
                $newFile = Read-IniValue "Files" "new_file"
                $backup  = Read-IniValue "Files" "backup_file"
                $newSha256 = Read-IniValue "Version" "new_sha256"

                if (!(Test-Path -LiteralPath $newFile)) {
                    throw "new file not found: $newFile"
                }

                $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $newFile).Hash.ToLowerInvariant()
                if ($actual -ne $newSha256.ToLowerInvariant()) {
                    throw "new file SHA256 mismatch: expected $newSha256, got $actual"
                }

                if (Test-Path -LiteralPath $backup) {
                    Remove-Item -LiteralPath $backup -Force
                }
                if (Test-Path -LiteralPath $target) {
                    Move-Item -LiteralPath $target -Destination $backup -Force
                }
                Move-Item -LiteralPath $newFile -Destination $target -Force
                exit 0
            } catch {
                Write-Error $_.Exception.Message
                exit 1
            }
        """).lstrip("\n")

        script_path = script_dir / "M9A_Update_Assistant_Update.ps1"
        script_path.write_text(ps1_content, encoding='utf-8')

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
        base_dir = current_exe.parent
        new_exe = base_dir / f"{current_exe.stem}.new.exe"
        backup_exe = base_dir / f"{current_exe.stem}.backup.exe"

        shutil.copy2(tmp_path, new_exe)
        self.logger.info(f"新版本已暂存: {new_exe}")

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
        state["old_version"] = old_version
        state["new_version"] = new_version
        state["old_sha256"] = old_sha256
        state["new_sha256"] = new_sha256
        state.set("Retry", "retry_count", _get_existing_retry_count())
        state.set("Retry", "max_retry", "3")
        state.save()

        self._generate_helper_ps1(base_dir)
        self._generate_update_ps1(base_dir)
        self.logger.info(f"已生成更新脚本: {base_dir}")

        state.transition("helper_started")

        self.logger.info("启动更新进程...")
        lock_file = base_dir / "update_started.lock"
        if lock_file.exists():
            lock_file.unlink()

        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(base_dir / "M9A_Update_Assistant_Update_Helper.ps1"),
                "-ParentPid", str(os.getpid()),
            ],
            creationflags=subprocess.DETACHED_PROCESS,
        )

        deadline = time.time() + 5
        while time.time() < deadline:
            if lock_file.exists():
                return
            time.sleep(0.1)

        raise RuntimeError("启动更新脚本失败：helper.ps1 未在 5 秒内就绪")

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
                f"GitHub: {expected_sha256[:16]}\n"
                f"本地:   {actual_sha256[:16]}"
            )
            return 2

        import M9A_Update_Assistant as app_module
        if new_version and app_module.VERSION != new_version:
            logger.critical(
                f"版本号不匹配: \n"
                f"GitHub: {new_version}\n"
                f"本地:   {app_module.VERSION}")
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
