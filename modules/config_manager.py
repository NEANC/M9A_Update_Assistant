#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import configparser

from pathlib import Path
from typing import List


class ConfigManager:
    """配置管理器，负责配置文件读取、校验、默认配置生成"""

    def __init__(self, config_file: str, logger: logging.Logger):
        """
        初始化配置管理器

        Args:
            config_file: 配置文件路径
            logger: 日志记录器
        """
        self.config_file = config_file
        self.logger = logger
        self.config = configparser.ConfigParser()

        self.m9a_folders: List[str] = []
        self.temp_folder = ''
        self.archive_folder_name = '更新前存档'
        self.cli_zip_pattern = 'M9A-win-x86_64-v*-Lite.zip'
        self.gui_zip_pattern = 'M9A-win-x86_64-v*-Full.zip'
        self.log_max_files = 15
        self.log_save_enabled = True
        self.github_repo = 'MAA1999/M9A'
        self.github_release_version = 'release'
        self.github_proxy = ''
        self.self_update_enabled = True

    def _generate_default_config(self) -> None:
        """生成默认配置文件"""
        default_config = r"""[Paths]
# M9A 文件夹路径（多个路径用逗号分隔）
m9a_folders = Z:\M9A

# 临时文件夹路径
temp_folder = Z:\Temp\M9A-Update-Assistant

# 配置存档文件夹名（用于保存更新前的配置）
archive_folder_name = 存档文件夹

[Logs]
# 是否保存日志文件，如遇 BUG 时请打开此选项，以获取更多调试信息
save_enabled = False

# 最大日志文件数量（超过此数量的旧日志将被删除）
max_files = 15

[GitHub]
# GitHub 仓库地址（格式：用户名/仓库名）
repo = MAA1999/M9A

# 代理服务器地址（例如：http://127.0.0.1:7890 或 socks5://127.0.0.1:1080），留空表示不使用代理
proxy =

# 版本选择
# release: 使用最新的发布版本，包括 Alpha、Beta 等预发布版本（https://github.com/MAA1999/M9A/releases）
# latest: 使用带有 latest 标签的正式版本（https://github.com/MAA1999/M9A/releases/latest）
release_version = release

[SelfUpdate]
# 是否启用程序自我更新
enabled = true
"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                f.write(default_config)
            print(f"已生成默认配置文件: {self.config_file}")
            print("请修改配置文件后重新运行程序。")
            sys.exit(0)
        except IOError as e:
            print(f"生成配置文件失败: {e}")
            sys.exit(1)

    def _resolve_temp_folder(self, temp_folder_config: str) -> str:
        """
        根据配置确定临时文件夹路径

        Args:
            temp_folder_config: 配置中的临时文件夹路径

        Returns:
            解析后的临时文件夹路径
        """
        if not temp_folder_config:
            system_temp = os.environ.get('TEMP', '')
            if system_temp:
                temp_folder = os.path.join(system_temp, 'M9A-Update-Assistant')
            else:
                local_app_data = os.environ.get('LOCALAPPDATA', '')
                if local_app_data:
                    temp_folder = os.path.join(local_app_data, 'Temp', 'M9A-Update-Assistant')
                else:
                    temp_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Temp')
            self.logger.info(f"配置为空，使用系统临时文件夹: {temp_folder}")
            return temp_folder
        if temp_folder_config == 'Temp':
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Temp')
        return temp_folder_config

    def _ensure_temp_folder_exists(self) -> None:
        """确保临时文件夹存在，若创建失败则回退到系统临时文件夹"""
        if os.path.exists(self.temp_folder):
            return
        try:
            os.makedirs(self.temp_folder, exist_ok=True)
            self.logger.info(f"已创建临时文件夹: {self.temp_folder}")
        except (OSError, PermissionError) as e:
            self.logger.warning(f"无法创建临时文件夹 {self.temp_folder}: {e}")
            system_temp = os.environ.get('TEMP', '')
            if system_temp:
                self.temp_folder = os.path.join(system_temp, 'M9A-Update-Assistant')
            else:
                local_app_data = os.environ.get('LOCALAPPDATA', '')
                if local_app_data:
                    self.temp_folder = os.path.join(local_app_data, 'Temp', 'M9A-Update-Assistant')
                else:
                    self.temp_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Temp')
            self.logger.info(f"使用系统临时文件夹: {self.temp_folder}")
            os.makedirs(self.temp_folder, exist_ok=True)

    def load(self) -> None:
        """加载配置文件"""
        if not os.path.exists(self.config_file):
            print(f"配置文件 {self.config_file} 不存在，将生成默认配置文件")
            self._generate_default_config()

        self.config.read(self.config_file, encoding='utf-8')

        m9a_folders_str = self.config.get('Paths', 'm9a_folders')
        if m9a_folders_str:
            self.m9a_folders = [folder.strip() for folder in m9a_folders_str.split(',') if folder.strip()]
        else:
            self.m9a_folders = []

        temp_folder_config = self.config.get('Paths', 'temp_folder', fallback='Temp').strip()
        self.temp_folder = self._resolve_temp_folder(temp_folder_config)
        self._ensure_temp_folder_exists()

        self.archive_folder_name = self.config.get('Paths', 'archive_folder_name', fallback='更新前存档').strip()
        if not self.archive_folder_name:
            self.archive_folder_name = '更新前存档'

        self.log_max_files = self.config.getint('Logs', 'max_files', fallback=15)
        self.log_save_enabled = self.config.getboolean('Logs', 'save_enabled', fallback=True)

        self.github_repo = self.config.get('GitHub', 'repo', fallback='MAA1999/M9A')
        self.github_release_version = self.config.get('GitHub', 'release_version', fallback='release')
        self.github_proxy = self.config.get('GitHub', 'proxy', fallback='').strip()
        self.self_update_enabled = self.config.getboolean('SelfUpdate', 'enabled', fallback=True)

        if self.github_proxy:
            self.logger.info(f"已配置代理: {self.github_proxy}")
        else:
            self.logger.info("未配置代理，若遇到网络问题请配置代理")

        self.logger.info(f"Release 版本: {self.github_release_version}")

    def validate(self) -> bool:
        """
        验证配置文件是否合法

        Returns:
            bool: 配置是否合法
        """
        if not self.m9a_folders:
            self.logger.error("配置错误: M9A 文件夹路径未配置")
            self.logger.error("请在配置文件中设置 m9a_folders 字段")
            return False

        for folder in self.m9a_folders:
            if not os.path.exists(folder):
                self.logger.warning(f"M9A 文件夹路径不存在: {folder}")
                self.logger.warning("程序将尝试创建该文件夹")

        try:
            temp_path = Path(self.temp_folder)
            temp_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"临时文件夹路径: {self.temp_folder}")
        except Exception as e:
            self.logger.error(f"临时文件夹路径错误: {e}")
            return False

        if not self.github_repo:
            self.logger.error("配置错误: GitHub 仓库地址未配置")
            return False

        if self.github_release_version not in ['release', 'latest']:
            self.logger.error(f"配置错误: 未知的 Release 版本类型: {self.github_release_version}")
            return False

        self.logger.info("配置验证通过")
        return True
