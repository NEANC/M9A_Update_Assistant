#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import shutil
import sys

from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.config_manager import ConfigManager
from modules.github_release_client import GitHubReleaseClient
from modules.download_manager import DownloadManager
from modules.logger_manager import (
    add_file_logger,
    cleanup_old_logs,
    raw_read_save_enabled,
    setup_logger,
)
from modules.m9a_updater import M9AUpdater
from modules.config_self_updater import UpdateState
from modules.self_updater import SelfUpdater
from modules.version import VERSION
from modules.zip_manager import ZipManager


def print_info():
    """打印程序的版本和版权信息。"""
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

        if self._raw_read_save_enabled():
            self.file_handler = self._add_file_logger()
        else:
            self.file_handler = None

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
        """添加文件日志记录器，委托给 logger_manager.add_file_logger"""
        return add_file_logger(self.logger, VERSION)

    def _cleanup_old_logs(self) -> None:
        """清理多余的日志文件，委托给 logger_manager.cleanup_old_logs"""
        cleanup_old_logs(self.logger, self.config.log_max_files)

    def validate_config(self) -> bool:
        """验证配置"""
        return self.config.validate()

    def _collect_outdated_folders(self, latest_version: str) -> List[str]:
        """
        对比各 M9A 文件夹本地版本与 GitHub 最新版本

        Args:
            latest_version: GitHub 最新版本号

        Returns:
            需要更新的 M9A 文件夹路径列表
        """
        latest_ver_tuple = SelfUpdater.version_to_tuple(latest_version)
        if not latest_ver_tuple:
            self.logger.error(f"GitHub 版本号解析失败: {latest_version}，将强制更新所有 M9A")
            return list(self.config.m9a_folders)

        outdated = []
        for m9a_folder in self.config.m9a_folders:
            if not os.path.exists(m9a_folder):
                self.logger.info(f"{m9a_folder} 目录不存在，将创建并部署最新版本")
                outdated.append(m9a_folder)
                continue

            local_version = M9AUpdater.get_version_from_interface(m9a_folder)
            if not local_version:
                self.logger.warning(f"{m9a_folder} 未读取到本地版本号，将强制更新到 {latest_version}")
                outdated.append(m9a_folder)
                continue

            local_ver_tuple = SelfUpdater.version_to_tuple(local_version)
            if not local_ver_tuple:
                self.logger.warning(f"{m9a_folder} 本地版本号解析失败: {local_version}，将强制更新到 {latest_version}")
                outdated.append(m9a_folder)
                continue

            if local_ver_tuple >= latest_ver_tuple:
                self.logger.info(f"{m9a_folder} 已是最新版本，跳过更新")
            else:
                self.logger.info(f"{m9a_folder} 将更新到 {latest_version}")
                outdated.append(m9a_folder)

        return outdated

    def _download_latest_release(self, release_info: Optional[Dict[str, Any]] = None,
                                  cached_cli: str = '') -> Optional[Dict[str, Any]]:
        """
        下载最新版本的 CLI 和 GUI ZIP 文件

        Args:
            release_info: 可选，已获取的 GitHub release 信息。若为 None 则内部调用 API。

        Returns:
            包含下载信息的字典
        """
        if release_info is None:
            release_info = self._github.get_latest_release_info()
        if not release_info:
            return None

        keywords = self._github.parse_release_keywords(release_info)
        cli_keyword = keywords['cli']
        gui_keywords = keywords['gui_keywords']

        cli_zip_pattern = f'M9A-win-x86_64-v*-{cli_keyword}.zip'
        gui_zip_patterns = [f'M9A-win-x86_64-v*-{keyword}.zip' for keyword in gui_keywords]

        self.config.cli_zip_pattern = cli_zip_pattern
        self.config.gui_zip_pattern = gui_zip_patterns[0] if gui_zip_patterns else 'M9A-win-x86_64-v*-Full.zip'

        tag_name = release_info.get('tag_name', 'latest')
        download_dir = Path(self.config.temp_folder) / "ZIP"
        download_dir.mkdir(parents=True, exist_ok=True)

        cli_url = self._github.find_download_url(release_info, cli_zip_pattern)

        gui_url = self._github.find_download_url(
            release_info, 'M9A-win-x86_64-v*-*.zip',
            select_smallest=True, exclude_patterns=[cli_zip_pattern],
        )
        if gui_url:
            gui_keyword = Path(gui_url).name.split('-')[-1].replace('.zip', '')
        else:
            gui_keyword = gui_keywords[0] if gui_keywords else 'Full'

        downloaded_files = []

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
            self.logger.warning(f"未找到匹配的 CLI ZIP 文件: {cli_zip_pattern}")
            return None

        downloaded_files.append(cli_path)

        need_gui_download = True
        cli_has_deps = self._zip.check_lite_zip_has_deps(cli_path)
        if cli_has_deps:
            need_gui_download = False
            self.logger.info("CLI ZIP 已包含 deps 文件夹，跳过 GUI ZIP 下载")

        if need_gui_download and gui_url:
            gui_filename = Path(gui_url).name
            gui_save_path = download_dir / gui_filename
            gui_path = self._check_or_download_zip(
                gui_url, gui_save_path, release_info, gui_filename, download_dir, tag_name,
            )
            if not gui_path:
                return None
            downloaded_files.append(gui_path)
        elif need_gui_download:
            self.logger.critical("未找到匹配的 GUI ZIP 文件，跳过 deps 提取")

        return {
            'files': downloaded_files,
            'cli_keyword': cli_keyword,
            'gui_keyword': gui_keyword,
            'version': tag_name,
            'cli_has_deps': cli_has_deps,
        }

    def _check_or_download_zip(self, url: str, save_path: Path, release_info: Dict,
                                zip_filename: str, download_dir: Path,
                                tag_name: str) -> Optional[str]:
        """
        检查缓存或下载 ZIP 文件，并进行完整性校验
        缓存匹配优先使用 ZIP 内部 interface.json 的版本号 + 文件名关键字
        """
        zip_keyword = Path(zip_filename).name.replace('.zip', '').split('-')[-1]

        for candidate in download_dir.glob('M9A-win-x86_64-v*-*.zip'):
            candidate_keyword = candidate.name.replace('.zip', '').split('-')[-1]
            if candidate_keyword != zip_keyword:
                continue
            cached_version = ZipManager.get_zip_version(str(candidate))
            if cached_version and cached_version == tag_name:
                self.logger.info(f"临时文件夹存在缓存文件 {cached_version}: {candidate}")
                if self._zip.verify_zip_integrity(str(candidate), release_info, zip_filename, self._github):
                    return str(candidate)
                self.logger.warning("缓存文件校验失败，将重新下载")
            elif cached_version:
                self.logger.debug(f"缓存 ZIP 内部版本 {cached_version} 与目标 {tag_name} 不匹配: {candidate}")

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

    def run_update(self) -> bool:
        """
        执行完整的更新流程
        """
        self.logger.info("正在从 GitHub 获取最新版本信息...")
        release_info = self._github.get_latest_release_info()
        if not release_info:
            self.logger.critical("无法获取 GitHub release 信息，更新终止")
            return False

        latest_version = release_info.get('tag_name', '')
        if not latest_version:
            self.logger.critical("GitHub release 信息中未找到版本号，更新终止")
            return False

        outdated_folders = self._collect_outdated_folders(latest_version)
        if not outdated_folders:
            self.logger.info("所有 M9A 已是最新版本，无需更新")
            self._cleanup_old_logs()
            return True

        self.logger.info(f"共有 {len(outdated_folders)} 个 M9A 需要更新")

        keywords = self._github.parse_release_keywords(release_info)
        cli_keyword = keywords['cli']
        self.config.cli_zip_pattern = f'M9A-win-x86_64-v*-{cli_keyword}.zip'

        cli_zip = self._updater.find_lite_zip(
            self.config.cli_zip_pattern, self.config.temp_folder, self._github, latest_version,
        )
        if cli_zip:
            self.logger.info(f"本地已缓存 CLI ZIP: {cli_zip}")

        gui_zip = None
        version = ''
        cli_has_deps = None

        download_result = self._download_latest_release(release_info, cached_cli=cli_zip)
        if download_result:
            downloaded_files = download_result['files']
            cli_keyword = download_result['cli_keyword']
            gui_keyword = download_result['gui_keyword']
            version = download_result.get('version', '')
            cli_has_deps = download_result.get('cli_has_deps')

            for file_path in downloaded_files:
                if cli_keyword in file_path:
                    cli_zip = file_path
                elif gui_keyword in file_path:
                    gui_zip = file_path
        elif cli_zip:
            self.logger.warning("从 GitHub 下载失败，使用本地缓存文件")
            cli_has_deps = self._zip.check_lite_zip_has_deps(cli_zip)
        else:
            info = self._updater.find_lite_zip(
                self.config.cli_zip_pattern, self.config.temp_folder, self._github, latest_version,
            )
            if info:
                if not self._zip.verify_zip_integrity(info, release_info, Path(info).name, self._github):
                    self.logger.critical("本地缓存文件校验失败，更新终止")
                    return False
                cli_zip = info
                cli_has_deps = self._zip.check_lite_zip_has_deps(cli_zip)
            else:
                self.logger.critical("未找到 CLI ZIP 文件，更新终止")
                return False

        self.logger.info(f"使用 CLI ZIP 文件: {cli_zip}")

        need_extract_deps = True
        if cli_has_deps is None:
            cli_has_deps = self._zip.check_lite_zip_has_deps(cli_zip)
        if cli_has_deps:
            need_extract_deps = False
            self.logger.info("CLI ZIP 已包含 deps 文件夹，跳过 deps 提取")
        elif not gui_zip:
            need_extract_deps = False
            self.logger.info("未找到 GUI ZIP 文件，跳过 deps 提取")

        all_success = True
        for index, m9a_folder in enumerate(outdated_folders, 1):
            self.logger.info(f"开始更新第 {index}/{len(outdated_folders)} 个 M9A: {m9a_folder}")

            folder_existed = os.path.exists(m9a_folder)

            if folder_existed:
                config_backup_successful = self._updater.backup_config(m9a_folder, version)
                if not config_backup_successful:
                    self.logger.warning("config 文件夹不存在或备份失败，将跳过备份和回写步骤")
            else:
                config_backup_successful = False

            if not self._updater.clean_m9a_folder(m9a_folder):
                self.logger.critical(f"清理 M9A 文件夹失败: {m9a_folder}")
                all_success = False
                continue

            if not self._zip.extract_zip_with_progress(cli_zip, m9a_folder, self._download):
                self.logger.critical(f"解压 CLI ZIP 失败: {m9a_folder}")
                all_success = False
                continue

            if config_backup_successful:
                if not self._updater.restore_config(m9a_folder, version):
                    self.logger.critical(f"回写 config 失败: {m9a_folder}")
                    all_success = False
                    continue

            if need_extract_deps:
                if not self._zip.extract_deps_from_full_zip(
                    gui_zip, m9a_folder,
                    self.config.gui_zip_pattern,
                    self.config.temp_folder,
                    self.config.m9a_folders,
                    self._download,
                    self._github,
                ):
                    self.logger.critical(f"提取 deps 文件夹失败: {m9a_folder}")
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
        )


