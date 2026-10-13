"""Bounded declarative test plans: validated data, never executable code."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from .engine import COMMAND_COST_MS, execute_steps, test_definition_snapshot


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["READ", "WRITE", "RESET", "STATUS"]
    address: Annotated[StrictInt, Field(ge=-1, le=16)] | None = None
    value: Annotated[StrictInt, Field(ge=-1, le=256)] | None = None

    @model_validator(mode="after")
    def check_arguments(self):
        if self.op in {"READ", "WRITE"} and self.address is None:
            raise ValueError("READ and WRITE require an integer address.")
        if self.op == "WRITE" and self.value is None:
            raise ValueError("WRITE requires an integer value.")
        if self.op != "WRITE" and self.value is not None:
            raise ValueError("Only WRITE accepts a value.")
        if self.op in {"RESET", "STATUS"} and self.address is not None:
            raise ValueError("RESET and STATUS do not accept an address.")
        return self


class ExpectedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: StrictBool
    value: Annotated[StrictInt, Field(ge=0, le=255)] | None = None
    state: Literal["ready", "dirty", "busy"] | None = None
    capacity: Annotated[StrictInt, Field(ge=1, le=16)] | None = None
    error: Literal["INVALID_ADDRESS", "INVALID_VALUE", "UNSUPPORTED_COMMAND", "TIMEOUT"] | None = (
        None
    )
    deadline_ms: Annotated[StrictInt, Field(ge=1, le=1000)] | None = None
    modeled_response_ms: Annotated[StrictInt, Field(ge=1, le=1000)] | None = None

    @model_validator(mode="after")
    def check_response(self):
        if self.ok and self.error is not None:
            raise ValueError("A successful expected response cannot contain an error.")
        if not self.ok and self.error is None:
            raise ValueError("A failed expected response requires an error code.")
        return self


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Command
    expected: ExpectedResponse


class PlanTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    name: str = Field(min_length=1, max_length=80)
    steps: list[PlanStep] = Field(min_length=1, max_length=64)


class TestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    name: str = Field(min_length=1, max_length=80)
    tests: list[PlanTest] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def check_limits(self):
        if any(re.fullmatch(r"[a-z][a-z0-9_]{0,47}", test.id) is None for test in self.tests):
            raise ValueError(
                "Test IDs must contain lowercase letters, digits and underscores only."
            )
        if len({test.id for test in self.tests}) != len(self.tests):
            raise ValueError("Test IDs must be unique within a plan.")
        if sum(len(test.steps) for test in self.tests) > 256:
            raise ValueError("A plan may contain at most 256 command steps in total.")
        for name in [self.name, *(test.name for test in self.tests)]:
            if not name.strip() or any(ord(character) < 32 for character in name):
                raise ValueError("Names must contain visible text without control characters.")
        if len(self.model_dump_json().encode("utf-8")) > 96_000:
            raise ValueError("A plan may contain at most 96,000 UTF-8 bytes.")
        return self


def engine_fingerprint() -> str:
    directory = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("device.py", "engine.py", "plans.py"):
        digest.update(name.encode())
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()


def plan_snapshot(plan: TestPlan | None) -> dict[str, Any]:
    if plan is None:
        standard = test_definition_snapshot()
        return {
            "kind": "standard",
            "plan": None,
            "name": "Standard catalog",
            "sha256": standard["sha256"],
            "test_count": len(standard["tests"]),
            "step_count": sum(len(steps) for steps in standard["steps"].values()),
            "definition_snapshot": standard,
        }
    normalized = plan.model_dump(exclude_none=True)
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return {
        "kind": "custom",
        "plan": normalized,
        "name": plan.name,
        "sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        "test_count": len(plan.tests),
        "step_count": sum(len(test.steps) for test in plan.tests),
    }


def custom_definition(snapshot: dict[str, Any]) -> dict[str, Any]:
    tests = snapshot["plan"]["tests"]
    return {
        "suite_version": "plan-1.0",
        "sha256": snapshot["sha256"],
        "tests": [
            {
                "id": test["id"],
                "name": test["name"],
                "description": "Custom declared test",
                "modeled_cost_ms": sum(
                    COMMAND_COST_MS[step["command"]["op"]] for step in test["steps"]
                ),
            }
            for test in tests
        ],
        "steps": {test["id"]: test["steps"] for test in tests},
    }


def run_plan(snapshot: dict[str, Any], scenario: str, seed: int, on_result=None) -> list[dict]:
    definition = custom_definition(snapshot)
    results = []
    for test in definition["tests"]:
        result = execute_steps(test, definition["steps"][test["id"]], scenario, seed)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
