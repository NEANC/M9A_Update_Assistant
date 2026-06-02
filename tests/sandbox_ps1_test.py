"""沙箱测试：强制 reload 模块 + 测试"""
import os, sys, subprocess, tempfile, importlib
from pathlib import Path

# 确保使用最新模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import modules.self_updater
importlib.reload(modules.self_updater)
from modules.self_updater import SelfUpdater

tmpdir = Path(tempfile.mkdtemp())
SelfUpdater._generate_update_ps1(tmpdir)

# 检查生成的脚本
script = (tmpdir / 'M9A_Update_Assistant_Update.ps1').read_text('utf-8')
has_ea = '$ErrorActionPreference' in script
has_gh = 'Get-FileHash' in script
has_if = 'if ($newSha256)' in script
print(f"  Script: ErrorAction={has_ea}, Get-FileHash={has_gh}, if_newSha256={has_if}")

# 找 Read-IniValue 正则
import re
m = re.search(r'\$pattern = .*', script)
if m: print(f"  Regex: {m.group()[:100]}")

# 检查 $ErrorActionPreference 所在行
for i, l in enumerate(script.splitlines(), 1):
    if 'ErrorAction' in l:
        print(f"  Line {i}: {l.strip()}")

# 用 backslash + 回退路径测试 update.ps1
t = str(tmpdir / 't.exe')
n = str(tmpdir / 't.new.exe')
b = str(tmpdir / 't.back.exe')
Path(t).write_text('old')
Path(n).write_text('new')
ini = tmpdir / 'update_state.ini'
ini.write_text(f"""[Files]
target = {t}
new_file = {n}
backup_file = {b}
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
print(f"  exit={r.returncode} stdout={r.stdout.strip()[:200]} stderr={r.stderr.strip()[:200]}")
print(f"  target={Path(t).exists()} backup={Path(b).exists()} new={Path(n).exists()}")

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
