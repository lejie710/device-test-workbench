param([ValidateRange(1024,65535)][int]$Port = 8717)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Run .\scripts\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) { throw 'The frontend is not built. Run .\scripts\setup.ps1 first.' }
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
Write-Host "Device Test Lab: http://127.0.0.1:$Port"
Write-Host 'Press Ctrl+C to stop this server. A second server cannot use the same runtime.'
& $projectPython -m uvicorn devicetestlab.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
