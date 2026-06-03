# 测试修复后的 Write-IniValue 正则
$content = "[State]`r`nstate = idle`r`nlast_error = `r`n`r`n[Files]`r`ntarget = C:\\test.exe`r`n`r`n[Version]`r`nnew_sha256 = abc`r`n`r`n[Retry]`r`nretry_count = 0`r`nmax_retry = 3`r`n"

$section = "State"
$key = "state"
$value = "verified"

$sectionEsc = [regex]::Escape("[$section]")
$keyEsc = [regex]::Escape("$key")
$pattern = "(?ms)($sectionEsc(?:(?!^\[).)*$keyEsc\s*=\s*).*?(\s*$)"

Write-Host "Before:"
Write-Host $content
Write-Host "---"

$newContent = $content -replace $pattern, "`${1}$value`${2}"
Write-Host "After:"
Write-Host $newContent
Write-Host "---"

# 验证：state=verified，不是 stateverified
if ($newContent -match 'state\s*=\s*verified') { Write-Host "PASS: state = verified" } else { Write-Host "FAIL" }
# 验证其他区未被破坏
if ($newContent -match 'target\s*=\s*C:') { Write-Host "PASS: target preserved" } else { Write-Host "FAIL" }
