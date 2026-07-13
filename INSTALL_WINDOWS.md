# Installing RadioTop on Windows 10/11

There are three ways to get RadioTop running on Windows, from easiest to most manual:

| Method | Best for |
|---|---|
| [A. Prebuilt `.exe`](#a-prebuilt-exe-easiest) | Just want to run it, no Python installed |
| [B. `install_windows.ps1` script](#b-automated-install-script) | Running from source, but don't want to manage Python/venv by hand |
| [C. Manual install](#c-manual-install) | Developers / full control |

All three require **Windows 10 (version 2004+) or Windows 11**. No admin rights are needed for any of them (everything installs to your user profile).

---

## A. Prebuilt `.exe` (easiest)

If a built release is available (see the repo's [Releases](../../releases) page), this is the simplest option:

1. Download `RadioTop.exe`.
2. Double-click it to run — no installer, no Python, nothing else to set up.
3. **First launch:** Windows may show a SmartScreen warning ("Windows protected your PC") because the executable isn't code-signed. Click **More info → Run anyway**. This is expected for small unsigned open-source tools; it's not a sign anything is wrong.
4. **Also on first launch:** Windows Defender Firewall may prompt for permission the first time you play a station — RadioTop opens a small local-only proxy server on `127.0.0.1` (to send a proper User-Agent header to the stream) that never listens on your network, only on your own machine. Allow it.

To build this `.exe` yourself instead of downloading one, see [Building the .exe](#building-the-exe-optional) below.

---

## B. Automated install script

This runs RadioTop from source, but automates the Python/dependency setup and creates Start Menu (and optionally Desktop) shortcuts that launch it like a normal app, with no console window.

1. Make sure you have the project folder locally (`git clone`, or download & extract the ZIP from GitHub), including `radiotop_gui.py`, the `assets/` folder, and `install_windows.ps1`.
2. Open **PowerShell** (Start menu → type "PowerShell") and `cd` into that folder:
   ```powershell
   cd C:\path\to\radiotop
   ```
3. Run the install script:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install_windows.ps1
   ```
   Add `-Desktop` if you also want a Desktop shortcut:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Desktop
   ```

   > **Why `-ExecutionPolicy Bypass`?** Windows blocks running downloaded `.ps1` scripts by default. This flag allows *this one script, this one time* to run, without permanently changing your system's script policy.

What the script does:
- Checks for Python 3.9+ (offers to install it via `winget` if it's missing and `winget` is available; otherwise points you to python.org)
- Creates a private virtual environment in `.venv` next to the script, so RadioTop's dependency (PySide6) doesn't touch anything else on your system
- Installs PySide6 into that environment
- Creates a **Start Menu** shortcut (search "RadioTop" to find it), and a **Desktop** shortcut if you passed `-Desktop`

To remove everything the script created:
```powershell
powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Uninstall
```
This deletes the `.venv` folder and the shortcuts. It does **not** delete `radiotop_gui.py`/the project folder, and does not touch your saved stations or API keys (see [Uninstalling completely](#uninstalling-completely) below).

---

## C. Manual install

For full control, or if you'd rather not run the script:

```powershell
git clone https://github.com/example/radiotop.git
cd radiotop
py -3 -m venv .venv
.venv\Scripts\pip install PySide6
.venv\Scripts\python radiotop_gui.py
```

To launch without a console window (recommended for everyday use), use `pythonw.exe` instead of `python.exe`:
```powershell
.venv\Scripts\pythonw radiotop_gui.py
```

You can make your own shortcut to that command via Explorer: right-click the Desktop → **New → Shortcut** → target:
```
C:\path\to\radiotop\.venv\Scripts\pythonw.exe "C:\path\to\radiotop\radiotop_gui.py"
```
Then right-click the new shortcut → **Properties → Change Icon...** and point it at `assets\radiotop.ico` if you'd like RadioTop's icon instead of the generic Python one.

---

## Building the `.exe` (optional)

If you want a standalone executable (for method A, or just to run RadioTop without keeping Python installed):

```powershell
cd radiotop
py -3 -m venv .venv
.venv\Scripts\pip install PySide6 pyinstaller
.venv\Scripts\pyinstaller radiotop.spec
```

The finished executable is written to `dist\RadioTop.exe`. It bundles Python, PySide6, and the app icon into a single file — copy it anywhere and run it, no install needed on the target machine. See `radiotop.spec` for details on what it bundles.

---

## Requirements recap

- Windows 10 (2004+) or Windows 11
- **Method A:** nothing else — the `.exe` is self-contained
- **Methods B & C:** Python 3.9+ (the script can install this for you via `winget`)
- No separate multimedia backend to install — Qt Multimedia uses the built-in Windows Media Foundation backend

## Where your data lives

RadioTop stores custom stations, volume, output device, the notifications toggle, and any Last.fm/Discogs keys via `QSettings`, which on Windows means the Registry:
```
HKEY_CURRENT_USER\Software\radiotop\RadioTop
```
There's no separate config file to hunt down or back up manually — though you can export that Registry key with `regedit` if you want a backup.

## Uninstalling completely

1. Run `install_windows.ps1 -Uninstall` (method B) to remove the `.venv` and shortcuts, or just delete the project folder / `RadioTop.exe` (methods A/C).
2. To also clear your saved stations and API keys, delete the Registry key above:
   ```powershell
   Remove-Item -Path 'HKCU:\Software\radiotop' -Recurse
   ```
   (Optional — most people can skip this step.)

## Troubleshooting

| Problem | Fix |
|---|---|
| "Windows protected your PC" (SmartScreen) | Click **More info → Run anyway**. The executable is unsigned but not malicious. |
| Firewall prompt on first play | Safe to allow — it's RadioTop's own local-only (`127.0.0.1`) stream proxy. |
| `install_windows.ps1` won't run / "scripts disabled" error | Use `powershell -ExecutionPolicy Bypass -File install_windows.ps1` rather than double-clicking the `.ps1` file. |
| No audio backend / playback errors | Check that Windows' Media Feature Pack is present (removed only on some "N" editions of Windows) — install it from [Microsoft's site](https://www.microsoft.com/en-us/software-download/mediafeaturepack). |
| `winget` not found when the script offers to install Python | Install Python manually from [python.org/downloads/windows](https://www.python.org/downloads/windows/) — check **"Add python.exe to PATH"** during setup — then re-run the install script. |
| Shortcut launches nothing / errors immediately | Re-run `install_windows.ps1` (not `-Uninstall`) to recreate the virtual environment; make sure `radiotop_gui.py` and `assets\` weren't moved after installing. |
