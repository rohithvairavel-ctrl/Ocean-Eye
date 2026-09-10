param([switch]$Production)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Run setup.ps1 first to create the local Python environment.' }
if ($Production) { & $projectPython scripts/launch.py --production } else { & $projectPython scripts/launch.py }
