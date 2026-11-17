# PenguinTerm uninstaller for Windows 10/11 (no arguments needed)
#
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
#
# Honors the same overrides as the installer:
#   $env:PENGUINTERM_PORT = '8443'
#   $env:PENGUINTERM_PREFIX = "$env:LOCALAPPDATA\PenguinTerm"
$ErrorActionPreference = 'Continue'

$Port   = if ($env:PENGUINTERM_PORT) { [int]$env:PENGUINTERM_PORT } else { 8443 }
$Prefix = if ($env:PENGUINTERM_PREFIX) { $env:PENGUINTERM_PREFIX } else { "$env:LOCALAPPDATA\PenguinTerm" }
$TaskName = 'PenguinTerm'

Write-Host "[penguinterm] Stopping and removing scheduled task '$TaskName'..."
schtasks.exe /End /TN $TaskName *>&1 | Out-Null
schtasks.exe /Delete /TN $TaskName /F *>&1 | Out-Null

Write-Host "[penguinterm] Removing firewall rule 'PenguinTerm (TCP $Port)'..."
netsh advfirewall firewall delete rule name="PenguinTerm (TCP $Port)" *>&1 | Out-Null

if (Test-Path $Prefix) {
    Write-Host "[penguinterm] Deleting $Prefix ..."
    Remove-Item -Recurse -Force $Prefix
}

Write-Host "[penguinterm] Uninstall complete. Configuration, database and virtual environment were removed."
Write-Host "[penguinterm] Your shell and user profile were not modified."

