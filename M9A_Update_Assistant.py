#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import sys

import colorama

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.config_manager import ConfigManager
from modules.github_release_client import GitHubReleaseClient
from modules.download_manager import DownloadManager
from modules.zip_manager import ZipManager
from modules.m9a_updater import M9AUpdater
from modules.self_updater import SelfUpdater


VERSION = "v1.10.0"


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


class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器，仅作用于控制台输出"""

    LEVEL_COLORS = {
        'DEBUG': colorama.Fore.CYAN,
        'INFO': colorama.Fore.WHITE,
        'WARNING': colorama.Fore.YELLOW,
        'ERROR': colorama.Fore.RED,
        'CRITICAL': colorama.Back.RED + colorama.Fore.BLACK + colorama.Style.BRIGHT,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        colorama.init(autoreset=True)

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, colorama.Fore.WHITE)
        result = super().format(record)
        return f"{color}{result}{colorama.Style.RESET_ALL}"


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
        self._updater = M9AUpdater(self.config.archive_folder_name, self.logger)
        self._self_update = SelfUpdater(
            self.config.github_proxy,
            self.config.temp_folder,
            self.logger,
        )

    def _setup_logger(self) -> logging.Logger:
        """
        设置日志记录器

        Returns:
            配置好的日志记录器
        """
        logger = logging.getLogger("M9AUpdateAssistant")
        logger.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = ColoredFormatter(
            '%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S',
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        return logger

    def _raw_read_save_enabled(self) -> bool:
        """在加载完整配置前，粗读配置文件判断是否启用日志保存"""
        if not Path(self.config_file).exists():
            return True
        try:
            import configparser
            raw = configparser.ConfigParser()
            raw.read(self.config_file, encoding='utf-8')
            return raw.getboolean('Logs', 'save_enabled', fallback=True)
        except Exception:
            return True

    def _add_file_logger(self) -> logging.FileHandler:
        """添加文件日志记录器，始终挂载"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        log_file = log_dir / f"M9A_Update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S',
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        self.logger.debug(f"当前程序版本: {VERSION}")
        self.logger.info(f"日志文件已创建: {log_file}")
        return file_handler

    def _cleanup_old_logs(self) -> None:
        """清理多余的日志文件"""
        log_dir = Path("logs")
        if not log_dir.exists():
            return

        log_files = list(log_dir.glob("M9A_Update_*.log"))
        if len(log_files) <= self.config.log_max_files:
            return

        log_files.sort(key=lambda x: x.stat().st_mtime)
        files_to_delete = log_files[:-self.config.log_max_files]
        for log_file in files_to_delete:
            try:
                log_file.unlink()
                self.logger.info(f"已删除多余的日志文件: {log_file}")
            except Exception as e:
                self.logger.warning(f"删除日志文件 {log_file} 失败: {e}")

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
            self.logger.warning(f"GitHub 版本号解析失败: {latest_version}，将更新所有 M9A")
            return list(self.config.m9a_folders)

        outdated = []
        for m9a_folder in self.config.m9a_folders:
            local_version = M9AUpdater.get_version_from_interface(m9a_folder)
            if not local_version:
                self.logger.info(f"未读取到本地版本号，将更新: {m9a_folder}")
                outdated.append(m9a_folder)
                continue

            local_ver_tuple = SelfUpdater.version_to_tuple(local_version)
            if not local_ver_tuple:
                self.logger.info(f"本地版本号解析失败: {local_version}，将更新: {m9a_folder}")
                outdated.append(m9a_folder)
                continue

            if local_ver_tuple >= latest_ver_tuple:
                self.logger.info(f"已是最新版本 (本地={local_version}, GitHub={latest_version})，跳过: {m9a_folder}")
            else:
                self.logger.info(f"发现新版本 (本地={local_version}, GitHub={latest_version})，需要更新: {m9a_folder}")
                outdated.append(m9a_folder)

        return outdated

    def _download_latest_release(self, release_info: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
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
        version_pattern = tag_name.replace('v', '')

        if cli_url:
            cli_filename = Path(cli_url).name
            cli_save_path = download_dir / cli_filename
            cli_match = cli_zip_pattern.replace('*', version_pattern)
            cli_path = self._check_or_download_zip(
                cli_url, cli_save_path, release_info, cli_filename, download_dir, cli_match,
            )
            if not cli_path:
                return None
            downloaded_files.append(cli_path)
        else:
            self.logger.error(f"未找到匹配的 CLI ZIP 文件: {cli_zip_pattern}")
            return None

        need_gui_download = True
        cli_zip_path = downloaded_files[0]
        cli_has_deps = self._zip.check_lite_zip_has_deps(cli_zip_path)
        if cli_has_deps:
            need_gui_download = False
            self.logger.info("CLI ZIP 已包含 deps 文件夹，跳过 GUI ZIP 下载")
        elif not self.config.github_full_download_enabled:
            need_gui_download = False
            self.logger.info("配置中禁用了 GUI 版本下载，跳过 GUI ZIP 下载")

        if need_gui_download and gui_url:
            gui_filename = Path(gui_url).name
            gui_save_path = download_dir / gui_filename
            gui_match = f"M9A-win-x86_64-v{version_pattern}-{gui_keyword}.zip"
            gui_path = self._check_or_download_zip(
                gui_url, gui_save_path, release_info, gui_filename, download_dir, gui_match,
            )
            if not gui_path:
                return None
            downloaded_files.append(gui_path)
        elif not need_gui_download:
            self.logger.info("跳过 GUI ZIP 下载")

        return {
            'files': downloaded_files,
            'cli_keyword': cli_keyword,
            'gui_keyword': gui_keyword,
            'version': tag_name,
            'cli_has_deps': cli_has_deps,
        }

    def _check_or_download_zip(self, url: str, save_path: Path, release_info: Dict,
                                zip_filename: str, download_dir: Path,
                                match_pattern: str) -> Optional[str]:
        """
        检查缓存或下载 ZIP 文件，并进行完整性校验
        """
        cached_files = list(download_dir.glob(match_pattern))
        if cached_files:
            cached_path = str(cached_files[0])
            self.logger.info(f"临时文件夹中已存在最新版本: {cached_path}")
            if self._zip.verify_zip_integrity(cached_path, release_info, zip_filename, self._github):
                return cached_path
            self.logger.error("缓存文件校验失败，将重新下载")

        if not self._download.download_file_with_progress(url, str(save_path)):
            return None

        if not self._zip.verify_zip_integrity(str(save_path), release_info, zip_filename, self._github):
            self.logger.error("下载文件校验失败")
            try:
                save_path.unlink()
            except Exception:
                pass
            return None

        return str(save_path)

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

        cli_zip = None
        gui_zip = None
        version = ''
        cli_has_deps = None

        download_result = self._download_latest_release(release_info)
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
        else:
            self.logger.warning("从 GitHub 下载失败，尝试使用本地文件")

        if not cli_zip:
            cli_has_deps = None
            cli_zip = self._updater.find_lite_zip(
                self.config.cli_zip_pattern, self.config.temp_folder, self._github,
            )
            if not cli_zip:
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
            print(f"\n")
            self.logger.info(f"开始更新第 {index}/{len(outdated_folders)} 个 M9A: {m9a_folder}")

            config_backup_successful = self._updater.backup_config(m9a_folder, version)
            if not config_backup_successful:
                self.logger.info("config 文件夹不存在或备份失败，将跳过备份和回写步骤")

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

        if not self._updater.clean_temp_folder(self.config.temp_folder):
            self.logger.warning("无法清理临时文件夹")

        self._cleanup_old_logs()

        if all_success:
            self.logger.info("所有需要更新的 M9A 已完成更新")
        else:
            self.logger.warning("部分 M9A 更新失败")

        return all_success

    def check_self_update(self) -> bool:
        """检查自身更新"""
        return self._self_update.check_self_update(
            VERSION, self._github, self._download, self._zip,
        )

    def self_update_perform(self) -> None:
        """执行自身更新替换"""
        self._self_update.perform(self._zip)


def main():
    """主函数"""
    if '--self-update-complete' in sys.argv:
        assistant = M9AUpdateAssistant()
        print_info()
        assistant.logger.info("自更新完成，正在验证...")

        # 轻量 health-check：验证配置和关键模块可用
        if not assistant.validate_config():
            assistant.logger.critical("配置验证失败!")
            SelfUpdater.rollback()
            sys.exit(1)
        assistant.logger.info("新版本验证通过")

        bak_path = Path(sys.executable).with_suffix('.exe.bak')
        if bak_path.exists():
            bak_path.unlink()
            assistant.logger.info(f"已删除备份文件: {bak_path}")

        assistant.logger.info("自更新完成，程序已就绪")
        print(f"\n")
        return

    if '--self-update' in sys.argv:
        assistant = M9AUpdateAssistant()
        assistant.self_update_perform()
        return

    try:
        print_info()
        assistant = M9AUpdateAssistant()

        if not assistant.validate_config():
            assistant.logger.critical("错误的配置，请修改配置文件后重新运行。")
            sys.exit(1)

        success = assistant.run_update()

        need_exit = assistant.check_self_update()
        if need_exit:
            print(f"\n程序将自动退出以完成自更新，稍后自动重启...\n")
            sys.exit(0)

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
