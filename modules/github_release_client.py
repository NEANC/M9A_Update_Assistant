#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import re
import time
import requests

from typing import Any, Dict, List, Optional


class GitHubReleaseClient:
    """GitHub Release 客户端，负责 API 请求、asset 查找、digest 获取"""

    def __init__(self, repo: str, release_version: str, proxy: str, logger: logging.Logger):
        """
        初始化 GitHub Release 客户端

        Args:
            repo: GitHub 仓库地址（格式：用户名/仓库名）
            release_version: Release 版本选择（release 或 latest）
            proxy: 代理地址
            logger: 日志记录器
        """
        self.repo = repo
        self.release_version = release_version
        self.proxy = proxy
        self.logger = logger

    @staticmethod
    def compile_pattern(pattern: str) -> re.Pattern:
        """
        将通配符模式编译为正则表达式

        Args:
            pattern: 含 * 通配符的模式串（如 M9A-win-x86_64-v*-Lite.zip）

        Returns:
            编译后的正则对象，匹配完整文件名
        """
        escaped = re.escape(pattern).replace(r'\*', r'.+')
        return re.compile('^' + escaped + '$')

    def get_latest_release_info(self, max_retries: int = 3, retry_interval: int = 10) -> Optional[Dict]:
        """
        获取 GitHub 最新 release 信息

        Args:
            max_retries: 最大重试次数
            retry_interval: 重试间隔（秒）

        Returns:
            Dict: release 信息字典，如果获取失败则返回 None
        """
        for attempt in range(max_retries):
            try:
                headers = {'User-Agent': 'M9A-Update-Assistant'}
                proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None

                if self.release_version == 'release':
                    api_url = f"https://api.github.com/repos/{self.repo}/releases"
                    response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                    response.raise_for_status()
                    releases = response.json()
                    if not releases:
                        self.logger.error("未找到任何 release")
                        return None
                    release_info = releases[0]
                elif self.release_version == 'latest':
                    api_url = f"https://api.github.com/repos/{self.repo}/releases/latest"
                    response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                    response.raise_for_status()
                    release_info = response.json()
                else:
                    self.logger.error(f"未知的 release_version: {self.release_version}")
                    return None

                self.logger.info(f"GitHub 版本: {release_info.get('tag_name', 'Unknown')}")
                return release_info
            except requests.RequestException as e:
                self.logger.error(f"获取 GitHub release 信息失败: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    self.logger.info(f"重试获取 release 信息（{attempt + 1}/{max_retries}）")
                else:
                    self.logger.error(f"获取 GitHub release 信息失败，已达到最大重试次数")
                    return None
            except Exception as e:
                self.logger.error(f"获取 GitHub release 信息时发生错误: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
                    self.logger.info(f"重试获取 release 信息（{attempt + 1}/{max_retries}）")
                else:
                    self.logger.error(f"获取 GitHub release 信息失败，已达到最大重试次数")
                    return None

        return None

    def parse_release_keywords(self, release_info: Dict) -> Dict[str, Any]:
        """
        解析 release 的 body 字段，提取 CLI 和 GUI 版本的关键词

        Args:
            release_info: GitHub release 信息

        Returns:
            Dict: 包含 'cli'、'gui' 和 'gui_keywords' 的字典
        """
        body = release_info.get('body', '')
        if not body:
            self.logger.warning("Github API: release body 为空，使用默认关键词")
            return {'cli': 'Lite', 'gui': 'Full', 'gui_keywords': ['Full']}

        cli_keywords = re.findall(r'(\w+)\s*=\s*命令行版', body)
        gui_keywords = re.findall(r'(\w+)\s*=\s*图形界面版', body)

        cli_keyword = cli_keywords[-1] if cli_keywords else 'Lite'
        gui_keyword = gui_keywords[-1] if gui_keywords else 'Full'

        if gui_keywords:
            gui_versions_str = ', '.join(gui_keywords)
            self.logger.debug(f"从 Github API 中提取关键词: 命令行版={cli_keyword}, 图形界面版=[{gui_versions_str}]")
        else:
            self.logger.debug(f"从 Github API 中提取关键词: 命令行版={cli_keyword}, 图形界面版={gui_keyword}")

        return {
            'cli': cli_keyword,
            'gui': gui_keyword,
            'gui_keywords': gui_keywords if gui_keywords else ['Full']
        }

    def find_download_url(self, release_info: Dict, pattern: str,
                           select_smallest: bool = False,
                           exclude_patterns: Optional[List[str]] = None) -> Optional[str]:
        """
        从 release 信息中查找匹配的下载链接

        Args:
            release_info: GitHub release 信息
            pattern: 文件名匹配模式
            select_smallest: 是否在多个匹配项中选择最小的文件
            exclude_patterns: 需要排除的文件名匹配模式列表

        Returns:
            下载 URL，如果未找到则返回 None
        """
        assets = release_info.get('assets', [])
        matched_assets = []
        exclude_regexes = [self.compile_pattern(ep) for ep in (exclude_patterns or [])]

        for asset in assets:
            asset_name = asset.get('name', '')
            if self.compile_pattern(pattern).match(asset_name):
                if any(rx.match(asset_name) for rx in exclude_regexes):
                    continue
                matched_assets.append(asset)

        if not matched_assets:
            return None

        if select_smallest and len(matched_assets) > 1:
            matched_assets.sort(key=lambda x: x.get('size', float('inf')))
            chosen_file = matched_assets[0]
            file_name = chosen_file.get('name', '')
            file_size_mb = chosen_file.get('size', 0) / (1024 * 1024)

            file_info_list = []
            for asset in matched_assets:
                name = asset.get('name', '')
                size_mb = asset.get('size', 0) / (1024 * 1024)
                keyword = name.split('-')[-1].replace('.zip', '')
                file_info_list.append(f"{keyword} ({size_mb:.2f} MB)")
            file_info_str = ', '.join(file_info_list)

            self.logger.info(f"找到 {len(matched_assets)} 个匹配文件: [{file_info_str}]")
            self.logger.info(f"选择最小的图形界面版本: {file_name} ({file_size_mb:.2f} MB)")
        else:
            chosen_file = matched_assets[0]
            file_name = chosen_file.get('name', '')
            file_size_mb = chosen_file.get('size', 0) / (1024 * 1024)
            self.logger.info(f"找到匹配文件: {file_name} ({file_size_mb:.2f} MB)")

        return matched_assets[0].get('browser_download_url')

    def get_asset_sha256(self, release_info: Dict, asset_name: str) -> Optional[str]:
        """
        从 GitHub release 信息中获取资产的 SHA256 哈希值

        Args:
            release_info: GitHub release 信息
            asset_name: 资产文件名

        Returns:
            SHA256 哈希值，如果未找到则返回 None
        """
        assets = release_info.get('assets', [])
        for asset in assets:
            if asset.get('name') == asset_name:
                digest = asset.get('digest', '')
                if digest.startswith('sha256:'):
                    return digest[7:]

        body = release_info.get('body', '')
        lines = body.split('\n')

        for line in lines:
            if asset_name in line and 'sha256' in line.lower():
                match = re.search(r'[0-9a-f]{64}', line.lower())
                if match:
                    return match.group(0)

        return None

    def get_exe_sha256_from_body(self, release_info: Dict, exe_name: str) -> Optional[str]:
        """
        从 release body 中提取指定 EXE 文件的 SHA256 哈希值

        Args:
            release_info: GitHub release 信息
            exe_name: EXE 文件名

        Returns:
            SHA256 哈希值，未找到则返回 None
        """
        body = release_info.get('body', '')
        for line in body.split('\n'):
            if exe_name in line and 'sha256' in line.lower():
                match = re.search(r'[0-9a-f]{64}', line.lower())
                if match:
                    return match.group(0)
        return None
