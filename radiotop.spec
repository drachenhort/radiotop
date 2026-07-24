# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for RadioTop.

Produces a single-file, windowed (no console) executable that bundles the
app icon so both the taskbar/window icon and Explorer's file icon match.

Usage (from the project root, in a virtualenv with PySide6 + PyInstaller):

    pip install pyinstaller
    pyinstaller radiotop.spec

The finished executable is written to dist/RadioTop.exe on Windows, or
dist/RadioTop on Linux. On macOS, the EXE above is additionally wrapped in
a BUNDLE below, producing dist/RadioTop.app - a real double-clickable app
bundle with a Dock icon, rather than a bare Unix binary.

Notes:
- PySide6's PyInstaller hooks (bundled with the pyside6 package) take
  care of pulling in the Qt plugins RadioTop needs (platform, styles,
  multimedia/Windows Media Foundation or AVFoundation backend)
  automatically - no manual --hidden-import / --collect-all flags should
  be needed.
- assets/radiotop.png and assets/radiotop_about_logo.png are bundled as
  data so the app can still find and use them at runtime via the
  _resource_path() helper in radiotop_gui.py, even when running from the
  frozen executable.
- The macOS BUNDLE's icon is assets/radiotop.icns, generated at build time
  by .github/workflows/build-macos.yml (not checked into the repo, since
  it's a derived binary asset like radiotop.ico's multi-resolution set).
  If it's missing (e.g. building locally without having run that
  generation step), the bundle is still produced, just without a custom
  Dock icon.
- The resulting RadioTop.app is not code-signed or notarized, so a fresh
  download will be Gatekeeper-blocked on other people's Macs until they
  right-click -> Open once (or run `xattr -cr RadioTop.app`).
"""

import os
import sys

a = Analysis(
    ['radiotop_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/radiotop.png', 'assets'), ('assets/radiotop_about_logo.png', 'assets')],
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

if sys.platform == 'darwin':
    icns_path = 'assets/radiotop.icns'
    app = BUNDLE(
        exe,
        name='RadioTop.app',
        icon=icns_path if os.path.exists(icns_path) else None,
        bundle_identifier='com.radiotop.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSUIElement': False,  # show a Dock icon and app switcher entry, not just a menu-bar icon
        },
    )
