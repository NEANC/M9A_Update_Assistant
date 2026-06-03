import tempfile, shutil, sys
from pathlib import Path
import configparser

t = Path(tempfile.mkdtemp())
ini = t / 'update_state.ini'

# PS 5.1 BOM
ini.write_bytes(b'\xef\xbb\xbf[State]\nstate = verified\n\n[Files]\ntarget = C:\\t.exe\n\n[Version]\nnew_sha256=abc\n\n[Retry]\nretry_count=0\nmax_retry=3\n')

# 模拟 load() 的 BOM 剔除逻辑
content = ini.read_text(encoding='utf-8')
print(f"has BOM: {content[0] == chr(0xfeff)}")
if content.startswith('\ufeff'):
    content = content[1:]
    print("BOM stripped")

cfg = configparser.ConfigParser(strict=False)
cfg.read_string(content)
print(f"state={cfg.get('State','state')}")
print(f"sha={cfg.get('Version','new_sha256')}")
assert cfg.get('State','state') == 'verified'
assert cfg.get('Version','new_sha256') == 'abc'
print("OK")

shutil.rmtree(t)
