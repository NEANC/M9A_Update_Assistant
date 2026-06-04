#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""主流程日志控制器：控制台彩色输出 + 文件日志（受 config.ini [Logs] save_enabled 控制）"""

import logging
import configparser

from datetime import datetime
from pathlib import Path

import colorama


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


def setup_main_logger() -> logging.Logger:
    """创建主流程日志记录器（仅控制台，文件日志由 should_save_logs + add_file_logger 控制）"""
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


def should_save_logs(config_file: str = "config.ini") -> bool:
    """读取配置文件判断是否启用日志文件保存（不依赖完整配置加载）"""
    if not Path(config_file).exists():
        return True
    try:
        raw = configparser.ConfigParser()
        raw.read(config_file, encoding='utf-8')
        return raw.getboolean('Logs', 'save_enabled', fallback=True)
    except Exception:
        return True


def add_file_logger(logger: logging.Logger, version: str = "") -> logging.FileHandler:
    """添加文件日志记录器，返回 handler 以便后续管理"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"M9A_Update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if version:
        logger.debug(f"当前软件版本: {version}")
    logger.info(f"日志文件已创建: {log_file}")
    return file_handler


def cleanup_old_logs(logger: logging.Logger, max_files: int) -> None:
    """清理多余的日志文件"""
    log_dir = Path("logs")
    if not log_dir.exists():
        return

    log_files = list(log_dir.glob("M9A_Update_*.log"))
    if len(log_files) <= max_files:
        return

    log_files.sort(key=lambda x: x.stat().st_mtime)
    files_to_delete = log_files[:-max_files]
    for log_file in files_to_delete:
        try:
            log_file.unlink()
            logger.info(f"已删除多余的日志文件: {log_file}")
        except Exception as e:
            logger.error(f"删除日志文件 {log_file} 失败: {e}")
