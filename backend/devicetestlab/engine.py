"""Independent test definitions and a full-suite runner with inspectable evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from time import perf_counter
from typing import Any

from .device import VirtualDevice

SUITE_VERSION = "1.0.0"
COMMAND_COST_MS = {"STATUS": 1, "RESET": 8, "WRITE": 4, "READ": 5, "UNKNOWN": 1}
SCENARIOS = [
    {
        "id": "healthy",
        "label": "Healthy device",
        "description": "All documented commands behave correctly.",
    },
    {
        "id": "corrupt_read",
        "label": "Corrupted read",
        "description": "One seeded address flips its lowest bit on every valid read.",
    },
    {
        "id": "ignored_write",
        "label": "Ignored write",
        "description": "One seeded address acknowledges writes without storing the value.",
    },
    {
        "id": "stuck_state",
        "label": "Stuck reset state",
        "description": "RESET clears data but leaves the device busy instead of ready.",
    },
    {
        "id": "timeout",
        "label": "Response timeout",
        "description": "Valid reads exceed the modeled 50 ms transport deadline.",
    },
]


def _step(op: str, expected: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    return {"command": {"op": op, **arguments}, "expected": expected}


def _write(address: int, value: int) -> dict[str, Any]:
    return _step("WRITE", {"ok": True}, address=address, value=value)


def _read(address: int, value: int) -> dict[str, Any]:
    return _step("READ", {"ok": True, "value": value}, address=address)


def _status(state: str) -> dict[str, Any]:
    return _step("STATUS", {"ok": True, "state": state, "capacity": 16})


def _pattern(values: list[int]) -> list[dict[str, Any]]:
    return [_write(i, value) for i, value in enumerate(values)] + [
        _read(i, value) for i, value in enumerate(values)
    ]


_DEFINITIONS = [
    (
        "initial_status",
        "Ready on power-up",
        "A fresh device reports ready and 16 addressable bytes.",
        "state",
        [_status("ready")],
    ),
    (
        "single_byte",
        "Single-byte round trip",
        "Write and read a known nonzero byte at address 3.",
        "integrity",
        [_write(3, 90), _read(3, 90)],
    ),
    (
        "overwrite",
        "Overwrite retained value",
        "A second write replaces a previous value at the same address.",
        "integrity",
        [_write(3, 17), _write(3, 230), _read(3, 230)],
    ),
    (
        "zero_pattern",
        "All-zero pattern",
        "Every byte returns zero after explicitly writing the zero pattern.",
        "pattern",
        _pattern([0] * 16),
    ),
    (
        "ones_pattern",
        "All-one pattern",
        "Every byte returns 255 after writing all bits high.",
        "pattern",
        _pattern([255] * 16),
    ),
    (
        "alternating_pattern",
        "Alternating bit pattern",
        "Adjacent bytes alternate 0xAA and 0x55 without corruption.",
        "pattern",
        _pattern([170, 85] * 8),
    ),
    (
        "address_isolation",
        "Address independence",
        "Distinct nonzero values remain attached to their own addresses.",
        "integrity",
        _pattern([i + 1 for i in range(16)]),
    ),
    (
        "boundary_addresses",
        "Boundary addresses",
        "The first and last valid addresses can be written independently.",
        "boundary",
        [_write(0, 49), _write(15, 197), _read(0, 49), _read(15, 197)],
    ),
    (
        "reset_clears",
        "Reset clears stored data",
        "RESET returns every written location to zero.",
        "reset",
        [_write(i, 109) for i in range(16)]
        + [_step("RESET", {"ok": True})]
        + [_read(i, 0) for i in range(16)],
    ),
    (
        "reset_state",
        "Reset returns ready",
        "Writing marks the device dirty and RESET restores the ready state.",
        "state",
        [_write(2, 40), _status("dirty"), _step("RESET", {"ok": True}), _status("ready")],
    ),
    (
        "invalid_read",
        "Reject invalid reads",
        "Negative, out-of-range and noninteger addresses are rejected.",
        "validation",
        [
            _step("READ", {"ok": False, "error": "INVALID_ADDRESS"}, address=a)
            for a in (-1, 16, "0", True)
        ],
    ),
    (
        "invalid_write",
        "Reject invalid writes",
        "Out-of-range addresses and invalid byte values are rejected without changing state.",
        "validation",
        [_step("WRITE", {"ok": False, "error": "INVALID_ADDRESS"}, address=16, value=1)]
        + [
            _step("WRITE", {"ok": False, "error": "INVALID_VALUE"}, address=0, value=v)
            for v in (-1, 256, "1", True)
        ]
        + [_status("ready")],
    ),
    (
        "unsupported_command",
        "Reject unknown command",
        "An unsupported command returns an explicit error without changing state.",
        "validation",
        [_step("ERASE", {"ok": False, "error": "UNSUPPORTED_COMMAND"}), _status("ready")],
    ),
    (
        "repeat_reset",
        "Reset is repeatable",
        "Two resets both acknowledge and finish in the ready state.",
        "reset",
        [_step("RESET", {"ok": True}), _step("RESET", {"ok": True}), _status("ready")],
    ),
]

TESTS: list[dict[str, Any]] = [
    {
        "id": identifier,
        "name": name,
        "description": description,
        "category": category,
        "modeled_cost_ms": sum(
            COMMAND_COST_MS.get(step["command"]["op"], COMMAND_COST_MS["UNKNOWN"]) for step in steps
        ),
    }
    for identifier, name, description, category, steps in _DEFINITIONS
]
_BY_ID: dict[str, dict[str, Any]] = {test["id"]: test for test in TESTS}
_STEPS = {definition[0]: definition[4] for definition in _DEFINITIONS}


def test_definition_snapshot() -> dict[str, Any]:
    """Store the executed commands and oracle responses, not just a mutable filename."""
    payload = {
        "suite_version": SUITE_VERSION,
        "tests": TESTS,
        "steps": _STEPS,
        "cost_policy": {
            "command_ms": COMMAND_COST_MS,
            "timeout": "A timed-out command costs its modeled transport deadline.",
            "deadline_ms": VirtualDevice.DEADLINE_MS,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {**json.loads(canonical), "sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def execute_test(test_id: str, scenario: str, seed: int) -> dict[str, Any]:
    """Execute against a fresh device; assertions do not inspect injected-fault settings."""
    if test_id not in _BY_ID:
        raise ValueError(f"Unknown test: {test_id}")
    if scenario not in {item["id"] for item in SCENARIOS}:
        raise ValueError(f"Unknown scenario: {scenario}")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2147483647:
        raise ValueError("Seed must be an integer from 0 through 2147483647.")
    return execute_steps(_BY_ID[test_id], _STEPS[test_id], scenario, seed)


def execute_steps(
    test: dict[str, Any], steps: list[dict[str, Any]], scenario: str, seed: int
) -> dict[str, Any]:
    """Execute validated declarative steps using the same independent device and oracle."""
    test_id = test["id"]
    started = perf_counter()
    trace: list[dict[str, Any]] = []
    modeled_cost = 0
    error = None
    try:
        device = VirtualDevice(scenario, seed)
        for step in steps:
            response = device.command(dict(step["command"]))
            modeled_cost += (
                response["deadline_ms"]
                if response.get("error") == "TIMEOUT"
                else COMMAND_COST_MS.get(step["command"]["op"], COMMAND_COST_MS["UNKNOWN"])
            )
            trace.append(
                {
                    "command": dict(step["command"]),
                    "response": response,
                    "expected": dict(step["expected"]),
                    "ok": response == step["expected"],
                }
            )
    except Exception as exc:
        # A runner exception is not evidence of a DUT assertion failure.
        error = f"{type(exc).__name__}: {exc}"
    mismatches = [item for item in trace if not item["ok"]]
    status = "error" if error else "fail" if mismatches else "pass"
    decisive = mismatches[0] if mismatches else trace[-1] if trace else None
    return {
        "id": test_id,
        "name": test["name"],
        "status": status,
        "duration_ms": round((perf_counter() - started) * 1000, 6),
        "modeled_cost_ms": modeled_cost,
        "expected": decisive["expected"] if decisive else None,
        "actual": decisive["response"] if decisive else None,
        "message": error
        or (
            f"{len(mismatches)} of {len(trace)} command checks failed."
            if mismatches
            else f"All {len(trace)} command checks passed."
        ),
        "trace": trace,
    }


def run_suite(
    scenario: str,
    seed: int,
    order: list[str] | None = None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Execute each test exactly once, including tests after an earlier failure."""
    selected = list(_BY_ID) if order is None else list(order)
    if (
        len(selected) != len(_BY_ID)
        or len(set(selected)) != len(selected)
        or set(selected) != set(_BY_ID)
    ):
        raise ValueError("Test order must contain every catalog test exactly once.")
    results = []
    for identifier in selected:
        result = execute_test(identifier, scenario, seed)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
