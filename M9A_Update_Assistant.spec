# -*- mode: python ; coding: utf-8 -*-

block_cipher = None


a = Analysis(['M9A_Update_Assistant.py'],
             pathex=[],
             binaries=[],
             datas=[],
             hiddenimports=['requests', 'socks'],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='M9A_Update_Assistant',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None)

# 添加自动生成配置文件的逻辑
# 当打包后的可执行文件运行时，如果缺少 config.ini，会自动生成默认配置