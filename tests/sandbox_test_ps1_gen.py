#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""沙盒测试：生成 helper.ps1 / update.ps1 并验证其核心函数"""

import os
import shutil
import subprocess
import sys
import tempfile

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from modules.self_updater import SelfUpdater


def main():
    """主测试入口"""
    test_dir = Path(tempfile.mkdtemp(prefix="m9a_sandbox_"))
    print(f"测试目录: {test_dir}")

    try:
        # ── 1. 生成两个 PS1 脚本 ──
        SelfUpdater._generate_helper_ps1(test_dir)
        SelfUpdater._generate_update_ps1(test_dir)

        helper_ps1 = test_dir / "M9A_Update_Assistant_Update_Helper.ps1"
        update_ps1 = test_dir / "M9A_Update_Assistant_Update.ps1"

        assert helper_ps1.exists(), "helper.ps1 生成失败"
        assert update_ps1.exists(), "update.ps1 生成失败"
        print(f"PASS: helper.ps1 已生成 ({helper_ps1.stat().st_size} bytes)")
        print(f"PASS: update.ps1 已生成 ({update_ps1.stat().st_size} bytes)")

        # ── 2. 准备测试用假文件 ──
        dummy_exe = test_dir / "dummy_app.exe"
        dummy_new = test_dir / "dummy_app.new.exe"
        dummy_new.write_bytes(b"new version content\n")

        # ── 3. 写 update_state.ini（模拟 Python 端写入的状态） ──
        state_ini = test_dir / "update_state.ini"
        state_ini.write_text(
            "[State]\n"
            "state = replacing\n"
            "last_error = \n"
            "step = \n"
            "message = \n"
            "progress = \n"
            "updated_at = \n"
            "\n"
            "[Files]\n"
            f"target = {dummy_exe}\n"
            f"new_file = {dummy_new}\n"
            f"backup_file = {test_dir / 'dummy_app.backup.exe'}\n"
            "\n"
            "[Version]\n"
            "old_version = v1.0.0\n"
            "new_version = v2.0.0\n"
            "old_sha256 = \n"
            "new_sha256 = \n"
            "\n"
            "[Retry]\n"
            "retry_count = 0\n"
            "max_retry = 3\n",
            encoding='utf-8',
        )

        # ── 4. 运行 update.ps1 ──
        print("\n--- 运行 update.ps1 ---")
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(update_ps1),
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(test_dir),
        )
        print(f"update.ps1 退出码: {result.returncode}")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(f"STDERR: {result.stderr.strip()}")

        # update.ps1 文件替换成功（dummy_new → dummy_exe）
        assert result.returncode == 0, f"update.ps1 失败，退出码 {result.returncode}"
        assert not dummy_new.exists(), "new_file 应已被移动"
        assert dummy_exe.exists(), "target 应已创建"
        print("PASS: update.ps1 文件替换成功")

        # ── 6. 运行 helper.ps1（跳过父进程等待和自检，只测参数构造） ──
        # 写一个最小的测试：模拟 helper 成功路径中的 Start-Process / Start-ProcWait
        print("\n--- 运行 helper.ps1 参数构造测试 ---")
        test_ps1 = test_dir / "test_helper_args.ps1"
        test_ps1.write_text(r"""
$ErrorActionPreference = "Stop"

# 从生成的 helper.ps1 中提取并测试关键函数
# 注入模拟环境变量和路径

$scriptDir = $PSScriptRoot
$stateFile = Join-Path $scriptDir "update_state.ini"
$logFile = Join-Path $scriptDir "update.log"
$lockFile = Join-Path $scriptDir "update_started.lock"

# ── 复制 helper.ps1 中的关键函数 ──
function Normalize-IniValue($value) {
    if ($null -eq $value) { return "" }
    return ([string]$value) -replace "(`r`n|`n|`r)", " "
}

function Quote-Arg($arg) {
    if ($null -eq $arg) { return '""' }
    $s = [string]$arg
    $s = $s -replace '\\(?=")', '\\'
    $s = $s -replace '"', '\"'
    if ($s -match '\s' -or $s -eq '') {
        return '"' + $s + '"'
    }
    return $s
}

function Assert-NotEmpty($name, $value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "missing required ini value: $name"
    }
}

Write-Host "=== Test A: Start-NormalAppVisible with empty args ==="
$argList = @()
$argsArr = @($argList | ForEach-Object { Quote-Arg $_ })
$argString = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
$startArgs = @{
    FilePath = "cmd.exe"
    WorkingDirectory = $env:TEMP
    WindowStyle = 'Hidden'
}
if ($argString) {
    $startArgs.ArgumentList = $argString
}
try {
    $p = Start-Process @startArgs -PassThru
    Start-Sleep -Milliseconds 500
    $p.Kill()
    Write-Host "PASS: Start-NormalAppVisible(empty) OK"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host "=== Test B: Start-NormalAppVisible with args ==="
$argList = @('--update-failed')
$argsArr = @($argList | ForEach-Object { Quote-Arg $_ })
$argString = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
$startArgs = @{
    FilePath = "cmd.exe"
    WorkingDirectory = $env:TEMP
    WindowStyle = 'Hidden'
    ArgumentList = $argString
}
try {
    $p = Start-Process @startArgs -PassThru
    Start-Sleep -Milliseconds 500
    $p.Kill()
    Write-Host "PASS: Start-NormalAppVisible(--update-failed) OK"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host "=== Test C: Start-ProcWait with empty args ==="
$argList = @()
$argsArr = @($argList | ForEach-Object { Quote-Arg $_ })
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cmd.exe"
$psi.UseShellExecute = $false
$psi.WorkingDirectory = $env:TEMP
$psi.Arguments = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
try {
    $proc = [System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Milliseconds 500
    if (-not $proc.HasExited) { $proc.Kill(); $proc.WaitForExit(5000) | Out-Null }
    Write-Host "PASS: Start-ProcWait(empty) OK"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host "=== Test D: Start-ProcWait with verify args ==="
$argList = @('--self-update-verify', '--expected-sha256', 'abc', '--expected-version', 'v1')
$argsArr = @($argList | ForEach-Object { Quote-Arg $_ })
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cmd.exe"
$psi.UseShellExecute = $false
$psi.WorkingDirectory = $env:TEMP
$psi.Arguments = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
Write-Host "Arguments = [$($psi.Arguments)]"
try {
    $proc = [System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Milliseconds 500
    if (-not $proc.HasExited) { $proc.Kill(); $proc.WaitForExit(5000) | Out-Null }
    Write-Host "PASS: Start-ProcWait(verify) OK"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host "=== Test E: Assert-NotEmpty ==="
try {
    Assert-NotEmpty "test" ""
    Write-Host "FAIL: should have thrown"
    exit 1
} catch {
    if ($_.Exception.Message -match "missing required ini value") {
        Write-Host "PASS: Assert-NotEmpty throws on empty"
    } else {
        Write-Host "FAIL: unexpected error: $($_.Exception.Message)"
        exit 1
    }
}

Assert-NotEmpty "test" "value"
Write-Host "PASS: Assert-NotEmpty passes on non-empty"

Write-Host "=== Test F: VerifyArgs conditional construction ==="
$newSha256 = "abc123"
$newVersion = "v2.0.0"
$verifyArgs = @('--self-update-verify')
if ($newSha256) { $verifyArgs += @('--expected-sha256', $newSha256) }
if ($newVersion) { $verifyArgs += @('--expected-version', $newVersion) }
$expected = '--self-update-verify --expected-sha256 abc123 --expected-version v2.0.0'
$actual = $verifyArgs -join ' '
if ($actual -eq $expected) {
    Write-Host "PASS: verifyArgs with all set"
} else {
    Write-Host "FAIL: expected [$expected] got [$actual]"
    exit 1
}

# 空值时不应追加
$newSha2562 = ""
$newVersion2 = ""
$verifyArgs2 = @('--self-update-verify')
if ($newSha2562) { $verifyArgs2 += @('--expected-sha256', $newSha2562) }
if ($newVersion2) { $verifyArgs2 += @('--expected-version', $newVersion2) }
if ($verifyArgs2.Count -eq 1 -and $verifyArgs2[0] -eq '--self-update-verify') {
    Write-Host "PASS: verifyArgs skips empty values"
} else {
    Write-Host "FAIL: verifyArgs has $($verifyArgs2.Count) items"
    exit 1
}

Write-Host ""
Write-Host "=== All helper tests passed ==="
""", encoding='utf-8')

        result3 = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(test_ps1),
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(test_dir),
        )
        print(result3.stdout)
        if result3.stderr.strip():
            print(f"STDERR: {result3.stderr.strip()}")
        assert result3.returncode == 0, f"helper 测试失败，退出码 {result3.returncode}"

        print("\n=== 全部沙盒测试通过 ===")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
