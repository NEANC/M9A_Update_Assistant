<#
.SYNOPSIS
    E2E Build 验证脚本：启动打包后的 EXE 并检查输出
.DESCRIPTION
    从环境变量读取 CI_EXE / CI_EXE_DIR，启动 EXE，验证 stdout/stderr/日志。
    用法：
        $env:CI_EXE = "path\to\exe"
        $env:CI_EXE_DIR = "path\to\exe\dir"
        & sandbox\e2e_build_verify.ps1
#>

param(
    [string]$ExePath = $env:CI_EXE,
    [string]$WorkDir = $env:CI_EXE_DIR,
    [string]$ExtraArgs = "--not-delete",
    [switch]$SelfUpdateEnabled
)

$ErrorActionPreference = "Stop"

Write-Host "======================================================"
Write-Host "启动 EXE: $ExePath"
Write-Host "工作目录: $WorkDir"
Write-Host "======================================================"

$stdoutFile = Join-Path $WorkDir "stdout.txt"
$stderrFile = Join-Path $WorkDir "stderr.txt"

$proc = Start-Process -FilePath $ExePath -WorkingDirectory $WorkDir `
    -ArgumentList $ExtraArgs `
    -WindowStyle Hidden -Wait -PassThru `
    -RedirectStandardOutput $stdoutFile `
    -RedirectStandardError $stderrFile

Write-Host "退出码: $($proc.ExitCode)"

$stdout = Get-Content $stdoutFile -Raw -ErrorAction SilentlyContinue
$stderr = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue

Write-Host "--- STDOUT ---"
Write-Host $stdout
Write-Host "--- STDERR ---"
Write-Host $stderr

$combined = "$stdout`n$stderr"
$allPassed = $true

# ── 验证 1: 配置验证通过 ──
if ($combined -match "配置验证通过") {
    Write-Host "[PASS] 配置验证通过"
} else {
    Write-Error "FAIL: 未找到 '配置验证通过'"
    $allPassed = $false
}

# ── 验证 2: 自更新行为 ──
if ($SelfUpdateEnabled) {
    # 自更新启用模式：验证版本检查流程已启动
    if ($combined -match "开始检查软件版本") {
        Write-Host "[PASS] 自更新检查已启动"
    } else {
        Write-Error "FAIL: 未找到 '开始检查软件版本'"
        $allPassed = $false
    }
    # 验证运行环境检测（打包模式）
    if ($combined -match "打包模式" -or $combined -match "运行环境") {
        Write-Host "[PASS] 打包模式环境检测通过"
    } else {
        Write-Host "[WARN] 未检测到打包模式环境信息"
    }
    # 检查是否有版本比较结果或网络请求
    if ($combined -match "检测到新版本" -or
        $combined -match "当前版本已最新" -or
        $combined -match "版本号校验错误") {
        Write-Host "[PASS] 版本比对已执行并有明确结论"
    } else {
        Write-Host "[WARN] 未检测到版本比对结论（可能 API 失败或超时）"
    }
} else {
    # 自更新禁用模式：验证被正确跳过
    if ($combined -match "已禁用软件更新" -or $combined -match "调试模式") {
        Write-Host "[PASS] 自更新已禁用，正确跳过"
    } else {
        Write-Host "[WARN] 未检测到自更新跳过消息"
    }
}

# ── 验证 3: 日志文件已创建 ──
if ($combined -match "日志文件已创建") {
    Write-Host "[PASS] 日志文件已创建"
} else {
    Write-Error "FAIL: 未找到 '日志文件已创建'"
    $allPassed = $false
}

# ── 验证 4: 日志文件内容 ──
$logFiles = Get-ChildItem -Path $WorkDir -Recurse -Filter "M9A_Update_*.log" `
    | Sort-Object LastWriteTime -Descending
if ($logFiles) {
    $latest = $logFiles[0]
    Write-Host "[PASS] 日志文件: $($latest.FullName)"
    $logContent = Get-Content $latest.FullName -Raw
    Write-Host "--- LOG (first 20 lines) ---"
    $logContent -split "`n" | Select-Object -First 20 | ForEach-Object { Write-Host $_ }

    if ($logContent -match "运行环境" -or $logContent -match "打包模式") {
        Write-Host "[PASS] 日志包含运行环境信息"
    } else {
        Write-Host "[WARN] 日志未包含运行环境信息"
    }
} else {
    Write-Error "FAIL: 未生成日志文件"
    $allPassed = $false
}

if (-not $allPassed) {
    Write-Error "E2E Build 验证失败"
    exit 1
}

Write-Host ""
Write-Host "[PASS] E2E Build 验证通过"
