# Resolve ordinary PATH tools, with an optional ignored local configuration.
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$toolConfigPath = Join-Path $script:ProjectRoot '.local\tooling.json'
$script:LocalTools = if (Test-Path -LiteralPath $toolConfigPath) {
    [System.IO.File]::ReadAllText($toolConfigPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
} else { $null }
function Find-ProjectTool([string]$Name) {
    if ($script:LocalTools -and $script:LocalTools.$Name -and (Test-Path -LiteralPath $script:LocalTools.$Name)) {
        return $script:LocalTools.$Name
    }
    $candidate = Get-Command $Name -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Source }
    throw "Required tool '$Name' is unavailable. Install the documented prerequisite or configure .local/tooling.json."
}
function Confirm-LastExit([string]$Operation) {
    if ($LASTEXITCODE -ne 0) { throw "$Operation failed with exit code $LASTEXITCODE." }
}
