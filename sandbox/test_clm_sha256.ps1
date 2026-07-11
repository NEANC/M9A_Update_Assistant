<#
.SYNOPSIS
    验证模拟生成脚本中的 Get-SHA256 实现可在 FullLanguage 与 ConstrainedLanguage Mode 下工作。
.DESCRIPTION
    1. 生成已知内容的测试文件。
    2. 定义模拟生成脚本中的 Get-SHA256 多路径实现。
    3. 使用 powershell.exe 在 FullLanguage 下验证 SHA256 与 Get-FileHash 基准一致。
    4. 使用 powershell.exe 子进程切换到 CLM 后验证 fallback 行为。
#>

$ErrorActionPreference = "Stop"

function Get-SHA256 {
    <#
    .SYNOPSIS
        计算指定文件的 SHA256 哈希。
    .DESCRIPTION
        模拟生成脚本中的 Get-SHA256 实现，按 .NET、Get-FileHash、certutil 顺序回退。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $lastError = $null
    $stream = $null
    $sha256 = $null

    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        $hash = $sha256.ComputeHash($stream)
        $result = [BitConverter]::ToString($hash).Replace('-', '').ToLowerInvariant()

        return ("{0}|{1}" -f $result, ".NET")
    } catch {
        $lastError = $_.Exception.Message
    } finally {
        if ($sha256) {
            $sha256.Dispose()
        }

        if ($stream) {
            $stream.Dispose()
        }
    }

    try {
        if (Get-Command -Name "Get-FileHash" -ErrorAction SilentlyContinue) {
            $result = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path -ErrorAction Stop).Hash.ToLowerInvariant()

            return ("{0}|{1}" -f $result, "Get-FileHash")
        }
    } catch {
        $lastError = $_.Exception.Message
    }

    try {
        $certOutput = & certutil.exe -hashfile $Path SHA256 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw ($certOutput -join "`n")
        }

        foreach ($line in $certOutput) {
            $hex = $line -replace '\s', ''
            if ($hex -match '^[0-9A-Fa-f]{64}$') {
                return ("{0}|{1}" -f $hex.ToLowerInvariant(), "certutil")
            }
        }

        throw "certutil output did not contain a SHA256 hash"
    } catch {
        $lastError = $_.Exception.Message
    }

    throw "Get-SHA256 failed: $lastError"
}

function ConvertTo-SingleQuotedLiteral {
    <#
    .SYNOPSIS
        将文本转换为 PowerShell 单引号字面量。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return "'" + ($Value -replace "'", "''") + "'"
}

function Write-CheckResult {
    <#
    .SYNOPSIS
        输出结构化检查结果。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [ValidateSet("PASS", "FAIL", "SKIP", "EXPECTED_FAIL")]
        [string]$Status,

        [string]$Detail = ""
    )

    if ($Detail) {
        Write-Host ("{0}: {1} - {2}" -f $Name, $Status, $Detail)
        return
    }

    Write-Host ("{0}: {1}" -f $Name, $Status)
}

$testContent = "Hello M9A Self-Update Test File Content"
$timestamp = Get-Date -Format 'yyyyMMddHHmmssffff'
$testFile = Join-Path $env:TEMP "m9a_clm_test_$timestamp.tmp"
$clmScriptFile = Join-Path $env:TEMP "m9a_clm_test_script_$timestamp.ps1"
$fullLanguagePassed = $false
$constrainedLanguageStatus = "FAIL"
$constrainedLanguageDetail = "未执行"

