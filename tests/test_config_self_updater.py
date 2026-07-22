#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import os
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config_self_updater import UpdateState


class TestUpdateStateInit(unittest.TestCase):
    """UpdateState 初始化和默认值测试"""

    def setUp(self):
        _suppress_self_updater_logs()
        self.original_argv0 = sys.argv[0]

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()

    def test_default_state_is_idle(self):
        """新实例默认状态为 idle"""
        state = UpdateState()
        self.assertEqual(state["state"], "idle")

    def test_default_empty_fields(self):
        """默认文件和版本字段为空"""
        state = UpdateState()
        self.assertEqual(state["target"], "")
        self.assertEqual(state["runtime_dir"], "")
        self.assertEqual(state["helper_ps1"], "")
        self.assertEqual(state["update_ps1"], "")
        self.assertEqual(state["lock_file"], "")
        self.assertEqual(state["log_file"], "")
        self.assertEqual(state["new_file"], "")
        self.assertEqual(state["backup_file"], "")
        self.assertEqual(state["old_version"], "")
        self.assertEqual(state["new_version"], "")
        self.assertEqual(state["old_sha256"], "")
        self.assertEqual(state["new_sha256"], "")
        self.assertEqual(state["step"], "")
        self.assertEqual(state["level"], "")
        with self.assertRaises(KeyError):
            _ = state["current_step"]
        self.assertEqual(state["message"], "")
        self.assertEqual(state["progress"], "")
        self.assertEqual(state["updated_at"], "")

    def test_default_retry_values(self):
        """默认重试配置"""
        state = UpdateState()
        self.assertEqual(state.get("Retry", "retry_count"), "0")
        self.assertEqual(state.get("Retry", "max_retry"), "3")

    def test_default_last_error_empty(self):
        """默认 last_error 为空"""
        state = UpdateState()
        self.assertEqual(state.get("State", "last_error"), "")


class TestUpdateStateReadWrite(unittest.TestCase):
    """UpdateState 读写操作测试"""

    def setUp(self):
        _suppress_self_updater_logs()
        self.original_argv0 = sys.argv[0]

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()

    def test_set_and_get_state(self):
        """设置和读取状态值"""
        state = UpdateState()
        state["state"] = "downloaded_verified"
        self.assertEqual(state["state"], "downloaded_verified")

    def test_set_and_get_file_paths(self):
        """设置和读取文件路径"""
        state = UpdateState()
        state["target"] = r"C:\App\app.exe"
        state["runtime_dir"] = r"C:\App\SelfUpdate\v1.0.0"
        state["helper_ps1"] = r"C:\App\SelfUpdate\v1.0.0\helper.ps1"
        state["update_ps1"] = r"C:\App\SelfUpdate\v1.0.0\update.ps1"
        state["lock_file"] = r"C:\App\SelfUpdate\v1.0.0\update.lock"
        state["log_file"] = r"C:\App\update.log"
        state["new_file"] = r"C:\App\app.new.exe"
        state["backup_file"] = r"C:\App\app.backup.exe"

        self.assertEqual(state["target"], r"C:\App\app.exe")
        self.assertEqual(state["runtime_dir"], r"C:\App\SelfUpdate\v1.0.0")
        self.assertEqual(state["helper_ps1"], r"C:\App\SelfUpdate\v1.0.0\helper.ps1")
        self.assertEqual(state["update_ps1"], r"C:\App\SelfUpdate\v1.0.0\update.ps1")
        self.assertEqual(state["lock_file"], r"C:\App\SelfUpdate\v1.0.0\update.lock")
        self.assertEqual(state["log_file"], r"C:\App\update.log")
        self.assertEqual(state["new_file"], r"C:\App\app.new.exe")
        self.assertEqual(state["backup_file"], r"C:\App\app.backup.exe")

    def test_set_and_get_version_info(self):
        """设置和读取版本信息"""
        state = UpdateState()
        state["old_version"] = "v1.10.0"
        state["new_version"] = "v1.11.0"
        state["new_sha256"] = "abc123"

        self.assertEqual(state["old_version"], "v1.10.0")
        self.assertEqual(state["new_version"], "v1.11.0")
        self.assertEqual(state["new_sha256"], "abc123")

    def test_set_and_get_last_error(self):
        """设置和读取错误信息"""
        state = UpdateState()
        state["last_error"] = "文件替换失败"
        self.assertEqual(state["last_error"], "文件替换失败")

    def test_set_and_get_status_step_and_level(self):
        """设置和读取与 PowerShell 状态字段一致的 step 与 level。"""
        state = UpdateState()
        state["step"] = "replace_done"
        state["level"] = "INFO"
        state.save()

        loaded = UpdateState.load()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["step"], "replace_done")
        self.assertEqual(loaded["level"], "INFO")

    def test_get_with_fallback(self):
        """get 方法的 fallback 参数"""
        state = UpdateState()
        self.assertEqual(state.get("State", "nonexistent", fallback="默认"), "默认")

    def test_set_retry_values(self):
        """设置重试值"""
        state = UpdateState()
        state.set("Retry", "retry_count", "2")
        state.set("Retry", "max_retry", "5")
        self.assertEqual(state.get("Retry", "retry_count"), "2")
        self.assertEqual(state.get("Retry", "max_retry"), "5")

    def test_setitem_invalid_key_raises(self):
        """无效键名抛出 KeyError"""
        state = UpdateState()
        with self.assertRaises(KeyError):
            _ = state["nonexistent_key"]
        with self.assertRaises(KeyError):
            state["nonexistent_key"] = "value"


