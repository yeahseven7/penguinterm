# PenguinTerm installer for Windows 10/11
#
# Usage (no arguments needed â€” just run it):
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
#
# Optional overrides (environment variables, never required):
#   $env:PENGUINTERM_PORT = '9090'   # default 8443
#   $env:PENGUINTERM_PREFIX = 'D:\PenguinTerm'   # default %LOCALAPPDATA%\PenguinTerm
#   $env:PENGUINTERM_NO_STARTUP_TASK = '1'       # don't register the logon task
#   $env:PENGUINTERM_NO_FIREWALL_PROMPT = '1'    # skip the firewall prompt
#
# Uninstall:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
#
# What it does (per-user, no administrator required except for the optional firewall rule):
#   1. Verifies Python 3.12+ (offers to install via winget if missing)
#   2. Creates %LOCALAPPDATA%\PenguinTerm with app\, venv\, config.json, tls\
#   3. Installs Python dependencies into the virtual environment
#   4. Registers a per-user Scheduled Task ("PenguinTerm") that starts the
#      server at logon (disable with $env:PENGUINTERM_NO_STARTUP_TASK = '1')
#   5. Offers to add a Windows Firewall inbound rule for the port (needs admin)
#   6. Writes an uninstall script (%LOCALAPPDATA%\PenguinTerm\uninstall.ps1)
$ErrorActionPreference = 'Stop'

$Port   = if ($env:PENGUINTERM_PORT) { [int]$env:PENGUINTERM_PORT } else { 8443 }
$Prefix = if ($env:PENGUINTERM_PREFIX) { $env:PENGUINTERM_PREFIX } else { "$env:LOCALAPPDATA\PenguinTerm" }

$AppDir   = Join-Path $Prefix 'app'
$VenvDir  = Join-Path $Prefix 'venv'
$Config   = Join-Path $Prefix 'config.json'
$TaskName = 'PenguinTerm'

function Log([string]$msg)  { Write-Host "[penguinterm] $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Warning "[penguinterm] $msg" }

function Find-Python {
    foreach ($candidate in @('python', 'py')) {
        try {
            $v = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $parts = $v.Trim().Split('.')
                if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12)) { return $candidate }
            }
        } catch { }
    }
    return $null
}

Log "Source prefix : $Prefix"
Log "Install prefix: $Prefix"
$NoStartupTask = $env:PENGUINTERM_NO_STARTUP_TASK -eq '1'
$NoFirewall    = $env:PENGUINTERM_NO_FIREWALL_PROMPT -eq '1'
$python = Find-Python
if (-not $python) {
    Warn "Python 3.12+ not found on PATH."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host "Install Python 3.12 via winget now? [Y/n]"
        if ($answer -eq '' -or $answer -match '^[Yy]') {
            winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
            $python = Find-Python
        }
    }
    if (-not $python) { throw "Python 3.12+ is required. Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and re-run." }
}
Log "Python        : $(& $python --version)"

# ------------------------------------------------- files & venv
$SrcDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Log "Source        : $SrcDir"

# Vendored frontend dependencies aren't in git; fetch them (pinned + verified) if missing.
if (-not (Test-Path (Join-Path $SrcDir 'frontend\assets\vendor\xterm.js'))) {
    Log "Fetching vendored frontend dependencies..."
    & (Join-Path $SrcDir 'scripts\fetch-vendor.ps1')
}

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
foreach ($item in @('backend', 'frontend', 'config', 'requirements.txt', 'README.md')) {
    Copy-Item -Recurse -Force (Join-Path $SrcDir $item) (Join-Path $AppDir $item)
}

