#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import hashlib
import json
import logging
import os
import zipfile

from pathlib import Path
from typing import Dict, Optional

from modules.github_release_client import GitHubReleaseClient
from modules.progress_bar import (
    create_progress_bar,
    format_ok, format_error,
)


class ZipManager:
    """ZIP 管理器，负责 ZIP 校验、解压、SHA256 计算与校验"""

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

    def extract_zip_with_progress(self, zip_path: str, extract_to: str) -> bool:
        """
        解压 ZIP 文件并显示进度

        Args:
            zip_path: ZIP 文件路径
            extract_to: 解压目标路径

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
                self.logger.debug(f"文件数量: {len(file_list)}, 总大小: {total_size / (1024 * 1024):.2f} MB")

                with create_progress_bar(
                    total=total_size,
                    desc=f"解压 {Path(zip_path).name}",
                ) as pbar:
                    try:
                        for file_info in zip_ref.infolist():
                            zip_ref.extract(file_info, extract_to)
                            pbar.update(file_info.file_size)
                    except Exception:
                        pbar.leave = True  # 错误时保留进度条
                        raise

            # ── 完成提示（亮绿色） ──
            print(format_ok("解压", Path(zip_path).name, extract_to, total_size))

            self.logger.debug(f"解压完成: {zip_path} -> {extract_to}")
            return True
        except (zipfile.BadZipFile, IOError, OSError) as e:
            self.logger.error(f"解压 ZIP 文件失败: {e}")
            print(format_error("解压", str(e)))
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

