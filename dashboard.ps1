# dashboard.ps1 - Launch the OpenClip Control Dashboard (no terminal window).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\dashboard.ps1

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPyW = Join-Path $PSScriptRoot 'backend\.venv\Scripts\pythonw.exe'
$dashScript = Join-Path $PSScriptRoot 'launcher_dash.py'

if (Test-Path $venvPyW) {
    Start-Process -FilePath $venvPyW -ArgumentList "`"$dashScript`"" -WorkingDirectory $PSScriptRoot
} else {
    Start-Process -FilePath 'pythonw' -ArgumentList "`"$dashScript`"" -WorkingDirectory $PSScriptRoot
}
