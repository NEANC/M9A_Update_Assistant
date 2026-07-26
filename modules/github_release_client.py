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
            release_version: Release 版本选择（preview 或 stable）
            proxy: 代理地址
            logger: 日志记录器
        """
        self.repo = repo
        self.release_version = release_version
        self.proxy = proxy
        self.logger = logger

    def _resolve_channel(self) -> str:
        """解析通道配置，兼容旧值 release→preview, latest→stable"""
        if self.release_version in ('preview', 'release'):
            return 'preview'
        if self.release_version in ('stable', 'latest'):
            return 'stable'
        return 'preview'

    @staticmethod
    def compile_pattern(pattern: str) -> re.Pattern:
        """
        将通配符模式编译为正则表达式

        Args:
            pattern: 含 * 通配符的模式串（如 M9A-win-x86_64-v*.zip）

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

                channel = self._resolve_channel()
                if channel == 'preview':
                    api_url = f"https://api.github.com/repos/{self.repo}/releases"
                    response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                    response.raise_for_status()
                    releases = response.json()
                    releases = [r for r in releases if not r.get('draft')]
                    if not releases:
                        self.logger.error("未找到任何有效的 release")
                        return None
                    release_info = releases[0]
                else:
                    api_url = f"https://api.github.com/repos/{self.repo}/releases/latest"
                    response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                    response.raise_for_status()
                    release_info = response.json()

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

    def get_release_by_tag(self, tag_name: str,
                            max_retries: int = 3,
                            retry_interval: int = 10) -> Optional[Dict]:
        """
        根据 tag 名称获取指定 GitHub release 信息

        Args:
            tag_name: 目标 tag 名称（允许不带 'v' 前缀）
            max_retries: 最大重试次数
            retry_interval: 重试间隔（秒）

        Returns:
            Dict: release 信息字典，如果获取失败则返回 None
        """
        v_tag = tag_name if tag_name.startswith('v') else f"v{tag_name}"

        for attempt in range(max_retries):
            try:
                headers = {'User-Agent': 'M9A-Update-Assistant'}
                proxies = {'http': self.proxy, 'https': self.proxy} if self.proxy else None
                api_url = f"https://api.github.com/repos/{self.repo}/releases/tags/{v_tag}"
                response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
                if response.status_code == 404:
                    self.logger.error(f"未找到 tag 为 {v_tag} 的 release")
                    return None
                response.raise_for_status()
                release_info = response.json()
                self.logger.info(f"已获取指定版本 release: {release_info.get('tag_name', v_tag)}")
                return release_info
            except requests.RequestException as e:
                self.logger.error(f"获取指定 release (tag={v_tag}) 失败: {e}")
                if attempt < max_retries - 1:
                    self.logger.info(f"等待 {retry_interval} 秒后重试...")
                    time.sleep(retry_interval)
            except Exception as e:
                self.logger.error(f"获取指定 release 时发生错误: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_interval)

        self.logger.error(f"获取指定 release (tag={v_tag}) 失败，已达到最大重试次数")
        return None

    def find_download_url(self, release_info: Dict, pattern: str) -> Optional[str]:
        """
        从 release 信息中查找匹配的下载链接

        Args:
            release_info: GitHub release 信息
            pattern: 文件名匹配模式

        Returns:
            下载 URL，如果未找到则返回 None
        """
        assets = release_info.get('assets', [])
        rx = self.compile_pattern(pattern)

        # 返回第一个匹配的资产。每个 release 应保证只有一个符合 pattern 的 CLI ZIP。
        for asset in assets:
            asset_name = asset.get('name', '')
            if rx.match(asset_name):
                file_size_mb = asset.get('size', 0) / (1024 * 1024)
                self.logger.info(f"找到匹配文件: {asset_name} ({file_size_mb:.2f} MB)")
                return asset.get('browser_download_url')

        return None

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