class TestUpdateStateSaveLoad(unittest.TestCase):
    """UpdateState 保存和加载测试"""

    def setUp(self):
        _suppress_self_updater_logs()
        self.original_argv0 = sys.argv[0]
        self.tmpdir = tempfile.mkdtemp()
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")
        Path(sys.argv[0]).touch()

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        """保存后加载字段保持一致"""
        state = UpdateState()
        state["state"] = "pending_new_verify"
        state["target"] = r"C:\App\app.exe"
        state["runtime_dir"] = r"C:\App\SelfUpdate\v1.0.0"
        state["helper_ps1"] = r"C:\App\SelfUpdate\v1.0.0\helper.ps1"
        state["update_ps1"] = r"C:\App\SelfUpdate\v1.0.0\update.ps1"
        state["lock_file"] = r"C:\App\SelfUpdate\v1.0.0\update.lock"
        state["log_file"] = r"C:\App\update.log"
        state["new_file"] = r"C:\App\app.new.exe"
        state["backup_file"] = r"C:\App\app.backup.exe"
        state["old_version"] = "v1.10.0"
        state["new_version"] = "v1.11.0"
        state["old_sha256"] = "abc111"
        state["new_sha256"] = "abcdef1234567890"
        state["last_error"] = "测试错误"
        state.set("Retry", "retry_count", "1")
        state.set("Retry", "max_retry", "5")
        state.save()

        loaded = UpdateState.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["state"], "pending_new_verify")
        self.assertEqual(loaded["target"], r"C:\App\app.exe")
        self.assertEqual(loaded["runtime_dir"], r"C:\App\SelfUpdate\v1.0.0")
        self.assertEqual(loaded["helper_ps1"], r"C:\App\SelfUpdate\v1.0.0\helper.ps1")
        self.assertEqual(loaded["update_ps1"], r"C:\App\SelfUpdate\v1.0.0\update.ps1")
        self.assertEqual(loaded["lock_file"], r"C:\App\SelfUpdate\v1.0.0\update.lock")
        self.assertEqual(loaded["log_file"], r"C:\App\update.log")
        self.assertEqual(loaded["new_file"], r"C:\App\app.new.exe")
        self.assertEqual(loaded["backup_file"], r"C:\App\app.backup.exe")
        self.assertEqual(loaded["old_version"], "v1.10.0")
        self.assertEqual(loaded["new_version"], "v1.11.0")
        self.assertEqual(loaded["old_sha256"], "abc111")
        self.assertEqual(loaded["new_sha256"], "abcdef1234567890")
        self.assertEqual(loaded.get("State", "last_error"), "测试错误")
        self.assertEqual(loaded.get("Retry", "retry_count"), "1")
        self.assertEqual(loaded.get("Retry", "max_retry"), "5")

    def test_load_nonexistent_file(self):
        """加载不存在的文件返回 None"""
        # 确保无残留
        _cleanup_state_file()
        loaded = UpdateState.load()
        self.assertIsNone(loaded)


class TestUpdateStateTransition(unittest.TestCase):
    """UpdateState 状态转换测试"""

    def setUp(self):
        _suppress_self_updater_logs()
        self.original_argv0 = sys.argv[0]
        self.tmpdir = tempfile.mkdtemp()
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")
        Path(sys.argv[0]).touch()

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_transitions(self):
        """所有有效状态转换"""
        state = UpdateState()
        for target_state in UpdateState.VALID_STATES:
            state.transition(target_state)
            self.assertEqual(state["state"], target_state)

    def test_invalid_transition_raises(self):
        """无效状态转换抛出异常"""
        state = UpdateState()
        with self.assertRaises(ValueError):
            state.transition("invalid_state")

    def test_transition_persists(self):
        """状态转换自动持久化"""
        state = UpdateState()
        state.transition("downloaded_verified")

        loaded = UpdateState.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["state"], "downloaded_verified")


class TestUpdateStateDelete(unittest.TestCase):
    """UpdateState delete 测试"""

    def setUp(self):
        _suppress_self_updater_logs()
        self.original_argv0 = sys.argv[0]
        self.tmpdir = tempfile.mkdtemp()
        sys.argv[0] = os.path.join(self.tmpdir, "test_app.exe")
        Path(sys.argv[0]).touch()

    def tearDown(self):
        sys.argv[0] = self.original_argv0
        _cleanup_state_file()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_delete_removes_file(self):
        """delete 删除状态文件"""
        state = UpdateState()
        state["state"] = "verified"
        state.save()

        state.delete()
        loaded = UpdateState.load()
        self.assertIsNone(loaded)

    def test_delete_nonexistent_no_error(self):
        """删除不存在的文件不抛异常"""
        state = UpdateState()
        state["state"] = "verified"
        state.save()
        state.delete()
        state.delete()


def _suppress_self_updater_logs():
    """抑制 self_updater 模块的日志输出"""
    logging.getLogger("M9AUpdateAssistant").setLevel(logging.CRITICAL)


def _cleanup_state_file():
    """清理可能残留的 update_state.ini"""
    ini_path = Path(sys.argv[0]).resolve().with_name(UpdateState.STATE_FILE_NAME)
    try:
        ini_path.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == '__main__':
    unittest.main()
