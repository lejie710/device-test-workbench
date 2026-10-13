"""Browser request validation; clients cannot choose code or filesystem paths."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .plans import TestPlan

Scenario = Literal["healthy", "corrupt_read", "ignored_write", "stuck_state", "timeout"]
Strategy = Literal["default", "recent_first", "ml"]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario = "healthy"
    strategy: Strategy = "default"
    seed: Annotated[StrictInt, Field(ge=0, le=2147483647)] = 42
    plan: TestPlan | None = None


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scenarios: list[Scenario] = Field(min_length=1, max_length=5)
    seeds: list[Annotated[StrictInt, Field(ge=0, le=2147483647)]] = Field(
        min_length=1, max_length=25
    )
    plan: TestPlan | None = None
