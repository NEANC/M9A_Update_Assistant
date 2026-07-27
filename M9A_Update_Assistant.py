#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import argparse
import logging
import os
import shutil
import sys
import configparser
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.config_manager import ConfigManager, parse_download_threads
from modules.github_release_client import GitHubReleaseClient
from modules.download_manager import DownloadManager
from modules.logger_manager import (
    add_file_logger,
    cleanup_old_logs,
    raw_read_save_enabled,
    setup_logger,
)
from modules.m9a_updater import (
    M9AUpdater,
    _normalize_version_name,
    _parse_version_to_tuple,
    find_best_config_version,
)
from modules.config_self_updater import UpdateState
from modules.self_updater import SelfUpdater
from modules.version import VERSION, print_info
from modules.zip_manager import ZipManager


def setup_utf8_console() -> None:
    """强制 stdout/stderr 使用 UTF-8 编码"""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if sys.stdin and hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class M9AUpdateAssistant:
    """M9A 更新类，用于处理 M9A 的更新操作（编排器）"""

    def __init__(self, config_file: str = "config.ini"):
        """
        初始化 M9A 更新助手

        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.logger = self._setup_logger()
        self.config = ConfigManager(config_file, self.logger)

        self._is_bundled = True
        self._package_type = "Nuitka"

        if self._raw_read_save_enabled():
            self.file_handler = self._add_file_logger()
        else:
            self.file_handler = None
            # 无文件日志时仍需检测运行环境
            self._is_bundled, self._package_type = SelfUpdater.detect_package_type()

        self.config.load()

        self._github = GitHubReleaseClient(
            self.config.github_repo,
            self.config.github_release_version,
            self.config.github_proxy,
            self.logger,
        )
        self._download = DownloadManager(
            self.config.github_proxy,
            self.config.temp_folder,
            self.logger,
            self.config.download_threads,
        )
        self._zip = ZipManager(self.logger)
        self._updater = M9AUpdater(self.config.archive_folder_path, self.logger)
        self._self_update = SelfUpdater(
            self.config.github_proxy,
            self.config.temp_folder,
            self.logger,
            self.config.self_update_channel,
        )

        self.keep_temp = False

    def _setup_logger(self) -> logging.Logger:
        """设置日志记录器，委托给 logger_manager.setup_logger"""
        return setup_logger()

    def _raw_read_save_enabled(self) -> bool:
        """在加载完整配置前，粗读配置文件判断是否启用日志保存"""
        return raw_read_save_enabled(self.config_file)

    def _add_file_logger(self) -> logging.FileHandler:
        """添加文件日志记录器：版本号 → 运行环境 → 日志文件已创建"""
        file_handler = add_file_logger(self.logger, VERSION)
        self._is_bundled, self._package_type = SelfUpdater.detect_package_type()
        self.logger.info(f"日志文件已创建: {file_handler.baseFilename}")
        return file_handler

    def _cleanup_old_logs(self) -> None:
        """清理多余的日志文件，委托给 logger_manager.cleanup_old_logs"""
        cleanup_old_logs(self.logger, self.config.log_max_files)

    def validate_config(self) -> bool:
        """验证配置"""
        return self.config.validate()

    def _collect_outdated_folders(self, target_version: str, force: bool = False) -> List[str]:
        """
        对比各 M9A 文件夹本地版本与目标版本

        Args:
            target_version: 目标版本号
            force: 是否强制模式（指定版本时启用，支持降级）

        Returns:
            需要更新的 M9A 文件夹路径列表
        """
        target_ver_tuple = SelfUpdater.version_to_tuple(target_version)
        if not target_ver_tuple:
            self.logger.error(f"版本号解析失败: {target_version}，将强制更新所有 M9A")
            return list(self.config.m9a_folders)

        outdated = []
        for m9a_folder in self.config.m9a_folders:
            if not os.path.exists(m9a_folder):
                self.logger.info(f"{m9a_folder} 目录不存在，将创建并部署 {target_version}")
                outdated.append(m9a_folder)
                continue

            local_version = M9AUpdater.get_version_from_interface(m9a_folder)
            if not local_version:
                self.logger.warning(f"{m9a_folder} 未读取到本地版本号，将强制更新到 {target_version}")
                outdated.append(m9a_folder)
                continue

            local_ver_tuple = SelfUpdater.version_to_tuple(local_version)
            if not local_ver_tuple:
                self.logger.warning(f"{m9a_folder} 本地版本号解析失败: {local_version}，将强制更新到 {target_version}")
                outdated.append(m9a_folder)
                continue

            if force:
                # 指定版本模式：只要本地不等于目标就更新（支持降级）
                if local_ver_tuple == target_ver_tuple:
                    self.logger.info(f"{m9a_folder} 已与指定版本 {target_version} 一致，跳过更新")
                else:
                    direction = "降级" if local_ver_tuple > target_ver_tuple else "升级"
                    self.logger.info(f"{m9a_folder} 将从 {local_version} {direction}到 {target_version}")
                    outdated.append(m9a_folder)
            else:
                # 常规模式：仅升级
                if local_ver_tuple >= target_ver_tuple:
                    self.logger.info(f"{m9a_folder} 已是最新版本，跳过更新")
                else:
                    self.logger.info(f"{m9a_folder} 将更新到 {target_version}")
                    outdated.append(m9a_folder)

        return outdated

    def _download_latest_release(self, release_info: Optional[Dict[str, Any]] = None,
                                  cached_cli: str = '') -> Optional[Dict[str, Any]]:
        """
        下载最新版本的 Windows x86_64 CLI ZIP 文件

        Args:
            release_info: 可选，已获取的 GitHub release 信息。若为 None 则内部调用 API。
            cached_cli: 可选，本地缓存的 CLI ZIP 文件路径。

        Returns:
            包含下载信息的字典，或错误信息
        """
        if release_info is None:
            release_info = self._github.get_latest_release_info()
        if not release_info:
            return None

        tag_name = release_info.get('tag_name', 'latest')
        download_dir = Path(self.config.temp_folder) / "ZIP"
        download_dir.mkdir(parents=True, exist_ok=True)

        cli_url = self._github.find_download_url(release_info, self.config.cli_zip_pattern)
        if not cached_cli and not cli_url:
            self.logger.critical(f"未找到版本 {tag_name} 的 CLI ZIP 文件，匹配规则: {self.config.cli_zip_pattern}，更新终止")
            return {'error': 'missing_cli_zip'}

        if cached_cli:
            self.logger.info(f"使用本地缓存 CLI ZIP: {cached_cli}")
            if not self._zip.verify_zip_integrity(cached_cli, release_info, Path(cached_cli).name, self._github):
                self.logger.warning("缓存 CLI ZIP 校验失败，将重新下载")
                cached_cli = ''

        if cached_cli:
            cli_path = cached_cli
        elif cli_url:
            cli_filename = Path(cli_url).name
            cli_save_path = download_dir / cli_filename
            cli_path = self._check_or_download_zip(
                cli_url, cli_save_path, release_info, cli_filename, download_dir, tag_name,
            )
            if not cli_path:
                return None
        else:
            self.logger.warning(f"未找到匹配的 CLI ZIP 文件: {self.config.cli_zip_pattern}")
            return None

        return {'files': [cli_path], 'version': tag_name}

    def _check_or_download_zip(self, url: str, save_path: Path, release_info: Dict,
                                zip_filename: str, download_dir: Path,
                                tag_name: str) -> Optional[str]:
        """
        检查缓存或下载 ZIP 文件，并进行完整性校验

        缓存匹配使用 ZIP 内部 interface.json 的版本号。
        """
        for candidate in download_dir.glob(self.config.cli_zip_pattern):
            cached_version = ZipManager.get_zip_version(str(candidate))
            if cached_version and _normalize_version_name(cached_version) == _normalize_version_name(tag_name):
                self.logger.info(f"临时文件夹存在缓存文件 {cached_version}: {candidate}")
                if self._zip.verify_zip_integrity(str(candidate), release_info, candidate.name, self._github):
                    return str(candidate)
                self.logger.warning("缓存文件校验失败，将重新下载")

        max_attempts = 3
        for attempt in range(max_attempts):
            if attempt > 0:
                self.logger.info(f"重新下载（第 {attempt + 1}/{max_attempts} 次）...")

            if not self._download.download_file_with_progress(url, str(save_path)):
                self.logger.error("下载失败")
                continue

            if self._zip.verify_zip_integrity(str(save_path), release_info, zip_filename, self._github):
                return str(save_path)

            self.logger.warning("下载文件校验失败，准备重试")
            try:
                save_path.unlink()
            except Exception:
                pass

        self.logger.critical("下载文件校验失败，已达到最大重试次数")
        return None

    def run_update(self, target_version: str = '') -> bool:
        """
        执行完整的更新流程

        Args:
            target_version: 指定目标版本号（为空则获取最新版本，支持不带 'v' 前缀）
        """
        if target_version:
            v_tag = target_version if target_version.startswith('v') else f"v{target_version}"
            self.logger.info(f"指定目标版本: {v_tag}，正在从 GitHub 获取对应 release...")
            release_info = self._github.get_release_by_tag(target_version)
        else:
            self.logger.info("正在从 GitHub 获取最新版本信息...")
            release_info = self._github.get_latest_release_info()

        if not release_info:
            self.logger.critical("无法获取 GitHub release 信息，更新终止")
            return False

        latest_version = release_info.get('tag_name', '')
        if not latest_version:
            self.logger.critical("GitHub release 信息中未找到版本号，更新终止")
            return False

        outdated_folders = self._collect_outdated_folders(latest_version, force=bool(target_version))
        if not outdated_folders:
            self.logger.info("所有 M9A 已是最新版本，无需更新")
            if self.keep_temp:
                self.logger.info("检查到 --not-delete 参数，保留临时文件夹")
            elif not self._updater.clean_temp_folder(self.config.temp_folder):
                self.logger.warning("无法清理临时文件夹")
            self._cleanup_old_logs()
            return True

        self.logger.info(f"共有 {len(outdated_folders)} 个 M9A 需要更新")

        cli_zip = self._updater.find_cli_zip(
            self.config.cli_zip_pattern, self.config.temp_folder, self._github, latest_version,
        )
        if cli_zip:
            self.logger.info(f"本地已缓存 CLI ZIP: {cli_zip}")

        version = ''

        download_result = self._download_latest_release(release_info, cached_cli=cli_zip)
        if download_result and download_result.get('error'):
            return False
        if download_result:
            downloaded_files = download_result['files']
            version = download_result.get('version', '')
            cli_zip = downloaded_files[0]
        elif cli_zip:
            if not self._zip.verify_zip_integrity(cli_zip, release_info, Path(cli_zip).name, self._github):
                self.logger.critical("本地缓存文件校验失败，且无法从 GitHub 下载，更新终止")
                return False
            self.logger.warning("从 GitHub 下载失败，使用本地缓存文件")
        else:
            info = self._updater.find_cli_zip(
                self.config.cli_zip_pattern, self.config.temp_folder, self._github, latest_version,
            )
            if info:
                if not self._zip.verify_zip_integrity(info, release_info, Path(info).name, self._github):
                    self.logger.critical("本地缓存文件校验失败，更新终止")
                    return False
                cli_zip = info
            else:
                self.logger.critical(f"未找到版本 {latest_version} 的 CLI ZIP 文件，匹配规则: {self.config.cli_zip_pattern}，更新终止")
                return False

        self.logger.info(f"使用 CLI ZIP 文件: {cli_zip}")

        all_success = True
        for index, m9a_folder in enumerate(outdated_folders, 1):
            self.logger.info(f"开始更新第 {index}/{len(outdated_folders)} 个 M9A: {m9a_folder}")

            folder_existed = os.path.exists(m9a_folder)

            if folder_existed:
                old_version = self._updater.backup_config(m9a_folder, version)
                if not old_version:
                    self.logger.warning("config 文件夹不存在或备份失败，将跳过备份和回写步骤")
            else:
                old_version = None

            if not self._updater.clean_m9a_folder(m9a_folder):
                self.logger.critical(f"清理 M9A 文件夹失败: {m9a_folder}")
                all_success = False
                continue

            if not self._zip.extract_zip_with_progress(cli_zip, m9a_folder):
                self.logger.critical(f"解压 CLI ZIP 失败: {m9a_folder}")
                all_success = False
                continue

            if old_version:
                # 降级时查找更匹配的历史版本配置
                old_version_tuple = _parse_version_to_tuple(old_version)
                target_version_tuple = _parse_version_to_tuple(version)
                is_downgrade = (
                    bool(target_version)
                    and old_version_tuple
                    and target_version_tuple
                    and old_version_tuple > target_version_tuple
                )
                if is_downgrade:
                    best_version = find_best_config_version(
                        self._updater.archive_dir,
                        M9AUpdater.get_backup_name(m9a_folder),
                        old_version,
                        version,
                        self.logger,
                    )
                else:
                    best_version = old_version
                if not self._updater.restore_config(m9a_folder, best_version):
                    self.logger.critical(f"回写 config 失败: {m9a_folder}")
                    all_success = False
                    continue

            self.logger.info(f"M9A 更新完成: {m9a_folder}")

        if self.keep_temp:
            self.logger.info("检查到 --not-delete 参数，保留临时文件夹")
        elif not self._updater.clean_temp_folder(self.config.temp_folder):
            self.logger.warning("无法清理临时文件夹")

        self._cleanup_old_logs()

        if all_success:
            self.logger.info("所有需要更新的 M9A 已完成更新")
        else:
            self.logger.warning("部分 M9A 更新失败")

        return all_success

    def check_self_update(self, force: bool = False) -> bool:
        """检查自身更新"""
        if not self.config.self_update_enabled:
            self.logger.info("已禁用软件更新")
            return False
        return self._self_update.check_self_update(
            VERSION, self._github, self._download, self._zip, force=force,
            is_bundled=self._is_bundled, package_type=self._package_type,
        )


def _resolve_temp_folder_from_config() -> str:
    """从 config.ini 读取并解析临时文件夹路径"""
    from modules.config_manager import resolve_temp_folder

    config = configparser.ConfigParser()
    if Path("config.ini").exists():
        config.read("config.ini", encoding='utf-8')
        temp_folder_config = config.get('Paths', 'temp_folder', fallback='').strip()
    else:
        temp_folder_config = ''

    return resolve_temp_folder(temp_folder_config)


def _clean_self_update_cache(logger: logging.Logger) -> None:
    """清理自更新下载缓存目录"""
    temp_folder = _resolve_temp_folder_from_config()
    SelfUpdater.clean_self_update_cache(temp_folder, logger)


def _is_safe_recovery_runtime_dir(runtime_dir: Path, backup_file: Path) -> bool:
    """判断恢复入口中的 runtime_dir 是否可安全整目录删除。"""
    try:
        runtime_path = runtime_dir.resolve()
        backup_path = backup_file.resolve()
    except (OSError, RuntimeError):
        return False
    if not runtime_path.anchor:
        return False
    if runtime_path == Path(runtime_path.anchor):
        return False
    return backup_path.is_relative_to(runtime_path)


def _cleanup_update_residue(logger: logging.Logger, not_delete: bool = False) -> None:
    """清理上次成功更新后的残留文件

    Args:
        logger: 日志记录器
        not_delete: 是否跳过缓存清理（对应 --not-delete 参数）
    """
    state = UpdateState.load()
    if not state:
        return

    current_state = state.get("State", "state", fallback="")

    if current_state == "verified":
        SelfUpdater._cleanup_update_residue(logger, not_delete=not_delete)
        if not not_delete:
            _clean_self_update_cache(logger)
    elif current_state in ("helper_started", "replacing", "pending_new_verify", "rollback"):
        logger.warning("检测到上次更新未完成，尝试恢复...")
        backup_file = Path(state["backup_file"])
        target = Path(state["target"])
        runtime_dir = Path(state["runtime_dir"]) if state["runtime_dir"] else None
        restored = False
        if backup_file.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup_file), str(target))
            restored = True
            logger.info("已从备份恢复")

        if restored:
            if runtime_dir and runtime_dir.exists():
                if _is_safe_recovery_runtime_dir(runtime_dir, backup_file):
                    shutil.rmtree(runtime_dir, ignore_errors=True)
                else:
                    logger.warning(f"跳过越界运行时目录清理: {runtime_dir}")
            state.transition("rollback_done")
        else:
            state["last_error"] = "启动时检测到未完成更新，未满足安全恢复条件"
            state.transition("failed_disabled")

    elif current_state == "rollback_done":
        logger.info("检测到上次更新回滚完成，清理状态文件")
        state.delete()

    elif current_state == "failed_disabled":
        failed_ver = state["new_version"]
        logger.warning(f"自更新已禁用：版本 {failed_ver} 多次验证失败")
        logger.warning(f"将跳过版本 {failed_ver} 的自动更新，等待远端发布新版本")


def parse_command_line_args() -> argparse.Namespace:
    """解析命令行参数

    Returns:
        argparse.Namespace: 解析后的命令行参数
    """
    parser = argparse.ArgumentParser(description="M9A Update Assistant")
    # 内部运行模式（不在帮助中显示）
    parser.add_argument("--self-update-verify", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--expected-sha256", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--expected-version", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--retry-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-failed", action="store_true", help=argparse.SUPPRESS)
    # 自更新检查模式
    parser.add_argument("--update", "--Update", action="store_true",
                        dest="update", default=False,
                        help="仅检查自身更新")
    parser.add_argument("--update-force", "--Update-force", "--Update-Force",
                        action="store_true",
                        dest="update_force", default=False,
                        help="强制更新自身到最新版本")
    # M9A 版本控制
    parser.add_argument("--m9a-version", type=str, default="",
                        help="指定 M9A 目标版本")
    # 其他
    parser.add_argument("--not-delete", action="store_true",
                        help="不删除临时文件")
    parser.add_argument('-t', '--threads', type=str, default='',
                        help='下载线程数；只接受纯数字，0 或 1 表示单线程，默认 4，最大 32')
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_command_line_args()

    # ── 新版验证模式 ──
    if args.self_update_verify:
        exit_code = SelfUpdater.self_update_verify()
        sys.exit(exit_code)

    # ── 重试更新模式 ──
    if args.retry_update:
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.info("正在重试自更新...")
        assistant = M9AUpdateAssistant()
        if args.threads:
            assistant._download.download_threads, _ = parse_download_threads(
                args.threads,
                assistant.logger,
                'CLI',
            )
        need_exit = assistant.check_self_update()
        if need_exit:
            sys.exit(0)
        logger.error("重试更新失败，无法获取新版本")
        return

    # ── 更新失败模式 ──
    if args.update_failed:
        print_info()
        logger = logging.getLogger("M9AUpdateAssistant")
        state = UpdateState.load()
        if state:
            failed_ver = state["new_version"]
            logger.critical(f"自更新失败：版本 {failed_ver} 多次验证不通过")
            print(f"\n软件自动更新失败，版本 {failed_ver} 已被标记为不可用。")
            print(f"您可以向开发人员提交 {Path(sys.argv[0]).resolve().parent / 'update.log'} 反馈此问题。")
            print(f"已回退到 {VERSION} 版本，后续将跳过 {failed_ver} 版本的自动更新。")
        else:
            logger.critical("自更新失败，但无法读取状态信息")
        print(f"\n按任意键退出...")
        input()
        return

    # ── 正常启动：清理上次更新残留 ──
    _cleanup_update_residue(logging.getLogger("M9AUpdateAssistant"), not_delete=args.not_delete)

    # ── 仅检查自身更新 / 强制更新自身模式 ──
    if args.update or args.update_force:
        print_info()
        assistant = M9AUpdateAssistant()
        if args.threads:
            assistant._download.download_threads, _ = parse_download_threads(
                args.threads,
                assistant.logger,
                'CLI',
            )
        if assistant.check_self_update(force=args.update_force):
            assistant.logger.info("已将新版本下载到临时文件夹，即将退出以完成更新...")
            sys.exit(0)
        else:
            print(f"\n按任意键退出...")
            input()
            return

    try:
        print_info()
        assistant = M9AUpdateAssistant()

        if args.threads:
            assistant._download.download_threads, _ = parse_download_threads(
                args.threads,
                assistant.logger,
                'CLI',
            )

        if args.not_delete:
            assistant.keep_temp = True

        if not assistant.validate_config():
            assistant.logger.critical("错误的配置，请修改配置文件后重新运行。")
            sys.exit(1)

        # 去除可能的前导 'v'，run_update 内部会自行标准化
        m9a_version = args.m9a_version.lstrip("v")
        success = assistant.run_update(target_version=m9a_version)

        need_exit = assistant.check_self_update()
        if need_exit:
            print(f"\n软件将自动退出以完成更新，稍后自动重启...\n")
            sys.exit(0)

        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.critical("捕获到Ctrl+C，终止运行")
        sys.exit(0)
    except Exception as e:
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.critical(f"软件执行出错: {e}")
        raise


if __name__ == "__main__":
    setup_utf8_console()
    main()