def _resolve_temp_folder_from_config() -> str:
    """从 config.ini 读取并解析临时文件夹路径"""
    import configparser

    exe_dir = str(Path(sys.argv[0]).resolve().parent)
    config = configparser.ConfigParser()
    if Path("config.ini").exists():
        config.read("config.ini", encoding='utf-8')
        temp_folder_config = config.get('Paths', 'temp_folder', fallback='').strip()
    else:
        temp_folder_config = ''

    if not temp_folder_config:
        system_temp = os.environ.get('TEMP', '')
        if system_temp:
            return os.path.join(system_temp, 'M9A-Update-Assistant')
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        if local_app_data:
            return os.path.join(local_app_data, 'Temp', 'M9A-Update-Assistant')
        return os.path.join(exe_dir, 'Temp')

    if temp_folder_config == 'Temp':
        return os.path.join(exe_dir, 'Temp')

    return temp_folder_config


def _clean_self_update_cache(logger: logging.Logger) -> None:
    """清理自更新下载缓存目录"""
    temp_folder = _resolve_temp_folder_from_config()
    SelfUpdater.clean_self_update_cache(temp_folder, logger)


def _cleanup_update_residue(logger: logging.Logger) -> None:
    """清理上次成功更新后的残留文件"""
    state = UpdateState.load()
    if not state:
        return

    current_state = state.get("State", "state", fallback="")

    if current_state == "verified":
        logger.info("清理上次更新残留文件...")
        target_path = Path(state["target"])
        script_dir = target_path.parent

        cleanup_files = [
            Path(state["backup_file"]),
            script_dir / f"{target_path.stem}.old.exe",
            script_dir / "M9A_Update_Assistant_Update_Helper.ps1",
            script_dir / "M9A_Update_Assistant_Update.ps1",
            script_dir / "update_started.lock",
            script_dir / "update.log",
        ]
        for f in cleanup_files:
            try:
                if f.exists():
                    f.unlink()
                    logger.debug(f"已删除残留文件: {f}")
            except OSError:
                pass

        # ── 清理自更新缓存 ──
        if '--not-delete' not in sys.argv:
            _clean_self_update_cache(logger)

        state.delete()
        logger.info("残留文件清理完成")
    elif current_state in ("helper_started", "replacing", "pending_new_verify", "rollback"):
        logger.warning("检测到上次更新未完成，尝试恢复...")
        backup_file = Path(state["backup_file"])
        target = Path(state["target"])
        if backup_file.exists() and not target.exists():
            shutil.move(str(backup_file), str(target))
            logger.info("已从备份恢复")
        state.delete()

    elif current_state == "rollback_done":
        logger.info("检测到上次更新回滚完成，清理状态文件")
        state.delete()

    elif current_state == "failed_disabled":
        failed_ver = state["new_version"]
        logger.warning(f"自更新已禁用：版本 {failed_ver} 多次验证失败")
        logger.warning(f"将跳过版本 {failed_ver} 的自动更新，等待远端发布新版本")


