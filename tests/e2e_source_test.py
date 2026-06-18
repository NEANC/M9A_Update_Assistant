#!/usr/bin/env python3
# -_- coding: utf-8 -*-
"""E2E 源码模式测试：配置验证、GitHub API 连通性、版本比较逻辑

用法：
    python tests/e2e_source_test.py              # 运行全部测试
    python tests/e2e_source_test.py --config      # 仅运行配置验证
    python tests/e2e_source_test.py --api         # 仅运行 GitHub API 测试
    python tests/e2e_source_test.py --version     # 仅运行版本比较测试
"""

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write_test_config(config_path: str) -> None:
    """生成 CI 测试用 config.ini"""
    m9a_dir = os.path.join(tempfile.gettempdir(), "ci_m9a_test")
    temp_dir = os.path.join(tempfile.gettempdir(), "ci_temp_m9a")
    archive_dir = os.path.join(tempfile.gettempdir(), "ci_archive_m9a")

    content = f"""[Paths]
m9a_folders = {m9a_dir}
temp_folder = {temp_dir}
archive_folder_path = {archive_dir}

[Logs]
save_enabled = true
max_files = 3

[GitHub]
repo = MAA1999/M9A
proxy =
m9a_update_channel = stable

[SelfUpdate]
enabled = true
self_update_channel = stable
"""
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(content)


def test_config_and_runtime(config_path: str) -> bool:
    """测试配置验证、运行环境检测、自更新跳过"""
    from M9A_Update_Assistant import M9AUpdateAssistant
    from modules.self_updater import SelfUpdater

    write_test_config(config_path)
    assistant = M9AUpdateAssistant(config_file=config_path)

    # ── 1. 验证运行环境检测 ──
    is_bundled, pkg_type = SelfUpdater.detect_package_type()
    assert not is_bundled, f"源码模式应检测为非打包，实际: {is_bundled}"
    print(f"[PASS] 运行环境检测: 源码模式 ({pkg_type})")

    # ── 2. 验证配置 ──
    assert assistant.validate_config(), "配置验证应通过"
    print("[PASS] config.ini 配置验证通过")

    # ── 3. 验证属性读取 ──
    print(f"[INFO] M9A 文件夹: {assistant.config.m9a_folders}")
    print(f"[INFO] 临时文件夹: {assistant.config.temp_folder}")
    print(f"[INFO] 存档文件夹: {assistant.config.archive_folder_path}")
    print(f"[INFO] GitHub 仓库: {assistant.config.github_repo}")
    print(f"[INFO] 自更新启用: {assistant.config.self_update_enabled}")

    # ── 4. 验证自更新在源码模式下被跳过 ──
    result = assistant.check_self_update()
    assert not result, "源码模式不应触发自更新"
    print("[PASS] 源码模式正确跳过自更新检查")

    return True


def test_github_api() -> bool:
    """测试 GitHub API 连通性"""
    from modules.github_release_client import GitHubReleaseClient

    logging.basicConfig(level=logging.WARNING)
    logger = logging.getLogger()

    client = GitHubReleaseClient("MAA1999/M9A", "stable", "", logger)
    info = client.get_latest_release_info()
    assert info is not None, "无法获取 M9A release 信息"
    tag = info.get("tag_name", "")
    assert tag, "release 中未找到 tag_name"
    print(f"[PASS] M9A 最新版本: {tag}")
    print(f"[INFO] Release URL: {info.get('html_url', 'N/A')}")

    return True


def test_version_compare() -> bool:
    """测试版本比较逻辑"""
    from modules.self_updater import SelfUpdater

    su = SelfUpdater("", "", logging.getLogger())

    tests = [
        ("v1.0.0", "v1.0.1", True),
        ("v1.0.1", "v1.0.0", False),
        ("v1.0.0", "v1.0.0", False),
        ("v1.0.0-alpha", "v1.0.0", True),
        ("v1.0.0", "v1.0.0-rc.1", False),
        ("v1.0.0-beta.1", "v1.0.0-beta.2", True),
        ("v3.0.0", "v4.0.0", True),
    ]
    for cur, new, expected in tests:
        result = su._version_newer_than(cur, new)
        status = "PASS" if result == expected else "FAIL"
        arrow = ">" if expected else "<="
        print(f"[{status}] {cur} {arrow} {new} (expected={expected}, got={result})")
        assert result == expected, f"版本比较错误: {cur} vs {new}"
    print("[PASS] 版本比较全部正确")

    return True


