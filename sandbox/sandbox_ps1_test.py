"""沙箱测试：验证 Helper.ps1 + Update.ps1 完整流程"""
import os, shutil, sys, subprocess, tempfile, importlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import modules.self_updater
importlib.reload(modules.self_updater)
from modules.self_updater import SelfUpdater

tmpdir = Path(tempfile.mkdtemp())
print(f"[SANDBOX] Test dir: {tmpdir}")

# 1. Generate scripts
SelfUpdater._generate_helper_ps1(tmpdir)
SelfUpdater._generate_update_ps1(tmpdir)
helper_path = tmpdir / 'M9A_Update_Assistant_Update_Helper.ps1'
update_path = tmpdir / 'M9A_Update_Assistant_Update.ps1'
print(f"[SANDBOX] Generated: helper={helper_path.stat().st_size}B, update={update_path.stat().st_size}B")

# 2. Verify script encoding (should be utf-8-sig = BOM)
for name, path in [('helper', helper_path), ('update', update_path)]:
    with open(path, 'rb') as f:
        head = f.read(3)
    has_bom = head == b'\xef\xbb\xbf'
    print(f"[SANDBOX] {name}.ps1 has BOM: {has_bom}")
    assert has_bom, f"{name}.ps1 missing BOM!"

# 3. Create test files with backslash paths (real Windows)
target_exe = tmpdir / 'MyApp.exe'
new_exe = tmpdir / 'MyApp.new.exe'
backup_exe = tmpdir / 'MyApp.backup.exe'
target_exe.write_text('old_content')
new_exe.write_text('new_content')

print(f"[SANDBOX] Test exe: target={target_exe.exists()}, new={new_exe.exists()}")
# Leave new_sha256 empty to skip Get-FileHash (not available in sandbox)
print(f"[SANDBOX] Skipping SHA256 validation (sandbox limitation)")

# 4. Create update_state.ini with backslash Windows paths
ini = tmpdir / 'update_state.ini'
ini_content = f"""[State]
state = idle
last_error =
current_step =
message =
progress =
updated_at =

[Files]
target = {target_exe}
new_file = {new_exe}
backup_file = {backup_exe}

[Version]
old_version = v1.14.0
new_version = v1.14.1
old_sha256 =
new_sha256 =

[Retry]
retry_count = 0
max_retry = 3
"""
ini.write_text(ini_content, encoding='utf-8')
print(f"[SANDBOX] INI created: {ini.stat().st_size}B")

# ─── TEST 1: Run update.ps1 standalone ───
print("\n" + "="*60)
print("[TEST 1] Running update.ps1 standalone...")
lock_file = tmpdir / 'update_started.lock'
log_file = tmpdir / 'update.log'
if lock_file.exists(): lock_file.unlink()
if log_file.exists(): log_file.unlink()

r = subprocess.run(
    ['powershell', '-NoP', '-Ep', 'Bypass', '-File', str(update_path)],
    cwd=str(tmpdir), capture_output=True, text=True, timeout=15
)
print(f"  exit={r.returncode}")
for l in r.stdout.splitlines():
    m = l.strip()
    if m: print(f"  stdout: {m}")
for l in r.stderr.splitlines()[:3]:
    m2 = l.strip()
    if m2: print(f"  stderr: {m2[:150]}")

# Verify file movements
t_ok = target_exe.exists()
b_ok = backup_exe.exists()
n_gone = not new_exe.exists()
print(f"  files: target={'OK' if t_ok else 'MISSING'}, backup={'OK' if b_ok else 'MISSING'}, new={'gone' if n_gone else 'still exists'}")
assert t_ok, "FAIL: target should exist after update.ps1"
assert b_ok, "FAIL: backup should exist"
assert n_gone, "FAIL: new_file should be moved"

# Verify content
assert target_exe.read_text() == 'new_content', "FAIL: target has wrong content!"
assert backup_exe.read_text() == 'old_content', "FAIL: backup has wrong content!"
print("  [PASS] File replacement correct")

# Verify INI state was updated by Set-UpdateStatus
ini2 = ini.read_text(encoding='utf-8')
print(f"  INI has 'replacing': {'replacing' in ini2}")
print(f"  INI has 'replace_done': {'replace_done' in ini2}")
assert 'replace_done' in ini2, "FAIL: Set-UpdateStatus not writing state!"

# Verify log file
if log_file.exists():
    log_text = log_file.read_text(encoding='utf-8-sig' if log_file.read_bytes()[:3] == b'\xef\xbb\xbf' else 'utf-8')
    lines = log_text.strip().splitlines()
    print(f"  log lines: {len(lines)}")
    for l in lines[-3:]:
        print(f"    LOG: {l[:150]}")
    # Check format: scriptTag > timestamp | LEVEL | message
    assert 'Update.ps1 ->' in log_text, "FAIL: log missing script tag!"
    assert '| INFO |' in log_text or '| ERROR |' in log_text, "FAIL: log missing level!"
    print("  [PASS] Log format correct")
else:
    print("  [WARN] No log file created")

print("  [PASS] Test 1: update.ps1 OK")

# ─── TEST 2: Verify helper.ps1 lock file + basic execution ───
print("\n" + "="*60)
print("[TEST 2] Helper.ps1 lock file + logging (ParentPid=0)...")

# Reset for a clean test
if lock_file.exists(): lock_file.unlink()
if log_file.exists(): log_file.unlink()

r = subprocess.run(
    ['powershell', '-NoP', '-Ep', 'Bypass', '-File', str(helper_path), '-ParentPid', '0'],
    cwd=str(tmpdir), capture_output=True, text=True, timeout=30
)
l_ok = lock_file.exists()
print(f"  exit={r.returncode}, lock={'OK' if l_ok else 'X'}")
for l in r.stdout.splitlines():
    m3 = l.strip()
    if m3: print(f"  stdout: {m3}")

assert l_ok, "FAIL: lock file not created!"

# Verify helper log
if log_file.exists():
    log_raw = log_file.read_bytes()
    encoding = 'utf-8-sig' if log_raw[:3] == b'\xef\xbb\xbf' else 'utf-8'
    log_text = log_file.read_text(encoding=encoding)
    print(f"  log lines: {len(log_text.strip().splitlines())}")
    for l in log_text.strip().splitlines()[:4]:
        print(f"    LOG: {l[:150]}")
    assert 'Helper.ps1 ->' in log_text
    print("  [PASS] Helper lock + log OK")

print(f"\n[SANDBOX] ALL TESTS PASSED")
shutil.rmtree(tmpdir, ignore_errors=True)
