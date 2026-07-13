# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for RadioTop.

Produces a single-file, windowed (no console) executable that bundles the
app icon so both the taskbar/window icon and Explorer's file icon match.

Usage (from the project root, in a virtualenv with PySide6 + PyInstaller):

    pip install pyinstaller
    pyinstaller radiotop.spec

The finished executable is written to dist/RadioTop.exe (or dist/RadioTop
on Linux/macOS - this spec works cross-platform, it just only produces a
.ico-branded icon on Windows, since that's the only platform that reads
the `icon=` argument).

Notes:
- PySide6's PyInstaller hooks (bundled with the pyside6 package) take
  care of pulling in the Qt plugins RadioTop needs (platform, styles,
  multimedia/Windows Media Foundation backend) automatically - no manual
  --hidden-import / --collect-all flags should be needed.
- assets/radiotop.png is bundled as data so the app can still find and
  use it at runtime via the _resource_path() helper in radiotop_gui.py,
  even when running from the frozen executable.
"""

a = Analysis(
    ['radiotop_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/radiotop.png', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RadioTop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app - no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/radiotop.ico',
)
