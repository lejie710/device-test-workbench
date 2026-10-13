"""Bounded sequential campaigns with explicit pause, retry and immutable snapshots."""

from __future__ import annotations

import json
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import Response

from .plans import TestPlan, engine_fingerprint, plan_snapshot
from .schemas import CampaignRequest, RunRequest
from .storage import utc_now


def register_campaign_routes(application, storage, worker, gate, make_run, perform_run):
    lock = RLock()

    def get(identifier):
        value = storage.get_campaign(identifier)
        if value is None:
            raise HTTPException(404, "Campaign not found.")
        return value

    def execute(identifier):
        try:
            while True:
                with lock:
                    campaign = get(identifier)
                    remaining = [case for case in campaign["cases"] if case["state"] != "completed"]
                    if not remaining:
                        campaign.update(
                            state="completed",
                            finished_at=utc_now(),
                            error=None,
                            pause_requested=False,
                        )
                        storage.save_campaign(campaign)
                        return
                    if campaign["pause_requested"]:
                        campaign.update(state="paused", pause_requested=False)
                        storage.save_campaign(campaign)
                        return
                    if sum(len(case["attempts"]) for case in campaign["cases"]) >= 50:
                        campaign.update(
                            state="error",
                            error="Campaign attempt limit reached. Create a new campaign.",
                        )
                        storage.save_campaign(campaign)
                        return
                    case = remaining[0]
                    raw_plan = campaign["plan_snapshot"]["plan"]
                    request = RunRequest(
                        scenario=case["scenario"],
                        seed=case["seed"],
                        strategy="default",
                        plan=TestPlan.model_validate(raw_plan) if raw_plan else None,
                    )
                    run = make_run(request)
                    run.update(
                        campaign_id=identifier,
                        campaign_case_id=case["id"],
                        attempt_number=len(case["attempts"]) + 1,
                    )
                    case.update(state="running", run_id=run["id"])
                    case["attempts"].append(run["id"])
                    campaign.update(state="running", error=None)
                    storage.save_campaign_attempt(run, campaign)
                perform_run(run, release_gate=False)
                with lock:
                    campaign = get(identifier)
                    case = next(c for c in campaign["cases"] if c["id"] == run["campaign_case_id"])
                    saved = storage.get_run(run["id"])
                    case["state"] = "completed" if saved["state"] == "completed" else "error"
                    campaign["completed"] = sum(
                        c["state"] == "completed" for c in campaign["cases"]
                    )
                    if case["state"] == "error":
                        campaign.update(state="error", error=saved.get("error") or "Run failed.")
                        storage.save_campaign(campaign)
                        return
                    storage.save_campaign(campaign)
        except Exception as exc:
            with lock:
                campaign = get(identifier)
                if campaign["state"] != "completed":
                    campaign.update(state="error", error=f"{type(exc).__name__}: {exc}")
                    storage.save_campaign(campaign)
        finally:
            gate.release()

    @application.post("/api/plans/validate")
    def validate(plan: TestPlan):
        return plan_snapshot(plan)

    @application.get("/api/campaigns")
    def list_campaigns():
        return {"campaigns": storage.list_campaigns()}

    @application.post("/api/campaigns", status_code=202)
    def create(request: CampaignRequest):
        if len(set(request.scenarios)) != len(request.scenarios) or len(set(request.seeds)) != len(
            request.seeds
        ):
            raise HTTPException(422, "Scenarios and seeds must each be unique.")
        if len(request.scenarios) * len(request.seeds) > 25:
            raise HTTPException(422, "A campaign is limited to 25 scenario/seed cases.")
        if not request.name.strip():
            raise HTTPException(422, "Campaign name must contain visible text.")
        with lock:
            if not gate.acquire(blocking=False):
                raise HTTPException(409, "A run, campaign or experiment is active.")
            try:
                cases: list[dict[str, Any]] = [
                    {
                        "id": str(uuid4()),
                        "scenario": scenario,
                        "seed": seed,
                        "state": "pending",
                        "run_id": None,
                        "attempts": [],
                    }
                    for scenario in request.scenarios
                    for seed in request.seeds
                ]
                campaign = {
                    "id": str(uuid4()),
                    "name": request.name,
                    "state": "queued",
                    "created_at": utc_now(),
                    "finished_at": None,
                    "plan_snapshot": plan_snapshot(request.plan),
                    "engine_fingerprint": engine_fingerprint(),
                    "cases": cases,
                    "completed": 0,
                    "total": len(cases),
                    "error": None,
                    "pause_requested": False,
                }
                storage.save_campaign(campaign)
                response = json.loads(json.dumps(campaign))
                worker.submit(execute, campaign["id"])
            except Exception:
                gate.release()
                raise
        return response

    @application.get("/api/campaigns/{identifier}")
    def detail(identifier: str):
        return get(identifier)

    @application.post("/api/campaigns/{identifier}/pause")
    def pause(identifier: str):
        with lock:
            campaign = get(identifier)
            if campaign["state"] == "paused":
                return campaign
            if campaign["state"] not in {"queued", "running", "pausing"}:
                raise HTTPException(409, "Only an active campaign can be paused.")
            campaign.update(state="pausing", pause_requested=True)
            storage.save_campaign(campaign)
            return campaign

    @application.post("/api/campaigns/{identifier}/resume", status_code=202)
    def resume(identifier: str):
        with lock:
            campaign = get(identifier)
            if campaign["state"] not in {"paused", "interrupted", "error"}:
                raise HTTPException(
                    409, "Only paused, interrupted or errored campaigns can resume."
                )
            if campaign["engine_fingerprint"] != engine_fingerprint():
                raise HTTPException(
                    409, "Engine changed since this campaign began. Create a new campaign."
                )
            if (
                campaign["plan_snapshot"]["kind"] == "standard"
                and campaign["plan_snapshot"]["sha256"] != plan_snapshot(None)["sha256"]
            ):
                raise HTTPException(409, "Standard definitions changed. Create a new campaign.")
            if sum(len(case["attempts"]) for case in campaign["cases"]) >= 50:
                raise HTTPException(409, "Campaign attempt limit reached. Create a new campaign.")
            if not gate.acquire(blocking=False):
                raise HTTPException(409, "A run, campaign or experiment is active.")
            try:
                campaign.update(state="queued", pause_requested=False, error=None, finished_at=None)
                storage.save_campaign(campaign)
                response = json.loads(json.dumps(campaign))
                worker.submit(execute, identifier)
            except Exception:
                gate.release()
                raise
            return response

    @application.get("/api/campaigns/{identifier}/export")
    def export(identifier: str):
        campaign = get(identifier)
        payload = {
            "campaign": campaign,
            "runs": [
                storage.get_run(run_id) for case in campaign["cases"] for run_id in case["attempts"]
            ],
        }
        return Response(
            json.dumps(payload, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="campaign-{campaign["id"]}.json"'
            },
        )
