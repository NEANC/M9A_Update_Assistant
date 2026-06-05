#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import configparser

from pathlib import Path
from typing import List

from modules.config_migration import apply_migrations


def _get_program_dir() -> str:
    """获取程序真实所在目录（兼容所有打包方式及源码运行）"""
    return str(Path(sys.argv[0]).resolve().parent)


class ConfigManager:
    """配置管理器，负责配置初始化、加载、验证"""

    DEFAULT_SECTIONS = {
        'Paths': {
            'm9a_folders': r'Z:\M9A',
            'temp_folder': r'Z:\Temp\M9A-Update-Assistant',
            'archive_folder_path': '存档文件夹',
        },
        'Logs': {
            'save_enabled': 'false',
            'max_files': '5',
        },
        'GitHub': {
            'repo': 'MAA1999/M9A',
            'proxy': '',
            'm9a_update_channel': 'preview',
        },
        'SelfUpdate': {
            'enabled': 'true',
            'self_update_channel': 'stable',
        },
    }

    _COMMENTS = {
        'Paths.m9a_folders': 'M9A 文件夹路径（多个路径用逗号分隔）',
        'Paths.temp_folder': '临时文件夹路径',
        'Paths.archive_folder_path': '配置存档文件夹路径（保存更新前的配置，若不是完整路径，将自动使用当前工作目录）',
        'Logs.save_enabled': '是否保存日志文件，如遇 BUG 时请打开此选项，以获取更多调试信息',
        'Logs.max_files': '最大日志文件数量（超过此数量的旧日志将被删除）',
        'GitHub.repo': 'GitHub 仓库地址（格式：用户名/仓库名）',
        'GitHub.proxy': '代理服务器地址（例如：http://127.0.0.1:7890 或 socks5://127.0.0.1:1080），留空表示不使用代理',
        'GitHub.m9a_update_channel': 'M9A 更新通道\npreview: 包括预发布版本 (Alpha/Beta/RC)\nstable: 仅正式发布版本',
        'SelfUpdate.enabled': '是否启用软件自我更新',
        'SelfUpdate.self_update_channel': '自我更新版本通道\npreview: 包括预发布版本 (Alpha/Beta/RC)\nstable: 仅正式发布版本',
    }

    @classmethod
    def _build_default_config(cls) -> str:
        """从 DEFAULT_SECTIONS + _COMMENTS 生成默认配置文件内容"""
        lines = []
        for section, keys in cls.DEFAULT_SECTIONS.items():
            lines.append(f'[{section}]')
            for key, val in keys.items():
                comment = cls._COMMENTS.get(f'{section}.{key}', '')
                if comment:
                    for cl in comment.split('\n'):
                        lines.append(f'# {cl}')
                lines.append(f'{key} = {val}')
            lines.append('')
        return '\n'.join(lines)

    def __init__(self, config_file: str, logger: logging.Logger):
        """
        初始化配置管理器

        Args:
            config_file: 配置文件路径
            logger: 日志记录器
        """
        self.config_file = config_file
        self.logger = logger
        self.config = configparser.ConfigParser(strict=False)

        self.m9a_folders: List[str] = []
        self.temp_folder = ''
        self.archive_folder_path = '存档文件夹'
        self.cli_zip_pattern = 'M9A-win-x86_64-v*-Lite.zip'
        self.gui_zip_pattern = 'M9A-win-x86_64-v*-Full.zip'
        self.log_max_files = 5
        self.log_save_enabled = False
        self.github_repo = 'MAA1999/M9A'
        self.github_release_version = 'preview'
        self.github_proxy = ''
        self.self_update_enabled = True
        self.self_update_channel = 'stable'

    def _generate_default_config(self) -> None:
        """生成默认配置文件"""
        default_config = self._build_default_config()
        tmp_path = self.config_file + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(default_config)
            os.replace(tmp_path, self.config_file)
            self.logger.info(f"已生成默认配置文件: {self.config_file}")
            self.logger.info("请修改配置文件后重新运行软件。")
            self.logger.info("按任意键退出...")
            input()
            sys.exit(0)
        except OSError as e:
            self.logger.error(f"生成配置文件失败: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            sys.exit(1)

    def _regenerate_config_file(self) -> None:
        """
        重建配置文件，保留所有已有值，仅补充缺失的模板键。
        遍历 self.config 中所有节和键，跳过 DEFAULT（无节头孤儿键）。
        """
        lines = []
        for section in self.config.sections():
            if section.upper() == 'DEFAULT' or section == '__migrations__':
                continue
            lines.append(f'[{section}]')
            template = self.DEFAULT_SECTIONS.get(section, {})
            written_keys = set()

            for key, default_val in template.items():
                written_keys.add(key)
                comment = self._COMMENTS.get(f'{section}.{key}', '')
                if comment:
                    for cl in comment.split('\n'):
                        lines.append(f'# {cl}')
                current = self.config.get(section, key, fallback=default_val)
                lines.append(f'{key} = {current}')

            for key, val in self.config.items(section):
                if key not in written_keys and key not in (self.config.defaults() or {}):
                    if not key.strip():
                        continue
                    lines.append(f'{key} = {val}')

            lines.append('')

        for section, keys in self.DEFAULT_SECTIONS.items():
            if not self.config.has_section(section):
                lines.append(f'[{section}]')
                for key, val in keys.items():
                    comment = self._COMMENTS.get(f'{section}.{key}', '')
                    if comment:
                        for cl in comment.split('\n'):
                            lines.append(f'# {cl}')
                    lines.append(f'{key} = {val}')
                lines.append('')

        tmp_path = self.config_file + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            os.replace(tmp_path, self.config_file)
        except OSError as e:
            self.logger.error(f"写入配置文件失败: {e}")
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _sanitize_config_file(self) -> None:
        """逐行清理损坏行：空键值行删除，无 = 行注释掉"""
        import re
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError:
            return

        fixed = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith(';'):
                new_lines.append(line)
                continue
            if re.match(r'^\[.+\]$', stripped):
                new_lines.append(line)
                continue
            if '=' not in stripped:
                new_lines.append(f'# [已修复] {line}')
                fixed = True
                continue
            key, sep, val = stripped.partition('=')
            if not key.strip():
                fixed = True
                continue
            new_lines.append(line)

        if fixed:
            tmp_path = self.config_file + '.tmp'
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                os.replace(tmp_path, self.config_file)
            except OSError as e:
                self.logger.error(f"修复配置文件失败: {e}")
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _recover_orphan_keys(self) -> bool:
        """将误归属的模板键还原到正确的节，返回是否做了修改"""
        changed = False
        defaults = self.config.defaults()
        if defaults:
            for key, val in list(defaults.items()):
                for section, keys in self.DEFAULT_SECTIONS.items():
                    if (key in keys and self.config.has_section(section)
                            and not self.config.has_option(section, key)):
                        self.config.set(section, key, val)
                        self.config.remove_option('DEFAULT', key)
                        self.logger.warning(f"键 {key} 已还原到 [{section}]")
                        changed = True
                        break

        for source_section in list(self.config.sections()):
            if source_section.upper() == 'DEFAULT' or source_section == '__migrations__':
                continue
            template = self.DEFAULT_SECTIONS.get(source_section, {})
            for key, val in list(self.config.items(source_section)):
                if not key.strip():
                    continue
                if key in (self.config.defaults() or {}):
                    continue
                if key in template:
                    continue
                for tgt_section, tgt_keys in self.DEFAULT_SECTIONS.items():
                    if (key in tgt_keys and tgt_section != source_section
                            and self.config.has_section(tgt_section)
                            and not self.config.has_option(tgt_section, key)):
                        self.config.set(tgt_section, key, val)
                        self.config.remove_option(source_section, key)
                        self.logger.warning(
                            f"键 {key}={val} 从 [{source_section}] 还原到 [{tgt_section}]"
                        )
                        changed = True
                        break
        return changed

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
                    temp_folder = os.path.join(_get_program_dir(), 'Temp')
            self.logger.info(f"配置为空，使用系统临时文件夹: {temp_folder}")
            return temp_folder
        if temp_folder_config == 'Temp':
            return os.path.join(_get_program_dir(), 'Temp')
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
                    self.temp_folder = os.path.join(_get_program_dir(), 'Temp')
            self.logger.info(f"使用系统临时文件夹: {self.temp_folder}")
            os.makedirs(self.temp_folder, exist_ok=True)

    def load(self) -> None:
        """加载配置文件"""
        if not os.path.exists(self.config_file):
            self.logger.info("配置文件不存在，将生成默认配置文件")
            self._generate_default_config()

        for pass_num in range(3):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config.read_file(f)
                break
            except configparser.Error as e:
                if pass_num == 0:
                    self.logger.warning(f"配置文件解析错误，正在尝试修复: {e}")
                    self._sanitize_config_file()
                elif pass_num == 1:
                    self.logger.critical("修复失败，将重新生成配置文件")
                    self._generate_default_config()
                else:
                    self.logger.critical(f"配置文件无法修复: {e}")
                    self.logger.critical(f"配置文件 {self.config_file} 已损坏且无法自动修复。")
                    self.logger.critical("请检查文件内容或删除后重新运行软件以生成默认配置。")
                    self.logger.critical("按任意键退出...")
                    input()
                    raise SystemExit(1)

        migrated = apply_migrations(self.config, self.logger)

        dirty = migrated


        for section in self.DEFAULT_SECTIONS:
            if not self.config.has_section(section):
                self.config.add_section(section)
                dirty = True

        orphaned = self._recover_orphan_keys()

        for section, keys in self.DEFAULT_SECTIONS.items():
            for key, val in keys.items():
                if not self.config.has_option(section, key):
                    self.config.set(section, key, val)
                    dirty = True
                    self.logger.warning(f"配置节: [{section}] 缺少键: {key}，已自动补充默认值")

        if dirty or orphaned:
            self._regenerate_config_file()

        m9a_folders_str = self.config.get('Paths', 'm9a_folders')
        if m9a_folders_str:
            self.m9a_folders = [folder.strip() for folder in m9a_folders_str.split(',') if folder.strip()]
        else:
            self.m9a_folders = []

        temp_folder_config = self.config.get('Paths', 'temp_folder', fallback='Temp').strip()
        self.temp_folder = self._resolve_temp_folder(temp_folder_config)
        self._ensure_temp_folder_exists()

        self.archive_folder_path = self.config.get('Paths', 'archive_folder_path', fallback='存档文件夹').strip()
        if not self.archive_folder_path:
            self.archive_folder_path = '存档文件夹'

        self.log_max_files = self.config.getint('Logs', 'max_files', fallback=5)
        self.log_save_enabled = self.config.getboolean('Logs', 'save_enabled', fallback=True)

        self.github_repo = self.config.get('GitHub', 'repo', fallback='MAA1999/M9A')
        self.github_release_version = self.config.get('GitHub', 'm9a_update_channel', fallback='preview')
        self.github_proxy = self.config.get('GitHub', 'proxy', fallback='').strip()
        self.self_update_enabled = self.config.getboolean('SelfUpdate', 'enabled', fallback=True)
        self.self_update_channel = self.config.get('SelfUpdate', 'self_update_channel', fallback='preview').strip()

        if self.github_proxy:
            self.logger.info(f"已配置代理: {self.github_proxy}")
        else:
            self.logger.info("未配置代理，若遇到网络问题请配置代理")

        self.logger.info(f"M9A 更新通道: {self.github_release_version}")

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
                self.logger.warning("软件将尝试创建该文件夹")

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

        if self.github_release_version not in ['preview', 'stable']:
            self.logger.error(f"配置错误: 未知的 M9A 更新通道: {self.github_release_version}")
            self.logger.error("可用选项: preview (含预发布), stable (仅正式版)")
            return False

        self.logger.info("配置验证通过")
        return True
