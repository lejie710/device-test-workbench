# Verification record

This record separates previously observed application checks from verification of this distribution. Passing simulated-device tests and intentional device FAIL observations are different: the application's tests must pass while a selected fault scenario can correctly produce failing assertions.

## Clean-checkout validation follow-up

The [first hosted run](https://github.com/lejie710/device-test-workbench/actions/runs/38402819639) on 13 October 2026 installed dependencies successfully, then reported 78 passing tests and 60 setup errors. The ignored project-root `.local` directory was absent. Pytest creates `pytest-gate` for `--basetemp=.local/pytest-gate`, but requires its parent to exist. Earlier local tooling preparation had created that parent and masked the missing prerequisite.

The shared check script now creates only the `.local` parent before pytest, and the direct commands in CONTRIBUTING include the same preparation. The workflow asserts that `.local` is absent before invoking the script, so future hosted runs continue to exercise a clean start.

Verification used a disposable checkout with its own locked dependencies, ordinary tools on PATH and no root `.local` or local tool configuration. The original script reproduced the same 78 passes and 60 setup errors. From that unchanged absent-parent state, the corrected full gate passed all 138 Python tests, lint, formatting, type checks, the production build and all 37 browser groups. The documented pytest block then passed all 138 tests from a second absent-parent start. Wrapper logs were kept outside the checkout so they could not create the missing directory. The upstream Starlette warning remains unchanged.

These are local regression results. A successful hosted run of the corrected workflow is still pending publication of the fix.

## Version 0.3.1 distribution checks

The 0.3.1 distribution was installed and verified in a fresh Windows checkout on 13 October 2026, using its own Python environment and frontend dependencies prepared from the included lockfiles. The checks below exercised that checkout's application code and rebuilt frontend.

| Check | Observed result |
|---|---|
| Python dependency installation and consistency | All 35 locked application and verification packages installed; `pip check` passed |
| Complete application Python suite | 138 passed in 31 seconds |
| Python lint and formatting | Passed for 26 Python files |
| Backend type checking | Passed for 13 source modules |
| Frontend TypeScript and production build | Passed; 1,592 modules transformed in 5.35 seconds |
| Core browser workflow | 10 groups passed |
| Campaign and saved-run comparison browser workflow | 8 groups passed |
| Accessibility and state browser workflow | 8 groups passed |
| Campaign comparison browser workflow | 11 groups passed |
| Windows launcher | `Start.cmd` served the application; `/api/health` reported version 0.3.1, the root returned HTTP 200, the catalog contained 14 tests and isolated run history started empty |
| Launcher process cleanup | The bounded launcher check and cleanup of its owned process tree passed |
| JavaScript dependency audit | Zero reported vulnerabilities at every severity across production and development dependencies |
| Python dependency advisory lookup | OSV queries for all 35 pinned packages returned zero affected packages; the recorded lockfile hash matches `requirements.lock` |
| Screenshot review | All 30 screenshots checked for unintended identifying content and valid PNG structure; no text metadata or trailing data found |

All 37 browser groups passed against the real API and rebuilt production frontend. The four browser records linked below contain their executed checks and timestamps. The Python suite emitted one upstream Starlette TestClient deprecation warning concerning `httpx`; it did not fail a test. These results describe the checked Windows environment, not every operating system or browser.

The [dependency audit](results/dependency-audit.json) records the dated JavaScript and Python advisory results from 13 October 2026. It does not establish the absence of undisclosed vulnerabilities or vulnerabilities in application code.

`PUBLIC_EXPORT_MANIFEST.json` is the initial 0.3.1 file inventory of 114 application, documentation, configuration and evidence files; future development takes place directly in this repository.

At the initial local handoff, GitHub Actions had not yet run. The clean-checkout follow-up above records its first result and the subsequent local correction. Earlier release sections below remain historical records.

## Version 0.3.0 application checks

Campaign comparison was verified on Windows on 13 October 2026. All 138 application tests passed within the complete development test run. Type checks passed for 13 backend modules, and the production frontend build passed with 1,592 modules transformed in 4.64 seconds.

The browser JSON records describe the latest executed checks. Earlier release summaries below are retained as historical context.

All 37 browser groups passed against the final integrated application: 10 core workflows, 8 campaigns/run comparison, 8 accessibility/state checks and 11 campaign comparison checks. The [campaign comparison record](results/browser-campaign-comparison-check.json) distinguishes actual simulator executions from controlled transport responses used to exercise delayed selections and unavailable-API feedback. No device outcomes were fabricated for the browser demonstrations.

The new checks include actual standard and custom campaigns, reordered conditions, changed definitions, precise denominators, saved latest-attempt validation, incomplete/missing evidence, exact displayed JSON export and real interruption/resume. Old saved-run JSON remained deeply equal after resumption. Comparison does not change the execution engine, storage schema or ML experiment.

The package-store setting is now consistent between setup and later builds. An offline frozen-lockfile refresh reused installed dependencies before the documented build passed. Dependency versions were unchanged. These are application checks; a fresh standalone 0.3.0 distribution and hosted GitHub Actions have not yet been verified.

## Version 0.2.1 distribution checks

The assembled distribution was installed and checked in a separate Windows folder on 13 October 2026. Its Python environment and frontend dependencies were prepared from the included lockfiles. The launcher loaded application code from that folder and began with empty run history.

| Check | Observed result |
|---|---|
| Complete application Python suite | 106 passed |
| Python lint and formatting | Passed for 24 Python files |
| Backend type checking | Passed for 12 source modules |
| Frontend TypeScript and production build | Passed |
| Original browser workflow | 10 groups passed |
| Campaign and comparison browser workflow | 8 groups passed |
| Accessibility and state browser workflow | 8 groups passed |
| Python dependency consistency | `pip check` passed with the 35 locked application and verification dependencies |
| Windows launcher | `Start.cmd` served the application, `/api/health` reported version 0.2.1, the root returned HTTP 200 and the catalog contained 14 tests |
| JavaScript dependency audit | Zero reported vulnerabilities at every severity in production and development dependencies |

All 26 browser groups passed against the candidate's real API and rebuilt production frontend after the final contrast adjustment. The three browser records below contain their check lists and execution timestamps. The production build transformed 1,590 modules and completed in 4.24 seconds on the verification host.

The Python suite retains the upstream Starlette TestClient deprecation warning. Verification targets this Windows configuration; other operating systems have not been verified. The dependency audit is dated 13 October 2026 and cannot establish the absence of vulnerabilities disclosed later.

## Previously observed application checks

The following checks were executed on Windows on 13 October 2026 against application version 0.2.0.

| Check | Observed result |
|---|---|
| Complete Python suite | 106 passed in 25.12 seconds |
| Python lint and formatting | Passed |
| Backend type checking | Passed for 12 source modules |
| Frontend TypeScript and production build | Passed |
| Original browser workflow | 10 groups passed |
| Campaign and comparison browser workflow | 8 groups passed |
| Existing SQLite evidence | Two earlier runs and one experiment remained byte-for-byte unchanged after opening under 0.2.0 |
| Three-seed robustness artifacts | All 23 nested artifact hashes and four source hashes verified |

The Python suite emitted one upstream Starlette TestClient deprecation warning. The warning did not fail a test. These results describe the recorded environment and revision; they do not imply coverage of every browser, operating system or dependency version.

## Behavior covered

Backend coverage includes strict plan validation, independent custom-plan assertions, shared-worker exclusion, pause after the current run, repeated resume rejection, explicit startup recovery, preserved retry attempts, immutable completed cases, engine fingerprint compatibility, the 50-attempt bound and atomic run/campaign linking. Injected persistence failures check that an unsuccessful campaign update does not leave an orphan run. Migration tests preserve older saved records.

The [original browser record](results/browser-check.json) covers empty state, healthy and faulty execution, explicit untrained-model fallback, evidence export, history, actual model training, trained full-suite execution, mobile layout, unavailable API feedback and restart persistence.

The [campaign and comparison record](results/browser-upgrade-check.json) covers invalid plans, JSON import and preview, a complete custom campaign, export, attempt inspection, condition warnings, expected/actual evidence, 375- and 320-pixel layouts, and an actual 25-case pause/resume sequence without repeating completed attempts.

The [accessibility and state record](results/browser-accessibility-check.json) covers keyboard focus, associated input validation, delayed-response selection, long identifiers at narrow widths, reduced motion, control contrast and worker state across views. These targeted checks are not a claim of complete accessibility conformance.

## Process and runtime isolation

Runtime ownership tests reject a second server before it can recover another live instance's records. They verify completion by the original owner, release after failure and release after a process crash.

The bounded command helper assigns a Windows command tree to a private Job Object before execution. Tests cover deadline refusal, exit-code propagation, assignment failure, and cleanup of the target and its child while a separate process remains alive. The POSIX implementation has not been executed on this Windows verification host.

## Experiment evidence

The [original report](results/experiment.json) records 210 cycles, 2,940 rows and a 120/30/60 chronological split. Each method executes 840 final-evaluation tests with equivalent observations. The simple history rule wins on mean modeled first-failure cost.

The [three-seed report](results/robustness-v1.1.json) retains every fixed cohort and per-regime result. All three seed comparisons preserve equivalent outcomes and complete work across methods. The rule wins all three overall discovery means. The [artifact manifest](results/robustness-v1.1-manifest.json) records the generated study artifacts and application source hashes. Raw evidence is reproducible using the protocol's command; the copied report does not include every generated runtime artifact.

The [dependency audit](results/dependency-audit.json) is a dated observation, not a guarantee about vulnerabilities disclosed later. No physical-device performance or production benefit was measured.

## Prepared continuous integration

The [GitHub Actions workflow](../.github/workflows/ci.yml) has been prepared for a Windows runner with Python 3.12, Node.js 24 and pnpm 11.25.0. It installs locked dependencies, checks their consistency and runs the application gate including the browser workflows. It grants read-only repository access, uses pinned action commits and bounds the job to 20 minutes.

The first hosted execution failed during temporary-directory preparation, as recorded above. The corrected workflow still requires a new hosted execution; local verification does not establish that result.