def main():
    """主函数"""

    # ── 新版验证模式 ──
    if '--self-update-verify' in sys.argv:
        exit_code = SelfUpdater.self_update_verify()
        sys.exit(exit_code)

    # ── 重试更新模式 ──
    if '--retry-update' in sys.argv:
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.info("正在重试自更新...")
        assistant = M9AUpdateAssistant()
        need_exit = assistant.check_self_update()
        if need_exit:
            sys.exit(0)
        logger.error("重试更新失败，无法获取新版本")
        return

    # ── 更新失败模式 ──
    if '--update-failed' in sys.argv:
        print_info()
        logger = logging.getLogger("M9AUpdateAssistant")
        state = UpdateState.load()
        if state:
            failed_ver = state["new_version"]
            logger.critical(f"自更新失败：版本 {failed_ver} 多次验证不通过")
            print(f"\n软件自动更新失败，版本 {failed_ver} 已被标记为不可用。")
            print(f"您可以向开发人员提交 {script_dir / 'update.log'} 反馈此问题。")
            print(f"已回退到 {VERSION} 版本，后续将跳过 {failed_ver} 版本的自动更新。")
        else:
            logger.critical("自更新失败，但无法读取状态信息")
        print(f"\n按任意键退出...")
        input()
        return

    # ── 正常启动：清理上次更新残留 ──
    _cleanup_update_residue(logging.getLogger("M9AUpdateAssistant"))

    # ── 仅检查自身更新模式 ──
    if any(flag in sys.argv for flag in ('-U', '--update', '--Update')):
        print_info()
        force = any(f in sys.argv for f in ('-f', '--update-force', '--Update-force'))
        assistant = M9AUpdateAssistant()
        if assistant.check_self_update(force=force):
            if force:
                assistant.logger.info("强制执行 Build 版本更新")
            assistant.logger.info("已将新版本下载到临时文件夹，即将退出以完成更新...")
            sys.exit(0)
        else:
            print(f"\n按任意键退出...")
            input()
            return

    try:
        print_info()
        assistant = M9AUpdateAssistant()

        if '--not-delete' in sys.argv:
            assistant.keep_temp = True

        if not assistant.validate_config():
            assistant.logger.critical("错误的配置，请修改配置文件后重新运行。")
            sys.exit(1)

        success = assistant.run_update()

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
    main()
