#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import json
import logging
import os
import re
import shutil
import sys

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from modules.progress_bar import tqdm, BAR_FORMAT, format_ok, format_error


VersionKey = Tuple[int, int, int, int, int]
ArchiveVersion = Tuple[VersionKey, str]


def _normalize_version_name(version_str: str) -> str:
    """规范化版本号字符串，用于目录名精确匹配"""
    version = version_str.strip()
    if not version:
        return ''
    if version[0].lower() == 'v':
        return 'v' + version[1:].lower()
    return 'v' + version.lower()


def _parse_version_to_tuple(version_str: str) -> VersionKey:
    """将版本号字符串解析为可比较的排序键，稳定版高于 rc/beta/alpha"""
    match = re.match(
        r'^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$',
        version_str.strip(),
        re.IGNORECASE,
    )
    if not match:
        return ()

    major, minor, patch, prerelease = match.groups()
    core = (int(major), int(minor or 0), int(patch or 0))
    if not prerelease:
        return core + (3, 0)

    pre = prerelease.lower()
    if pre.startswith('alpha'):
        weight = 0
    elif pre.startswith('beta'):
        weight = 1
    elif pre.startswith('rc'):
        weight = 2
    else:
        weight = -1

    number_match = re.search(r'(\d+)', pre)
    number = int(number_match.group(1)) if number_match else 0
    return core + (weight, number)


def _collect_archive_versions(archive_dir: Path, backup_name: str) -> List[ArchiveVersion]:
    """扫描存档目录，收集所有含 {backup_name}/config 的版本排序键和目录名（降序排列）"""
    versions = []
    if not archive_dir.exists():
        return versions
    for entry in archive_dir.iterdir():
        if not entry.is_dir():
            continue
        version_dir = entry.name
        if not (entry / backup_name / "config").exists():
            continue
        ver_tuple = _parse_version_to_tuple(version_dir)
        if not ver_tuple:
            continue
        versions.append((ver_tuple, version_dir))
    versions.sort(key=lambda x: (x[0], _normalize_version_name(x[1])), reverse=True)
    return versions