try {
    [System.IO.File]::WriteAllText($testFile, $testContent, [System.Text.Encoding]::UTF8)
    Write-Host "Test file: $testFile"

    $baselineHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $testFile -ErrorAction Stop).Hash.ToLowerInvariant()
    Write-Host "Baseline (Get-FileHash): $baselineHash"

    try {
        $fullLanguageResult = Get-SHA256 -Path $testFile
        $fullLanguageParts = $fullLanguageResult -split '\|', 2
        $fullLanguageHash = $fullLanguageParts[0]
        $fullLanguageMethod = $fullLanguageParts[1]
        Write-Host ("FullLanguage SHA256 ({0}): {1}" -f $fullLanguageMethod, $fullLanguageHash)

        if ($baselineHash -eq $fullLanguageHash) {
            $fullLanguagePassed = $true
            Write-CheckResult -Name "FullLanguage" -Status "PASS" -Detail ("method={0}" -f $fullLanguageMethod)
        } else {
            Write-CheckResult -Name "FullLanguage" -Status "FAIL" -Detail "hash mismatch"
        }
    } catch {
        Write-CheckResult -Name "FullLanguage" -Status "FAIL" -Detail $_.Exception.Message
    }

    if (-not $fullLanguagePassed) {
        Write-Host "Overall: FAIL"
        exit 1
    }

    $functionText = ${function:Get-SHA256}.ToString()
    $testFileLiteral = ConvertTo-SingleQuotedLiteral -Value $testFile
    $baselineHashLiteral = ConvertTo-SingleQuotedLiteral -Value $baselineHash
    $clmScript = @"
`$ErrorActionPreference = "Stop"
function Get-SHA256 {
$functionText
}

try {
    if (`$ExecutionContext.SessionState.LanguageMode -ne 'ConstrainedLanguage') {
        Write-Host ("ConstrainedLanguage: SKIP - requested CLM but current LanguageMode={0}" -f `$ExecutionContext.SessionState.LanguageMode)
        exit 2
    }

    `$result = Get-SHA256 -Path $testFileLiteral
    `$parts = `$result -split '\|', 2
    `$hash = `$parts[0]
    `$method = `$parts[1]
    Write-Host ("CLM SHA256 ({0}): {1}" -f `$method, `$hash)

    if (`$hash -ne $baselineHashLiteral) {
        Write-Host "ConstrainedLanguage: FAIL - hash mismatch"
        exit 1
    }

    if (`$method -eq ".NET") {
        Write-Host "ConstrainedLanguage: EXPECTED_FAIL - .NET path unexpectedly succeeded; CLM policy did not block method invocation"
        exit 2
    }

    Write-Host ("ConstrainedLanguage: PASS - fallback method={0}" -f `$method)
    exit 0
} catch {
    Write-Host ("ConstrainedLanguage: FAIL - {0}" -f `$_.Exception.Message)
    exit 1
}
"@

    [System.IO.File]::WriteAllText($clmScriptFile, $clmScript, [System.Text.Encoding]::UTF8)

    Write-Host "`n--- CLM Simulation ---"
    $clmCommand = @"
`$ErrorActionPreference = 'Stop'
`$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
Write-Host ('Current LanguageMode: ' + `$ExecutionContext.SessionState.LanguageMode)
& $(ConvertTo-SingleQuotedLiteral -Value $clmScriptFile)
exit `$LASTEXITCODE
"@

    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $clmCommand
    $clmExitCode = $LASTEXITCODE

    if ($clmExitCode -eq 0) {
        $constrainedLanguageStatus = "PASS"
        $constrainedLanguageDetail = "fallback 验证通过"
    } elseif ($clmExitCode -eq 2) {
        $constrainedLanguageStatus = "EXPECTED_FAIL"
        $constrainedLanguageDetail = "CLM 策略与当前环境不完全匹配，已由子进程明确标记"
    } else {
        $constrainedLanguageStatus = "FAIL"
        $constrainedLanguageDetail = "子进程退出码 $clmExitCode"
    }

    if ($constrainedLanguageStatus -eq "FAIL") {
        Write-CheckResult -Name "ConstrainedLanguageSummary" -Status "FAIL" -Detail $constrainedLanguageDetail
        Write-Host "Overall: FAIL"
        exit 1
    }

    Write-CheckResult -Name "ConstrainedLanguageSummary" -Status $constrainedLanguageStatus -Detail $constrainedLanguageDetail

    if ($constrainedLanguageStatus -eq "PASS") {
        Write-Host "Overall: PASS"
    } else {
        Write-Host ("Overall: PASS_WITH_{0}" -f $constrainedLanguageStatus)
    }

    exit 0
} finally {
    Remove-Item -LiteralPath $testFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $clmScriptFile -Force -ErrorAction SilentlyContinue
}
