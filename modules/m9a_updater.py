#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import json
import logging
import os
import shutil

from datetime import datetime
from pathlib import Path
from typing import Optional


class M9AUpdater:
    """M9A 更新器，负责备份、清理、部署、回写 config"""

    def __init__(self, archive_folder_path: str, logger: logging.Logger):
        """
        初始化 M9A 更新器

        Args:
            archive_folder_path: 配置中的存档文件夹名或绝对路径
            logger: 日志记录器
        """
        self.archive_dir = Path(self._resolve_archive_dir(archive_folder_path))
        self.logger = logger

    @staticmethod
    def _resolve_archive_dir(archive_folder_path: str) -> str:
        """解析存档文件夹绝对路径：绝对路径直接返回，相对名拼到程序根目录"""
        if os.path.isabs(archive_folder_path):
            return archive_folder_path
        program_root = Path(__file__).parent.parent
        return str(program_root / archive_folder_path)

    @staticmethod
    def get_version_from_interface(m9a_folder: str, fallback_version: str = '') -> str:
        """
        从 M9A 文件夹的 interface.json 中读取版本号

        Args:
            m9a_folder: M9A 文件夹路径
            fallback_version: 读取失败时的回退版本号

        Returns:
            读取到的版本号，若失败则返回 fallback_version
        """
        interface_json_path = Path(m9a_folder) / "interface.json"
        if not interface_json_path.exists():
            return fallback_version
        try:
            with open(interface_json_path, 'r', encoding='utf-8') as f:
                interface_data = json.load(f)
            version = interface_data.get('version', fallback_version)
            if version and version != fallback_version:
                logging.getLogger("M9AUpdateAssistant").info(f"从 {m9a_folder} 的 interface.json 获取到版本号: {version}")
            return version
        except (json.JSONDecodeError, IOError, OSError) as e:
            logging.getLogger("M9AUpdateAssistant").warning(f"读取 interface.json 失败: {e}")
            return fallback_version

    @staticmethod
    def get_backup_name(m9a_folder: str) -> str:
        """
        从 M9A 文件夹路径中提取备份名称

        Args:
            m9a_folder: M9A 文件夹路径

        Returns:
            备份名称（如 Z-M9A）
        """
        m9a_path_obj = Path(m9a_folder)
        drive_letter = m9a_path_obj.drive.replace(':', '')
        folder_name = m9a_path_obj.name
        return f"{drive_letter}-{folder_name}"

    def backup_config(self, m9a_folder: str, version: str = '') -> bool:
        """
        备份 config 文件夹到程序根目录

        Args:
            m9a_folder: M9A 文件夹路径
            version: 版本号（如 v3.19.0）

        Returns:
            bool: 操作是否成功
        """
        version = self.get_version_from_interface(m9a_folder, version)
        if not version:
            self.logger.warning("未找到版本号，跳过备份")
            return False

        m9a_config_path = Path(m9a_folder) / "config"
        if not m9a_config_path.exists():
            self.logger.warning(f"M9A 文件夹中的 config 文件夹不存在: {m9a_config_path}")
            return False

        try:
            backup_name = self.get_backup_name(m9a_folder)
            archive_path = self.archive_dir / version / backup_name / "config"

            if archive_path.exists():
                old_backup_dir = self.archive_dir / version / "old"
                old_backup_dir.mkdir(parents=True, exist_ok=True)

                timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
                old_backup_name = f"{backup_name}_{timestamp}"
                old_backup_zip = old_backup_dir / f"{old_backup_name}.zip"

                self.logger.info(f"已存在备份，将其压缩到: {old_backup_zip}")
                shutil.make_archive(str(old_backup_dir / old_backup_name), 'zip', str(archive_path.parent))

                shutil.rmtree(archive_path.parent)
                self.logger.info(f"删除旧备份目录: {archive_path.parent}")

            archive_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(m9a_config_path, archive_path, dirs_exist_ok=True)
            self.logger.info(f"config 文件夹已备份到: {archive_path}")

            return True
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"备份 config 文件夹失败: {e}")
            return False

    def clean_m9a_folder(self, m9a_folder: str) -> bool:
        """
        清理 M9A 文件夹中的所有文件

        Args:
            m9a_folder: M9A 文件夹路径

        Returns:
            bool: 操作是否成功
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

    def restore_config(self, m9a_folder: str, version: str = '') -> bool:
        """
        将 config 回写到 M9A 文件夹

        Args:
            m9a_folder: M9A 文件夹路径
            version: 版本号（如 v3.19.0）

        Returns:
            bool: 操作是否成功
        """
        version = self.get_version_from_interface(m9a_folder, version)
        if not version:
            self.logger.warning("版本号为空，跳过回写")
            return False

        backup_name = self.get_backup_name(m9a_folder)
        archive_config_path = self.archive_dir / version / backup_name / "config"
        m9a_config_path = Path(m9a_folder) / "config"

        if not archive_config_path.exists():
            self.logger.warning(f"未找到备份的 config 文件夹: {archive_config_path}")
            return False

        try:
            self.logger.info(f"config 文件夹正在回写：{archive_config_path} -> {m9a_config_path}")
            shutil.copytree(archive_config_path, m9a_config_path, dirs_exist_ok=True)
            self.logger.info(f"config 文件夹已回写到: {m9a_config_path}")
            return True
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"回写 config 文件夹失败: {e}")
            return False

    def clean_temp_folder(self, temp_folder: str) -> bool:
        """
        清理临时文件夹

        Args:
            temp_folder: 临时文件夹路径

        Returns:
            bool: 操作是否成功
        """
        temp_path = Path(temp_folder)

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

    def find_lite_zip(self, cli_zip_pattern: str, temp_folder: str,
                       gh_client, target_version: str = '') -> Optional[str]:
        """
        查找匹配的 CLI ZIP 文件，优先通过内部 interface.json 版本号匹配

        Args:
            cli_zip_pattern: CLI ZIP 匹配模式（备选，版本匹配失败时回退）
            temp_folder: 临时文件夹
            gh_client: GitHubReleaseClient 实例（用于 compile_pattern）
            target_version: 目标版本号（如 v3.28.3），为空时不进行版本匹配

        Returns:
            找到的 ZIP 文件路径，如果未找到则返回 None
        """
        from modules.zip_manager import ZipManager

        cli_zip_regex = gh_client.compile_pattern(cli_zip_pattern)
        cli_keyword = cli_zip_pattern.replace('.zip', '').split('-')[-1].lstrip('*')
        search_dirs = [Path(temp_folder) / "ZIP", Path(temp_folder), Path.cwd()]
        all_zips = []

        for search_dir in search_dirs:
            if search_dir.exists():
                all_zips.extend(list(search_dir.glob('M9A-win-x86_64-v*-*.zip')))

        if target_version:
            for candidate in all_zips:
                keywords = candidate.name.replace('.zip', '').split('-')[-1]
                if keywords != cli_keyword:
                    continue
                version = ZipManager.get_zip_version(str(candidate))
                if version and version == target_version:
                    self.logger.info(f"缓存 ZIP 版本 {version} 匹配: {candidate}")
                    return str(candidate)
            self.logger.warning(f"未找到版本 {target_version} 的缓存 ZIP，将重新下载")
            return None

        for candidate in all_zips:
            if cli_zip_regex.match(candidate.name):
                self.logger.info(f"使用文件名匹配缓存 ZIP: {candidate}")
                return str(candidate)

        if not all_zips:
            self.logger.debug(f"未找到任何 M9A ZIP 文件")
        else:
            self.logger.warning(f"未找到匹配的 CLI ZIP 文件: {cli_zip_pattern}")
        return None
