"""调试 update.ps1 — 打印 Read-IniValue 返回值"""
import sys, subprocess, tempfile, shutil
from pathlib import Path
sys.path.insert(0, '..')
from modules.self_updater import SelfUpdater

tmpdir = Path(tempfile.mkdtemp())
paths = {
    'runtime_dir': tmpdir,
    'state_file': tmpdir / 'update_state.ini',
    'log_file': tmpdir / 'update.log',
    'helper_ps1': tmpdir / 'M9A_Update_Assistant_Update_Helper.ps1',
    'update_ps1': tmpdir / 'M9A_Update_Assistant_Update.ps1',
    'lock_file': tmpdir / 'update_started.lock',
    'new_file': tmpdir / 'test_app.new.exe',
    'backup_file': tmpdir / 'test_app.backup.exe',
}
SelfUpdater._generate_update_ps1(paths)

# 注入调试代码：在 update.ps1 的 try 块开头加 Write-Host
script = (tmpdir / 'M9A_Update_Assistant_Update.ps1').read_text(encoding='utf-8')
# 在 Set-Content $tmp 之后插入调试
script = script.replace(
    'if (!(Test-Path -LiteralPath $newFile))',
    'Write-Host "DEBUG target=[$target]" ; Write-Host "DEBUG newFile=[$newFile]" ; Write-Host "DEBUG backup=[$backup]" ; Write-Host "DEBUG newSha256=[$newSha256]" ; if (!(Test-Path -LiteralPath $newFile))'
)

# 创建测试文件
exe = tmpdir / 'test_app.exe'
exe.write_bytes(b'old')
shutil.copy(str(exe), str(tmpdir / 'test_app.new.exe'))

ini = tmpdir / 'update_state.ini'
ini.write_text(f"""[Files]
target = {tmpdir}\\test_app.exe
new_file = {tmpdir}\\test_app.new.exe
backup_file = {tmpdir}\\test_app.backup.exe
[Version]
new_sha256 = 
[Retry]
retry_count = 0
max_retry = 3
""", encoding='utf-8')

r = subprocess.run(
    ['powershell', '-NoP', '-Ep', 'Bypass', '-File', str(tmpdir / 'M9A_Update_Assistant_Update.ps1')],
    cwd=str(tmpdir), capture_output=True, text=True, timeout=10
)
print(f"exit={r.returncode}")
for line in r.stdout.splitlines()[:10]:
    print(f"  OUT: {line}")
for line in r.stderr.splitlines()[:5]:
    print(f"  ERR: {line[:200]}")

shutil.rmtree(tmpdir, ignore_errors=True)
