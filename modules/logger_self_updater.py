#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""自更新相关日志控制器：CLI 诊断模式日志 + 更新残留清理"""

import logging
import shutil
import sys

from datetime import datetime
from pathlib import Path

from modules.config_self_updater import UpdateState
from modules.logger import ColoredFormatter


def setup_mode_logger(log_prefix: str = "M9A_Mode") -> None:
    """为 CLI 模式设置日志（控制台 + 文件，不受 save_enabled 控制）"""
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

    try:
        exe_dir = Path(sys.argv[0]).resolve().parent
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = exe_dir / f'{log_prefix}_{timestamp}.log'

        file_handler = logging.FileHandler(str(log_path), encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logger.info(f"日志文件已创建: {log_path}")
    except Exception:
        print(f"[警告] 无法创建日志文件，仅输出到控制台", file=sys.stderr)


def cleanup_update_residue(logger: logging.Logger) -> None:
    """清理上次成功更新后的残留文件（PS1 脚本、lock、日志），失败则尝试从备份恢复"""
    state = UpdateState.load()
    if not state:
        return

    current_state = state.get("State", "state", fallback="")

    if current_state == "verified":
        logger.info("清理上次更新残留文件...")
        target_path = Path(state["target"])
        script_dir = target_path.parent

        cleanup_files = [
            Path(state["backup_file"]),
            script_dir / f"{target_path.stem}.old.exe",
            script_dir / "M9A_Update_Assistant_Update_Helper.ps1",
            script_dir / "M9A_Update_Assistant_Update.ps1",
            script_dir / "update_started.lock",
            script_dir / "update.log",
        ]
        # 清理 CLI 模式诊断日志（自检、重试、失败）
        for pattern in ("M9A_SelfUpdateVerify_*.log", "M9A_RetryUpdate_*.log", "M9A_UpdateFailed_*.log"):
            for f in script_dir.glob(pattern):
                cleanup_files.append(f)
        for f in cleanup_files:
            try:
                if f.exists():
                    f.unlink()
                    logger.debug(f"已删除残留文件: {f}")
            except OSError:
                pass

        state.delete()
        logger.info("残留文件清理完成")
    elif current_state in ("helper_started", "replacing", "pending_new_verify", "rollback"):
        logger.warning("检测到上次更新未完成，尝试恢复...")
        backup_file = Path(state["backup_file"])
        target = Path(state["target"])
        if backup_file.exists() and not target.exists():
            shutil.move(str(backup_file), str(target))
            logger.info("已从备份恢复")
        state.delete()

    elif current_state == "rollback_done":
        logger.info("检测到上次更新回滚完成，清理状态文件")
        state.delete()

    elif current_state == "failed_disabled":
        failed_ver = state["new_version"]
        logger.warning(f"自更新已禁用：版本 {failed_ver} 多次验证失败")
        logger.warning(f"将跳过版本 {failed_ver} 的自动更新，等待远端发布新版本")
