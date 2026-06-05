<#
.SYNOPSIS
    Sandbox test: Verify Start-NormalAppVisible / Start-ProcWait arg construction
    with empty argument list in PowerShell 5.1
#>

$ErrorActionPreference = "Stop"

Write-Host "=== PowerShell Version ==="
Write-Host $PSVersionTable.PSVersion

Write-Host ""
Write-Host "=== Test 1: Empty @() piped to ForEach-Object ==="
$argList = @()
$argsArr = @($argList | ForEach-Object {
    if ($_ -match ' ') { '"{0}"' -f $_ } else { $_ }
})
$argString = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
Write-Host "argsArr.Count = $($argsArr.Count)"
Write-Host "argString type = $($argString.GetType().Name)"
Write-Host "argString value = [$argString]"
Write-Host "argString is null? $($null -eq $argString)"
if ($null -eq $argString) {
    Write-Host "FAIL: argString is null"
    exit 1
} else {
    Write-Host "PASS"
}

Write-Host ""
Write-Host "=== Test 2: Start-Process with conditional ArgumentList (splatting) ==="
# This is the exact pattern used in the fix
$startArgs = @{
    FilePath = "cmd.exe"
    WorkingDirectory = $env:TEMP
    WindowStyle = 'Hidden'
}
try {
    $p = Start-Process @startArgs -PassThru
    Start-Sleep -Milliseconds 500
    $p.Kill()
    Write-Host "PASS: Start-Process succeeded (process started and killed)"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host ""
Write-Host "=== Test 2b: Start-Process with non-empty arguments (splatting) ==="
$argList2 = @('/c', 'echo ok')
$argsArr2 = @($argList2 | ForEach-Object {
    if ($_ -match ' ') { '"{0}"' -f $_ } else { $_ }
})
$argString2 = $argsArr2 -join ' '
$startArgs2 = @{
    FilePath = "cmd.exe"
    WorkingDirectory = $env:TEMP
    WindowStyle = 'Hidden'
    ArgumentList = $argString2
}
try {
    $p = Start-Process @startArgs2 -Wait -PassThru
    Write-Host "PASS: Start-Process with args exited with code $($p.ExitCode)"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host ""
Write-Host "=== Test 3: Non-empty arg list ==="
$argList = @('--self-update-verify', '--expected-sha256', 'abc123')
$argsArr = @($argList | ForEach-Object {
    if ($_ -match ' ') { '"{0}"' -f $_ } else { $_ }
})
$argString = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
Write-Host "argString value = [$argString]"
Write-Host "argString is null? $($null -eq $argString)"
if ($argString -eq '--self-update-verify --expected-sha256 abc123') {
    Write-Host "PASS"
} else {
    Write-Host "FAIL: unexpected value"
    exit 1
}

Write-Host ""
Write-Host "=== Test 4: Args with spaces ==="
$argList = @('--path', 'C:\Program Files\test', '--flag')
$argsArr = @($argList | ForEach-Object {
    if ($_ -match ' ') { '"{0}"' -f $_ } else { $_ }
})
$argString = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
Write-Host "argString value = [$argString]"
Write-Host "expected       = [--path `"C:\Program Files\test`" --flag]"
if ($argString -eq '--path "C:\Program Files\test" --flag') {
    Write-Host "PASS"
} else {
    Write-Host "FAIL: quoting incorrect"
    exit 1
}

Write-Host ""
Write-Host "=== Test 5: ProcessStartInfo.Arguments empty ==="
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cmd.exe"
$psi.UseShellExecute = $false
$argList = @()
$argsArr = @($argList | ForEach-Object {
    if ($_ -match ' ') { '"{0}"' -f $_ } else { $_ }
})
$psi.Arguments = if ($argsArr.Count -gt 0) { $argsArr -join ' ' } else { '' }
Write-Host "psi.Arguments type = $($psi.Arguments.GetType().Name)"
Write-Host "psi.Arguments value = [$($psi.Arguments)]"
Write-Host "psi.Arguments is null? $($null -eq $psi.Arguments)"
if ($null -eq $psi.Arguments) {
    Write-Host "FAIL: psi.Arguments is null"
    exit 1
} else {
    Write-Host "PASS"
}

Write-Host ""
Write-Host "=== All tests passed ==="

# ── Quote-Arg tests ──
Write-Host ""
Write-Host "=== Test 6: Quote-Arg ==="
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

$tests = @(
    @{ input = "simple"; expected = "simple" },
    @{ input = "C:\Program Files\test"; expected = '"C:\Program Files\test"' },
    @{ input = '--path'; expected = '--path' },
    @{ input = 'arg with "quote"'; expected = '"arg with \"quote\""' },
    @{ input = $null; expected = '""' },
    @{ input = ""; expected = '""' }
)

foreach ($t in $tests) {
    $result = Quote-Arg $t.input
    if ($result -eq $t.expected) {
        Write-Host "PASS: input=[$($t.input)] -> [$result]"
    } else {
        Write-Host "FAIL: input=[$($t.input)] expected=[$($t.expected)] got=[$result]"
        exit 1
    }
}

Write-Host ""
Write-Host "=== Test 7: Empty args via Quote-Arg → splatting ==="
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
    Write-Host "PASS: Start-Process with empty Quote-Arg args succeeded"
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 1
}

Write-Host ""
Write-Host "=== All tests passed ==="
