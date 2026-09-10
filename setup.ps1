param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .venv/Scripts/python.exe)) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 or pass -Python with the full path to python.exe.' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
& ./.venv/Scripts/python.exe scripts/create_demo_case.py
if ($LASTEXITCODE -ne 0) { throw 'Demo generation failed.' }
& ./.venv/Scripts/python.exe scripts/load_local_geography.py
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Host 'Ready. Run .\start.ps1 and open http://127.0.0.1:5173'
