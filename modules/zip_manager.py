#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import hashlib
import json
import logging
import os
import zipfile

from pathlib import Path
from typing import Dict, Optional

from modules.download_manager import DownloadManager
from modules.github_release_client import GitHubReleaseClient


class ZipManager:
    """ZIP 管理器，负责 ZIP 校验、解压、deps 提取、SHA256 计算与校验"""

    def __init__(self, logger: logging.Logger):
        """
        初始化 ZIP 管理器

        Args:
            logger: 日志记录器
        """
        self.logger = logger

    @staticmethod
    def calculate_sha256(file_path: str) -> str:
        """计算文件的 SHA256 哈希值"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def verify_zip_integrity(self, zip_path: str, release_info: Dict,
                              zip_filename: str, gh_client: GitHubReleaseClient) -> bool:
        """
        验证 ZIP 文件的完整性

        Args:
            zip_path: ZIP 文件路径
            release_info: GitHub release 信息
            zip_filename: ZIP 文件名
            gh_client: GitHubReleaseClient 实例

        Returns:
            文件是否完整
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                bad_file = zip_ref.testzip()
                if bad_file is not None:
                    self.logger.error(f"ZIP CRC 校验失败，损坏的文件: {bad_file}")
                    return False

            expected_sha256 = gh_client.get_asset_sha256(release_info, zip_filename)
            if expected_sha256:
                actual_sha256 = self.calculate_sha256(zip_path)
                if actual_sha256 != expected_sha256:
                    self.logger.error("SHA256 校验失败:")
                    self.logger.warning(f"GitHub: {expected_sha256}")
                    self.logger.warning(f"本地:   {actual_sha256}")
                    return False
                self.logger.info("SHA256 校验成功")
                self.logger.info(f"GitHub: {expected_sha256}")
                self.logger.info(f"本地:   {actual_sha256}")
            else:
                self.logger.info("未找到 SHA256 校验值，仅验证文件格式")

            return True
        except zipfile.BadZipFile:
            self.logger.error(f"无效的 ZIP 文件: {zip_path}")
            return False
        except (IOError, OSError) as e:
            self.logger.error(f"验证 ZIP 文件失败: {e}")
            return False

    def verify_exe_sha256(self, file_path: str, release_info: Dict,
                           exe_name: str, gh_client: GitHubReleaseClient,
                           allow_fallback: bool = True) -> bool:
        """
        校验 EXE 文件的 SHA256 哈希值

        Args:
            file_path: 文件路径
            release_info: release 信息
            exe_name: EXE 文件名
            gh_client: GitHubReleaseClient 实例
            allow_fallback: 是否允许在未找到 SHA256 时跳过校验

        Returns:
            校验是否通过
        """
        expected = gh_client.get_exe_sha256_from_body(release_info, exe_name)
        if not expected:
            if allow_fallback:
                self.logger.info("Github API 中未找到 SHA256 校验值，跳过校验")
                return True
            self.logger.error("Github API 中未找到 SHA256 校验值")
            return False
        actual = self.calculate_sha256(file_path)
        if actual != expected:
            self.logger.error("SHA256 校验失败:")
            self.logger.warning(f"GitHub: {expected}")
            self.logger.warning(f"本地:   {actual}")
            return False
        self.logger.info("SHA256 校验成功")
        self.logger.info(f"GitHub: {expected}")
        self.logger.info(f"本地:   {actual}")
        return True

    def verify_file_sha256(self, file_path: str, expected: str) -> bool:
        """
        使用已知期望值校验文件 SHA256

        Args:
            file_path: 文件路径
            expected: 期望的 SHA256 值

        Returns:
            校验是否通过
        """
        actual = self.calculate_sha256(file_path)
        if actual != expected:
            self.logger.error("SHA256 校验失败:")
            self.logger.warning(f"GitHub: {expected}")
            self.logger.warning(f"本地:   {actual}")
            return False
        self.logger.info("SHA256 校验成功")
        self.logger.info(f"GitHub: {expected}")
        self.logger.info(f"本地:   {actual}")
        return True

    def extract_zip_with_progress(self, zip_path: str, extract_to: str,
                                   download_manager: DownloadManager) -> bool:
        """
        解压 ZIP 文件并显示进度

        Args:
            zip_path: ZIP 文件路径
            extract_to: 解压目标路径
            download_manager: DownloadManager 实例（用于进度条）

        Returns:
            bool: 操作是否成功
        """
        if not os.path.exists(zip_path):
            self.logger.error(f"ZIP 文件不存在: {zip_path}")
            return False

        try:
            Path(extract_to).mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                file_list = zip_ref.namelist()
                total_size = sum(file.file_size for file in zip_ref.filelist)

                self.logger.info(f"开始解压: {zip_path}")
                self.logger.info(f"文件数量: {len(file_list)}, 总大小: {total_size / (1024 * 1024):.2f} MB")

                extracted_size = 0
                for file_info in zip_ref.infolist():
                    zip_ref.extract(file_info, extract_to)
                    extracted_size += file_info.file_size
                    download_manager.print_progress("解压进度", (extracted_size / total_size) * 100,
                                                    extracted_size / (1024 * 1024), total_size / (1024 * 1024))

                download_manager.clear_progress_line()
                download_manager.reset_progress_timer()

            self.logger.info(f"解压完成: {zip_path} -> {extract_to}")
            return True
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"解压 ZIP 文件失败: {e}")
            return False

    def check_lite_zip_has_deps(self, zip_path: str) -> bool:
        """
        检查 CLI ZIP 文件中是否包含 deps 文件夹

        Args:
            zip_path: CLI ZIP 文件路径

        Returns:
            bool: 是否包含 deps 文件夹
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_name in zip_ref.namelist():
                    if file_name.startswith('deps/'):
                        self.logger.info("CLI ZIP 文件中存在 deps 文件夹")
                        return True
                self.logger.warning("CLI ZIP 文件中不包含 deps 文件夹")
                return False
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"检查 CLI ZIP 文件失败: {e}")
            return False

    @staticmethod
    def get_zip_version(zip_path: str) -> Optional[str]:
        """
        从 ZIP 文件内的 interface.json 读取版本号

        Args:
            zip_path: ZIP 文件路径

        Returns:
            版本号字符串（如 v3.28.3），读取失败返回 None
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                candidates = [n for n in zf.namelist() if n.endswith('interface.json')]
                for name in candidates:
                    data = json.loads(zf.read(name).decode('utf-8'))
                    version = data.get('version')
                    if version:
                        return version
                return None
        except Exception:
            return None

    def extract_deps_from_full_zip(self, gui_zip_file: str, m9a_folder: str,
                                    gui_zip_pattern: str,
                                    temp_folder: str,
                                    m9a_folders: list,
                                    download_manager: DownloadManager,
                                    gh_client: GitHubReleaseClient) -> bool:
        """
        从 GUI ZIP 文件中提取 deps 文件夹到 M9A 文件夹

        Args:
            gui_zip_file: GUI ZIP 文件路径
            m9a_folder: M9A 文件夹路径
            gui_zip_pattern: GUI ZIP 匹配模式
            temp_folder: 临时文件夹
            m9a_folders: M9A 文件夹列表
            download_manager: 下载管理器
            gh_client: GitHub Release 客户端

        Returns:
            操作是否成功
        """
        if gui_zip_file and os.path.exists(gui_zip_file):
            gui_zip_path = Path(gui_zip_file)
        else:
            gui_zip_regex = gh_client.compile_pattern(gui_zip_pattern)
            search_dirs = [Path(temp_folder), Path.cwd()]
            gui_zip_files = []

            for search_dir in search_dirs:
                if search_dir.exists():
                    gui_zip_files.extend([f for f in search_dir.glob('M9A-win-x86_64-v*-*.zip')
                                          if gui_zip_regex.match(f.name)])

            if not gui_zip_files:
                self.logger.warning(f"未找到匹配的 GUI ZIP 文件: {gui_zip_pattern}")
                return False

            gui_zip_path = gui_zip_files[0]

        self.logger.info(f"GUI ZIP 文件: {gui_zip_path}")

        try:
            m9a_path = Path(m9a_folder) if m9a_folder else Path(m9a_folders[0])
            m9a_path.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(gui_zip_path, 'r') as zip_ref:
                deps_files = [f for f in zip_ref.namelist() if f.startswith('deps/')]

                if not deps_files:
                    self.logger.critical(f"未找到 deps 文件夹: {gui_zip_path}")
                    return False

                total_size = sum(zip_ref.getinfo(f).file_size for f in deps_files)

                self.logger.info(f"开始提取 deps 文件夹: {len(deps_files)} 个文件, "
                                f"总大小: {total_size / (1024 * 1024):.2f} MB")

                extracted_size = 0
                for file_name in deps_files:
                    file_info = zip_ref.getinfo(file_name)
                    zip_ref.extract(file_info, m9a_path)
                    extracted_size += file_info.file_size
                    download_manager.print_progress("提取 deps", (extracted_size / total_size) * 100,
                                                    extracted_size / (1024 * 1024), total_size / (1024 * 1024))

                download_manager.clear_progress_line()
                download_manager.reset_progress_timer()

            self.logger.info(f"deps 文件夹已提取到: {m9a_path}")
            return True
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"提取 deps 文件夹失败: {e}")
            return False