Log "Creating virtual environmentâ€¦"
& $python -m venv $VenvDir
$venvPython = Join-Path $VenvDir 'Scripts\python.exe'
$venvPythonNoLogo = Join-Path $VenvDir 'Scripts\pythonw.exe'
& $venvPython -m pip install --upgrade pip | Out-Null
Log "Installing dependenciesâ€¦"
& $venvPython -m pip install -r (Join-Path $AppDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

# ------------------------------------------------- configuration
if (-not (Test-Path $Config)) {
    Log "Generating configuration ($Config)â€¦"
    & $venvPython -c @"
import json
with open(r'$AppDir\config\penguinterm.example.json') as fh:
    cfg = json.load(fh)
cfg['port'] = $Port
with open(r'$Config', 'w') as fh:
    json.dump(cfg, fh, indent=2)
"@
} else {
    Log "Keeping existing configuration $Config"
}

# ------------------------------------------------- uninstaller (self-contained copy)
$GeneratedUninstall = @"
# PenguinTerm uninstaller (generated for this install).
# Removes the scheduled task, the firewall rule and the install directory.
`$ErrorActionPreference = 'Continue'
`$TaskName = 'PenguinTerm'
schtasks.exe /End /TN `$TaskName *>&1 | Out-Null
schtasks.exe /Delete /TN `$TaskName /F *>&1 | Out-Null
netsh advfirewall firewall delete rule name="PenguinTerm (TCP $Port)" *>&1 | Out-Null
if (Test-Path '$Prefix') { Remove-Item -Recurse -Force '$Prefix' }
Write-Host 'PenguinTerm uninstalled. Configuration, database and venv were removed.'
"@
Set-Content -Path (Join-Path $Prefix 'uninstall.ps1') -Value $GeneratedUninstall -Encoding UTF8

# ------------------------------------------------- startup task
if (-not $NoStartupTask) {
    $action = "`"$venvPythonNoLogo`" -m backend.main"
    try {
        schtasks.exe /Create /TN $TaskName /TR "$action" /SC ONLOGON /RL LIMITED /F | Out-Null
        # the task needs the data dir set; wrap it in a command instead of relying on env vars
        schtasks.exe /Delete /TN $TaskName /F | Out-Null
        $wrapper = Join-Path $Prefix 'start-penguinterm.cmd'
        Set-Content -Path $wrapper -Value @"
@echo off
set PENGUINTERM_DATA_DIR=$Prefix
cd /d "$AppDir"
start "" /b "$venvPythonNoLogo" -m backend.main
"@ -Encoding ASCII
        schtasks.exe /Create /TN $TaskName /TR "`"$wrapper`"" /SC ONLOGON /RL LIMITED /F | Out-Null
        Log "Scheduled task '$TaskName' registered (starts at logon, limited privileges)"
    } catch {
        Warn "Could not register the logon task: $_"
        Warn "Start manually with: set PENGUINTERM_DATA_DIR=$Prefix && $venvPythonNoLogo -m backend.main (from $AppDir)"
    }
}

# ------------------------------------------------- firewall
if (-not $NoFirewall) {
    $answer = Read-Host "Add a Windows Firewall inbound rule for TCP $Port? (requires admin) [y/N]"
    if ($answer -match '^[Yy]') {
        try {
            netsh advfirewall firewall add rule name="PenguinTerm (TCP $Port)" dir=in action=allow protocol=TCP localport=$Port | Out-Null
            Log "Firewall rule added for inbound TCP $Port"
        } catch {
            Warn "Firewall rule failed (try an elevated PowerShell): $_"
            Warn "Manual command: netsh advfirewall firewall add rule name=`"PenguinTerm (TCP $Port)`" dir=in action=allow protocol=TCP localport=$Port"
        }
    } else {
        Warn "Skipped. Friends cannot reach this server until inbound TCP $Port is allowed."
    }
}

# ------------------------------------------------- done
Write-Host @"

[penguinterm] PenguinTerm installed.

  Web UI : https://<this-machine>:$Port
           (accept the self-signed certificate once, or replace
            $Prefix\tls\cert.pem + key.pem with your own certificate)
  Config : $Config
  Data   : $Prefix
  Start  : launch 'PenguinTerm' from the Start menu task list, or run:
             set PENGUINTERM_DATA_DIR=$Prefix
             cd /d "$AppDir"
             "$venvPythonNoLogo" -m backend.main

Friends connect with: https://<your-host-or-dynamic-dns>:$Port
Port forwarding on your router: forward TCP $Port to this PC.
Cannot forward (CGNAT)? Use Tailscale or ZeroTier â€” see README.

Uninstall: powershell -ExecutionPolicy Bypass -File "$Prefix\uninstall.ps1"
"@

