# Contributing to Device Test Lab

Keep each change focused enough to explain and verify. Start from the current `main`, reproduce the relevant behavior and read the [engineering notes](docs/ENGINEERING_NOTES.md) before changing a contract or persistence behavior.

## Branches and commits

Use `<type>/<short-description>` for branches and `type: imperative action` for commit subjects. Supported types are `feat`, `fix`, `docs`, `refactor`, `test` and `chore`. Examples are `fix/campaign-resume` and `fix: preserve completed campaign attempts`. An optional scope may identify the affected component.

Keep one coherent change with its relevant documentation and tests in each commit. A commit body should explain a reason or tradeoff when the subject is insufficient. Complete applicable checks and review the diff before committing.

## Validation

Prepare the environment using the [README setup instructions](README.md#start-on-windows). Run `scripts/check.ps1` for Python lint, formatting, type checks, backend tests and the frontend production build. Use `scripts/check.ps1 -Browser` for changes affecting visible behavior or API integration.

The browser checks use installed Google Chrome by default. To use Microsoft Edge, set `$env:DTL_BROWSER_CHANNEL = 'msedge'`. They run headlessly with isolated temporary runtimes. Documentation-only changes need content and link review; validate changed executable instructions as well.

When PowerShell script execution is blocked, run these commands directly from the project folder:

```powershell
.\.venv\Scripts\python.exe -m ruff check backend tests scripts
.\.venv\Scripts\python.exe -m ruff format --check backend tests scripts
.\.venv\Scripts\python.exe -m mypy backend
[System.IO.Directory]::CreateDirectory((Join-Path (Get-Location) '.local')) | Out-Null
$previousRuntime = $env:DTL_RUNTIME_DIR
try {
    $env:DTL_RUNTIME_DIR = Join-Path (Get-Location) '.local/gate-module-runtime'
    .\.venv\Scripts\python.exe -m pytest --basetemp=.local/pytest-gate
} finally {
    $env:DTL_RUNTIME_DIR = $previousRuntime
}
pnpm --dir frontend run build
.\.venv\Scripts\python.exe scripts/run_bounded.py --timeout 240 -- node scripts/browser_check.cjs
.\.venv\Scripts\python.exe scripts/run_bounded.py --timeout 240 -- node scripts/browser_upgrade_check.cjs
.\.venv\Scripts\python.exe scripts/run_bounded.py --timeout 240 -- node scripts/browser_accessibility_check.cjs
.\.venv\Scripts\python.exe scripts/run_bounded.py --timeout 240 -- node scripts/browser_campaign_comparison_check.cjs
```

Stop and fix a failed check before proceeding. The last four commands are the browser checks; they require an installed supported browser. Review relevant rendered states as well as the automated checks.

The [GitHub Actions workflow](.github/workflows/ci.yml) prepares a Windows environment and runs the same application and browser gate on pull requests, pushes to `main` and manual dispatch. It uses read-only repository permissions and a 20-minute job timeout. Check the actual workflow result on the pull request; a local pass does not establish a result on a hosted runner.

## Pull requests

Use the [PR template](.github/pull_request_template.md) to describe what changed, why it matters and which checks actually ran. State material limitations and relevant checks not run. A previous release's verification record does not establish that a new change passes.

Include a small synthetic reproduction for a behavior change. For interface changes, include a useful screenshot when it explains the result. For ordering changes, report the fixed comparison protocol and observed results, including a loss to the simple history rule.

Keep credentials, personal data, real lab artifacts, local runtime records and generated caches out of the diff. Use original synthetic fixtures that can be shared and reproduced. The general AI-assistance acknowledgement belongs in README.md; automatic AI co-author trailers are not required.
