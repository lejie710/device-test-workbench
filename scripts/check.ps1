param([switch]$Browser)
. (Join-Path $PSScriptRoot 'tooling.ps1')
Set-Location -LiteralPath $ProjectRoot
$projectPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$nodeTool = Find-ProjectTool 'node'
$packageTool = Find-ProjectTool 'pnpm'
$env:PATH = (Split-Path -Parent $nodeTool) + [System.IO.Path]::PathSeparator + $env:PATH
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
& $projectPython -m ruff check backend tests scripts
Confirm-LastExit 'Python lint'
& $projectPython -m ruff format --check backend tests scripts
Confirm-LastExit 'Python formatting'
& $projectPython -m mypy backend
Confirm-LastExit 'Python type checking'
$priorRuntimeDirectory = $env:DTL_RUNTIME_DIR
# Pytest creates its base directory, but requires the parent to exist.
[System.IO.Directory]::CreateDirectory((Join-Path $ProjectRoot '.local')) | Out-Null
try {
    $env:DTL_RUNTIME_DIR = Join-Path $ProjectRoot '.local/gate-module-runtime'
    & $projectPython -m pytest --basetemp=.local/pytest-gate
    Confirm-LastExit 'Backend and ML tests'
} finally {
    $env:DTL_RUNTIME_DIR = $priorRuntimeDirectory
}
& $packageTool --dir frontend run build
Confirm-LastExit 'Frontend type check and build'
if ($Browser) {
    & $projectPython scripts/run_bounded.py --timeout 240 --record .local/browser-boundary.json -- $nodeTool scripts/browser_check.cjs
    Confirm-LastExit 'Browser integration checks'
    & $projectPython scripts/run_bounded.py --timeout 240 --record .local/browser-upgrade-boundary.json -- $nodeTool scripts/browser_upgrade_check.cjs
    Confirm-LastExit 'Campaign and comparison browser checks'
    & $projectPython scripts/run_bounded.py --timeout 240 --record .local/browser-accessibility-boundary.json -- $nodeTool scripts/browser_accessibility_check.cjs
    Confirm-LastExit 'Keyboard and interaction browser checks'
    & $projectPython scripts/run_bounded.py --timeout 240 --record .local/browser-campaign-comparison-boundary.json -- $nodeTool scripts/browser_campaign_comparison_check.cjs
    Confirm-LastExit 'Campaign comparison browser checks'
}
Write-Host 'All requested checks passed.'
