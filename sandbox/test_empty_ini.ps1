# Test: two-step Read-IniValue approach
$content = @"
[Version]
old_sha256 =
new_sha256 =

[Retry]
retry_count = 0
"@

$section = "Version"
$key = "new_sha256"
$sectionEsc = [regex]::Escape("[$section]")
$keyEsc = [regex]::Escape($key)

# Step 1: Extract section content
$sectionPattern = "(?ms)^$sectionEsc\s*\r?\n(.*?)(?=^\s*\[|\z)"
if ($content -match $sectionPattern) {
    $sectionContent = $matches[1]
    Write-Host "Section content: [$sectionContent]"
    
    # Step 2: Find key within section
    $keyPattern = "(?m)^$keyEsc\s*=\s*(.*?)[\t ]*$"
    if ($sectionContent -match $keyPattern) {
        $val = $matches[1]
        Write-Host "Read-IniValue returned: [$val]"
        Write-Host "Length: $($val.Length)"
        Write-Host "if(`$val) is: $(if ($val) { 'TRUE' } else { 'FALSE' })"
    } else {
        Write-Host "KEY NOT FOUND in section"
    }
} else {
    Write-Host "SECTION NOT FOUND"
}
