<#
.SYNOPSIS
    Installs or uninstalls RadioTop on Windows 10/11 by running it from
    source in a private virtual environment, with Start Menu / Desktop
    shortcuts that launch it without a console window.

.DESCRIPTION
    - Verifies Python 3.9+ is available (offers to install it via winget
      if it's missing and winget is present).
    - Creates a private virtual environment in .venv next to this script,
      so RadioTop's dependency (PySide6) never touches your system/other
      Python environments.
    - Installs PySide6 into that virtual environment.
    - Creates a Start Menu shortcut (and, optionally, a Desktop shortcut)
      that launch RadioTop via pythonw.exe, so no console window appears.

.PARAMETER Desktop
    Also create a Desktop shortcut in addition to the Start Menu one.

.PARAMETER Uninstall
    Remove the virtual environment and any shortcuts created by this
    script. Does not touch radiotop_gui.py or your saved stations/settings
    (those live in the Registry under HKCU\Software\radiotop\RadioTop and
    are left alone; remove them yourself via `regedit` if you want a full
    wipe).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Desktop

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [switch]$Desktop,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

# Resolve paths relative to this script, not the caller's working directory.
$RepoRoot     = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath   = Join-Path $RepoRoot 'radiotop_gui.py'
$VenvDir      = Join-Path $RepoRoot '.venv'
$PythonExe    = Join-Path $VenvDir 'Scripts\python.exe'
$PythonwExe   = Join-Path $VenvDir 'Scripts\pythonw.exe'
$IconPath     = Join-Path $RepoRoot 'assets\radiotop.ico'
$StartMenuDir = [Environment]::GetFolderPath('Programs')
$DesktopDir   = [Environment]::GetFolderPath('Desktop')
$ShortcutName = 'RadioTop.lnk'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function New-AppShortcut($DirPath) {
    $shell = New-Object -ComObject WScript.Shell
    $path = Join-Path $DirPath $ShortcutName
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath = $PythonwExe
    $sc.Arguments = '"' + $ScriptPath + '"'
    $sc.WorkingDirectory = $RepoRoot
    if (Test-Path $IconPath) {
        $sc.IconLocation = $IconPath
    }
    $sc.Description = 'RadioTop - internet radio player'
    $sc.Save()
    return $path
}

# ----------------------------------------------------------------- Uninstall
if ($Uninstall) {
    Write-Step 'Uninstalling RadioTop'

    foreach ($dir in @($StartMenuDir, $DesktopDir)) {
        $path = Join-Path $dir $ShortcutName
        if (Test-Path $path) {
            Remove-Item $path -Force
            Write-Ok "Removed shortcut: $path"
        }
    }

    if (Test-Path $VenvDir) {
        Remove-Item $VenvDir -Recurse -Force
        Write-Ok "Removed virtual environment: $VenvDir"
    }

    Write-Host ''
    Write-Ok 'Uninstall complete.'
    Write-Warn2 'Your saved stations/settings (Registry: HKCU\Software\radiotop\RadioTop) were left in place.'
    Write-Warn2 'radiotop_gui.py and this install script were left in place - delete the folder yourself if you want them gone too.'
    exit 0
}

# ------------------------------------------------------------------- Install
Write-Step 'Checking for radiotop_gui.py'
if (-not (Test-Path $ScriptPath)) {
    Write-Error "radiotop_gui.py not found next to this script (expected: $ScriptPath). Run this script from the RadioTop project folder."
}
Write-Ok "Found $ScriptPath"

Write-Step 'Checking for Python 3.9+'
$pythonCmd = $null
foreach ($candidate in @('py -3', 'python', 'python3')) {
    $parts = $candidate -split ' '
    $exeName = $parts[0]
    $exeArgs = @()
    if ($parts.Length -gt 1) { $exeArgs = $parts[1..($parts.Length - 1)] }
    try {
        $verOutput = & $exeName @($exeArgs + '--version') 2>&1
        if ($LASTEXITCODE -eq 0 -and $verOutput -match '(\d+)\.(\d+)\.(\d+)') {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 9)) {
                $pythonCmd = $candidate
                Write-Ok "Found $verOutput (via '$candidate')"
                break
            } else {
                Write-Warn2 "Found $verOutput via '$candidate', but RadioTop needs 3.9+ - skipping"
            }
        }
    } catch {
        # candidate not on PATH - try the next one
    }
}

if (-not $pythonCmd) {
    Write-Warn2 'No suitable Python installation found.'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = Read-Host 'Install Python 3.12 now via winget? [Y/n]'
        if ($answer -eq '' -or $answer -match '^[Yy]') {
            Write-Step 'Installing Python via winget (this opens its own progress UI)'
            winget install -e --id Python.Python.3.12
            Write-Warn2 'Python was just installed - close this window and re-run install_windows.ps1 in a NEW PowerShell window so PATH changes take effect.'
            exit 0
        }
    }
    Write-Error "Please install Python 3.9+ from https://www.python.org/downloads/windows/ (check 'Add python.exe to PATH' during setup), then re-run this script."
}

$cmdParts = $pythonCmd -split ' '
$pyExe = $cmdParts[0]
$pyExeArgs = @()
if ($cmdParts.Length -gt 1) { $pyExeArgs = $cmdParts[1..($cmdParts.Length - 1)] }

Write-Step 'Creating virtual environment (.venv)'
if (Test-Path $VenvDir) {
    Write-Ok '.venv already exists - reusing it'
} else {
    & $pyExe @($pyExeArgs + @('-m', 'venv', $VenvDir))
    Write-Ok "Created $VenvDir"
}

Write-Step 'Installing PySide6 (this can take a minute)'
& $PythonExe '-m' 'pip' 'install' '--upgrade' 'pip' | Out-Null
& $PythonExe '-m' 'pip' 'install' 'PySide6'
Write-Ok 'PySide6 installed'

Write-Step 'Creating Start Menu shortcut'
$smPath = New-AppShortcut $StartMenuDir
Write-Ok "Created $smPath"

if ($Desktop) {
    Write-Step 'Creating Desktop shortcut'
    $dtPath = New-AppShortcut $DesktopDir
    Write-Ok "Created $dtPath"
}

Write-Host ''
Write-Ok 'RadioTop is installed.'
Write-Host '    Launch it from the Start Menu (search "RadioTop"), or run:' -ForegroundColor White
Write-Host "    `"$PythonwExe`" `"$ScriptPath`"" -ForegroundColor White
Write-Host ''
Write-Warn2 'First launch may trigger a one-time Windows Defender Firewall prompt (RadioTop opens a local-only proxy on 127.0.0.1 for stream playback) - allowing it is safe.'
