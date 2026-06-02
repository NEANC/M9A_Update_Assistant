$ini = @"
[Files]
target = C:/test/file.exe
new_file = C:/test/new.exe

[Version]
new_version = v1.0
"@

$sectionEsc = [regex]::Escape('[Files]')
$keyEsc = [regex]::Escape('target')
Write-Host "sectionEsc: $sectionEsc"
Write-Host "keyEsc: $keyEsc"

# Test1: current pattern with \Q[\E
$p1 = "(?ms)^$sectionEsc(?:(?!^\Q[\E).)*^$keyEsc\s*=\s*(.*?)\s*$"
Write-Host "p1: $p1"
if ($ini -match $p1) { Write-Host "p1 MATCH: $($matches[1])" } else { Write-Host "p1 NO MATCH" }

# Test2: using \[ instead of \Q[\E
$p2 = "(?ms)^$sectionEsc(?:(?!^\[).)*^$keyEsc\s*=\s*(.*?)\s*$"
Write-Host "p2: $p2"
if ($ini -match $p2) { Write-Host "p2 MATCH: $($matches[1])" } else { Write-Host "p2 NO MATCH" }

# Test3: simple line-by-line within section
$p3 = "(?ms)^$sectionEsc(?:\r?\n)+$keyEsc\s*=\s*(.*?)\s*$"
Write-Host "p3: $p3"
if ($ini -match $p3) { Write-Host "p3 MATCH: $($matches[1])" } else { Write-Host "p3 NO MATCH" }
