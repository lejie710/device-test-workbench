# Comparing two saved runs

Run comparison shows observations and the conditions under which they were collected. It does not assign a cause or apply a universal regression threshold. Select a left run and a right run; every numeric delta is right minus left.

The response contains both saved run summaries, changed conditions, added or removed test identifiers, every common test's original status, and its expected response, actual response and complete command trace. A runner ERROR stays an ERROR. A pending test stays pending. Neither becomes a device PASS or FAIL.

## Comparability

Two runs are comparable only when both contain a complete set of PASS/FAIL observations and their scenario, seed, suite kind, test definitions, engine fingerprint and test membership agree. A different strategy or execution order is disclosed but can still be compared because each test starts with an isolated device state.

If a recorded engine fingerprint is missing, the comparison retains the evidence and explains that implementation equivalence is unverified. Earlier V1 records remain readable and exportable; missing provenance is not silently replaced with the current implementation's fingerprint. Selecting the same run twice also produces a warning because it is not an independent observation.

A healthy run followed by an ignored-write scenario may show five changed test outcomes at seed 3. The scenario change is known and visible. The comparison must not label that pair a production regression or infer a hardware root cause. A changed expectation in a custom plan is similarly different from a changed device response under an unchanged expectation.

## Timing

Laptop wall time includes the runner, model ordering, storage updates and host scheduling. It is contextual. Modeled cost follows the simulator's documented command-cost policy and is shown in a separate field. Incomplete runs contain only observed partial work; pending tests' nominal catalog costs are not counted as executed work. No observed failure produces a null first-failure value and a null first-failure delta when either side lacks one.

The implementation is the pure function `compare_runs` in `backend/devicetestlab/comparison.py`. `GET /api/compare?left=<run-id>&right=<run-id>` reads both records and returns that comparison. Returned data is copied so a consumer cannot mutate an archived record through the comparison object.