def test_build_tag_detection() -> bool:
    """测试构建版本标记检测"""
    from modules.self_updater import SelfUpdater

    tests = [
        ("v1.0.0", False, "正式版"),
        ("v0.0.1-build.g123456", True, "纯构建版"),
        ("v1.11.5-beta.5-2-build.ae83e00", True, "预发布+构建版"),
        ("v1.0.0-alpha.1", False, "预发布无构建"),
        ("v1.0.0-rc.2", False, "RC 无构建"),
        ("v2.0.0-build.g123abc", True, "dot 变体构建"),
    ]
    for version, expected, desc in tests:
        result = SelfUpdater._is_build_tag(version)
        status = "PASS" if result == expected else "FAIL"
        print(f"[{status}] {desc}: {version} → build_tag={result} (expected={expected})")
        assert result == expected, f"构建标记检测错误: {version}"
    print("[PASS] 构建版本标记检测全部正确")

    return True


def test_prerelease_weight() -> bool:
    """测试预发布权重计算"""
    from modules.self_updater import SelfUpdater

    tests = [
        ("v1.0.0-alpha", (1, 0), "alpha 无编号"),
        ("v1.0.0-alpha.1", (1, 1), "alpha.1"),
        ("v1.0.0-alpha.2", (1, 2), "alpha.2"),
        ("v1.0.0-beta", (2, 0), "beta 无编号"),
        ("v1.0.0-beta.1", (2, 1), "beta.1"),
        ("v1.0.0-rc", (3, 0), "rc 无编号"),
        ("v1.0.0-rc.3", (3, 3), "rc.3"),
        ("v1.0.0", (0, 0), "正式版无预发布"),
        ("v1.0.0-beta-2", (2, 2), "beta-2 连字符变体"),
    ]
    for version, expected, desc in tests:
        result = SelfUpdater._prerelease_weight(version)
        status = "PASS" if result == expected else "FAIL"
        print(f"[{status}] {desc}: {version} → weight={result} (expected={expected})")
        assert result == expected, f"预发布权重错误: {version}"
    print("[PASS] 预发布权重计算全部正确")

    return True


def test_self_update_source_skip() -> bool:
    """测试源码模式下自更新被跳过（不调用网络）"""
    from M9A_Update_Assistant import M9AUpdateAssistant

    config_path = os.path.join(tempfile.gettempdir(), "test_selfupdate_config.ini")
    write_test_config(config_path)
    # 覆盖启用自更新
    with open(config_path, 'a', encoding='utf-8') as f:
        f.write("\n# override for self-update test\n")
    import configparser
    c = configparser.ConfigParser()
    c.read(config_path, encoding='utf-8')
    c.set("SelfUpdate", "enabled", "true")
    with open(config_path, 'w', encoding='utf-8') as f:
        c.write(f)

    assistant = M9AUpdateAssistant(config_file=config_path)
    # 源码模式下 _is_bundled=False，check_self_update 第一行就 return False
    result = assistant.check_self_update()
    assert not result, "源码模式不应触发自更新（即使 config 启用）"
    print("[PASS] 源码模式 check_self_update 正确跳过（不调用网络）")

    return True


def main() -> None:
    """主入口"""
    args = set(sys.argv[1:])

    # 默认运行全部
    run_all = not args or "--all" in args
    config_path = os.path.join(tempfile.gettempdir(), "test_config.ini")

    try:
        if run_all or "--config" in args:
            print("=" * 60)
            print("[TEST] 配置验证 & 启动流程")
            print("=" * 60)
            test_config_and_runtime(config_path)
            print()

        if run_all or "--api" in args:
            print("=" * 60)
            print("[TEST] GitHub API 连通性")
            print("=" * 60)
            test_github_api()
            print()

        if run_all or "--version" in args:
            print("=" * 60)
            print("[TEST] 版本比较逻辑")
            print("=" * 60)
            test_version_compare()
            print()

        if run_all or "--self-update" in args:
            print("=" * 60)
            print("[TEST] 自更新组件测试")
            print("=" * 60)
            test_build_tag_detection()
            print()
            test_prerelease_weight()
            print()
            test_self_update_source_skip()
            print()

        print("[PASS] E2E Source 测试全部通过")
    except AssertionError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
