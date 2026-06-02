"""Debug: 直接测试 update.ps1 的最小化版本"""
import subprocess, tempfile, shutil
from pathlib import Path

tmpdir = Path(tempfile.mkdtemp())

# 创建并写最小化测试脚本
test_ps1 = tmpdir / 'test.ps1'
# 用 backslash 路径
target = str(tmpdir / 't.exe')
new_file = str(tmpdir / 't.new.exe')
backup = str(tmpdir / 't.back.exe')

# 创建测试文件
Path(target).write_text('old')
Path(new_file).write_text('new')

# 最小化的 PS1 测试
test_ps1.write_text(f'''$ErrorActionPreference = "Stop"
$target = "{target}"
$newFile = "{new_file}"
$backup = "{backup}"

Write-Host "target exists: $(Test-Path $target)"
Write-Host "newFile exists: $(Test-Path $newFile)"

try {{
    if (!(Test-Path -LiteralPath $newFile)) {{
        throw "new file not found"
    }}
    if (Test-Path -LiteralPath $backup) {{
        Remove-Item -LiteralPath $backup -Force
    }}
    if (Test-Path -LiteralPath $target) {{
        Move-Item -LiteralPath $target -Destination $backup -Force
    }}
    Move-Item -LiteralPath $newFile -Destination $target -Force
    Write-Host "SUCCESS"
    exit 0
}} catch {{
    Write-Error $_.Exception.Message
    exit 1
}}
''', encoding='utf-8')

r = subprocess.run(['powershell', '-NoP', '-Ep', 'Bypass', '-File', str(test_ps1)],
    capture_output=True, text=True, cwd=str(tmpdir), timeout=10)

print(f"exit={r.returncode}")
for l in r.stdout.splitlines(): print(f"  OUT: {l}")
for l in r.stderr.splitlines(): print(f"  ERR: {l[:200]}")
print(f"target: {Path(target).read_text() if Path(target).exists() else 'X'}")
print(f"backup: {Path(backup).read_text() if Path(backup).exists() else 'X'}")
print(f"new: {'OK' if Path(new_file).exists() else 'gone'}")

shutil.rmtree(tmpdir, ignore_errors=True)
