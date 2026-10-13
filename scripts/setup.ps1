param([switch]$UseSystemCertificates)
. (Join-Path $PSScriptRoot 'tooling.ps1')
Set-Location -LiteralPath $ProjectRoot
$basePython = Find-ProjectTool 'python'
$nodeTool = Find-ProjectTool 'node'
$packageTool = Find-ProjectTool 'pnpm'
$env:PATH = (Split-Path -Parent $nodeTool) + [System.IO.Path]::PathSeparator + $env:PATH
if ($UseSystemCertificates) { $env:NODE_OPTIONS = '--use-system-ca' }
Write-Host 'Preparing isolated Python and frontend dependencies from PyPI and the npm registry.'
& $basePython -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12 or later is required"'
Confirm-LastExit 'Python version check'
$projectPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    & $basePython -m venv '.venv'
    Confirm-LastExit 'Python environment creation'
}
& $projectPython -m pip install --disable-pip-version-check --timeout 120 --index-url https://pypi.org/simple -r requirements.lock
Confirm-LastExit 'Locked Python dependency installation'
& $projectPython -m pip install --disable-pip-version-check --timeout 120 --index-url https://pypi.org/simple --no-deps -e .
Confirm-LastExit 'Project package installation'
& $packageTool install --dir frontend --frozen-lockfile --store-dir .local/pnpm-store
Confirm-LastExit 'Locked frontend dependency installation'
& $packageTool --dir frontend run build
Confirm-LastExit 'Frontend production build'
Write-Host 'Setup complete. Start the app with .\scripts\start.ps1'