def find_best_config_version(
    archive_dir: Path,
    backup_name: str,
    current_version: str,
    target_version: str,
    logger: logging.Logger,
) -> str:
    """
    从存档目录中查找最适合的配置版本

    查找策略：
    1. 扫描存档目录，收集所有包含 {backup_name}/config 的版本目录
    2. 版本号降序排列
    3. 从 target_version 开始向下查找：
       找到 → 返回该版本号
       找不到 → 在列表中找下一个更低的版本
       都没有 → 返回 current_version（回退）

    Args:
        archive_dir: 存档根目录
        backup_name: 备份名称（如 Z-M9A）
        current_version: 当前版本号（作为最终回退值）
        target_version: 目标版本号（降级目标）
        logger: 日志记录器

    Returns:
        最适合的版本号字符串
    """
    target_tuple = _parse_version_to_tuple(target_version)
    if not target_tuple:
        logger.warning(f"目标版本号解析失败: {target_version}，回退到当前版本: {current_version}")
        return current_version

    archive_versions = _collect_archive_versions(archive_dir, backup_name)
    if not archive_versions:
        logger.debug(f"存档目录无可用版本，回退到当前版本: {current_version}")
        return current_version

    normalized_target = _normalize_version_name(target_version)
    for _, ver_dir in archive_versions:
        if _normalize_version_name(ver_dir) == normalized_target:
            logger.info(f"降级配置查找: 精确命中目标版本 {ver_dir}")
            return ver_dir

    for ver_tuple, ver_dir in archive_versions:
        if ver_tuple < target_tuple:
            logger.info(f"降级配置查找: 目标版本 {target_version} 无备份，使用更早版本 {ver_dir}")
            return ver_dir

    logger.info(f"降级配置查找: 所有存档版本均高于目标 {target_version}，回退到当前版本 {current_version}")
    return current_version


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
        program_root = Path(sys.argv[0]).resolve().parent
        return str(program_root / archive_folder_path)

    @staticmethod
    def _walk_dir_size(path: Path) -> int:
        """递归计算目录总大小（字节）"""
        total = 0
        try:
            for root, _, filenames in os.walk(path):
                for fn in filenames:
                    total += (Path(root) / fn).stat().st_size
        except OSError:
            pass
        return total

    @staticmethod
    def _copy_tree_with_progress(src: Path, dst: Path, desc: str,
                                  logger: logging.Logger) -> bool:
        """
        带 tqdm 进度条的目录拷贝

        Args:
            src: 源路径
            dst: 目标路径
            desc: tqdm 描述
            logger: 日志记录器

        Returns:
            是否成功
        """
        files_to_copy = []
        total_size = 0
        for root, _, filenames in os.walk(src):
            for fn in filenames:
                fp = Path(root) / fn
                rel = fp.relative_to(src)
                files_to_copy.append((fp, dst / rel))
                total_size += fp.stat().st_size

        if not files_to_copy:
            logger.debug(f"空目录，跳过拷贝: {src}")
            return True

        pbar = None
        try:
            dst.mkdir(parents=True, exist_ok=True)
            pbar = tqdm(total=total_size, unit='B', unit_scale=True, unit_divisor=1024,
                       desc=desc, bar_format=BAR_FORMAT, leave=False)
            for file_src, file_dst in files_to_copy:
                file_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_src, file_dst)
                pbar.update(file_src.stat().st_size)
            pbar.close()
            return True
        except (IOError, OSError, shutil.Error) as e:
            if pbar:
                pbar.leave = True
                pbar.close()
            print(format_error(desc, str(e)))
            logger.error(f"拷贝失败: {e}")
            return False

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

    def backup_config(self, m9a_folder: str, version: str = '') -> Optional[str]:
        """
        备份 config 文件夹到程序根目录

        Args:
            m9a_folder: M9A 文件夹路径
            version: 版本号回退值（如 v3.19.0），若 interface.json 不可读则使用此值

        Returns:
            实际使用的旧版本号，若备份失败则返回 None
        """
        version = self.get_version_from_interface(m9a_folder, version)
        if not version:
            self.logger.warning("未找到版本号，跳过备份")
            return None

        m9a_config_path = Path(m9a_folder) / "config"
        if not m9a_config_path.exists():
            self.logger.warning(f"M9A 文件夹中的 config 文件夹不存在: {m9a_config_path}")
            return None

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
            success = self._copy_tree_with_progress(
                m9a_config_path, archive_path,
                f"备份 {backup_name}/config", self.logger,
            )
            if not success:
                return None

            print(format_ok("备份", backup_name + "/config", str(archive_path), self._walk_dir_size(m9a_config_path)))
            return version
        except (IOError, OSError, shutil.Error) as e:
            self.logger.error(f"备份 config 文件夹失败: {e}")
            return None

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
            version: 旧版本号（必须传入，不能从 interface.json 重新读取）

        Returns:
            bool: 操作是否成功
        """
        # 不能从 interface.json 重新读取版本号——
        # restore 时 M9A 文件夹已被新 ZIP 覆盖，interface.json 是新版本号。
        # 必须使用调用方传入的旧版本号，它对应备份时使用的路径。
        backup_name = self.get_backup_name(m9a_folder)
        archive_config_path = self.archive_dir / version / backup_name / "config"
        m9a_config_path = Path(m9a_folder) / "config"

        if not archive_config_path.exists():
            self.logger.warning(f"未找到备份的 config 文件夹: {archive_config_path}")
            return False

        try:
            success = self._copy_tree_with_progress(
                archive_config_path, m9a_config_path,
                f"回写 {backup_name}/config", self.logger,
            )
            if not success:
                return False

            print(format_ok("回写", backup_name + "/config", str(m9a_config_path), self._walk_dir_size(archive_config_path)))
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
