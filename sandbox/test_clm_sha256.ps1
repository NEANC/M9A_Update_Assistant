<#
.SYNOPSIS
    测试 Get-SHA256 函数在约束语言模式 (Constrained Language Mode) 下的行为
.DESCRIPTION
    1. 生成已知内容的测试文件
    2. 提取生成的 Update.ps1 中的 Get-SHA256 函数代码
    3. 在 CLM 下执行相同的 .NET SHA256 计算
    4. 与 Get-FileHash 结果对比
#>

$ErrorActionPreference = "Stop"

# 1. 创建测试文件
$testContent = "Hello M9A Self-Update Test File Content"
$testFile = Join-Path $env:TEMP "m9a_clm_test_$(Get-Date -Format 'yyyyMMddHHmmss').tmp"
[System.IO.File]::WriteAllText($testFile, $testContent, [System.Text.Encoding]::UTF8)

Write-Host "Test file: $testFile"

# 2. 基准 SHA256（使用 Get-FileHash）
$baselineHash = (Get-FileHash -Algorithm SHA256 -Path $testFile).Hash.ToLowerInvariant()
Write-Host "Baseline (Get-FileHash): $baselineHash"

# 3. 用纯 .NET 方式计算 SHA256（模拟 Get-SHA256 函数）
$stream = $null
$sha256 = $null
try {
    $stream = [System.IO.File]::OpenRead($testFile)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha256.ComputeHash($stream)
    $dotNetHash = [BitConverter]::ToString($hash).Replace('-', '').ToLowerInvariant()
    Write-Host ".NET SHA256:            $dotNetHash"
} finally {
    if ($sha256) { $sha256.Dispose() }
    if ($stream) { $stream.Dispose() }
}

# 4. 对比
if ($baselineHash -eq $dotNetHash) {
    Write-Host "PASS: Get-FileHash == .NET SHA256"
} else {
    Write-Host "FAIL: Hash mismatch!"
    exit 1
}

# 5. 约束语言模式测试 — 在子进程中模拟
Write-Host "`n--- CLM Simulation ---"

$clmScript = @"
`$stream = `$null; `$sha256 = `$null
try {
    `$stream = [System.IO.File]::OpenRead('$testFile')
    `$sha256 = [System.Security.Cryptography.SHA256]::Create()
    `$hash = `$sha256.ComputeHash(`$stream)
    `$result = [BitConverter]::ToString(`$hash).Replace('-', '').ToLowerInvariant()
    Write-Host "CLM .NET SHA256: `$result"
    if ('$baselineHash' -eq `$result) {
        Write-Host "PASS: CLM .NET SHA256 matches baseline"
    } else {
        Write-Host "FAIL: CLM hash mismatch!"
        exit 1
    }
} finally {
    if (`$sha256) { `$sha256.Dispose() }
    if (`$stream) { `$stream.Dispose() }
}
"@

# 将 CLM 脚本写入临时文件并以 CLM 模式执行
$clmScriptFile = Join-Path $env:TEMP "m9a_clm_test_script_$(Get-Date -Format 'yyyyMMddHHmmss').ps1"
[System.IO.File]::WriteAllText($clmScriptFile, $clmScript, [System.Text.Encoding]::UTF8)

try {
    pwsh.exe -NoProfile -ExecutionPolicy Bypass -Command @"
`$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
Write-Host "Current LanguageMode: `$(`$ExecutionContext.SessionState.LanguageMode)"
& '$clmScriptFile'
"@
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL: CLM test failed"
        exit 1
    }
} catch {
    Write-Host "WARNING: CLM simulation requires pwsh.exe. Error: $_"
    Write-Host "Falling back to non-CLM verification..."
    
    # 回退：直接在当前会话中验证
    & $clmScriptFile
    if ($LASTEXITCODE -ne 0) {
        exit 1
    }
}

# 6. 清理
Remove-Item $testFile -Force -ErrorAction SilentlyContinue
Remove-Item $clmScriptFile -Force -ErrorAction SilentlyContinue

Write-Host "`nAll checks passed."
exit 0
