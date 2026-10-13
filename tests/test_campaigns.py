"""Campaign progress, pause/resume, crash recovery and snapshot integrity."""

import copy
import importlib
import json
import sqlite3
import time
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv("DTL_RUNTIME_DIR", str(tmp_path / "module"))
    return importlib.import_module("devicetestlab.app").create_app(
        tmp_path / "runtime", tmp_path / "dist"
    )


def wait_campaign(client, identifier, states, application):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = client.get(f"/api/campaigns/{identifier}").json()
        if value["state"] in states and not application.state.worker_gate.locked():
            return value
        time.sleep(0.01)
    pytest.fail(f"Campaign did not reach {states}: {value}")


def request(plan=None):
    return {
        "name": "Controlled campaign",
        "scenarios": ["healthy", "ignored_write"],
        "seeds": [3],
        "plan": plan,
    }


def test_custom_campaign_runs_exports_and_preserves_plan(application):
    raw = json.loads((Path(__file__).parents[1] / "examples/plans/byte-roundtrip.json").read_text())
    with TestClient(application) as client:
        preview = client.post("/api/plans/validate", json=raw)
        assert preview.status_code == 200
        response = client.post("/api/campaigns", json=request(raw))
        assert response.status_code == 202
        final = wait_campaign(client, response.json()["id"], {"completed"}, application)
        assert final["completed"] == final["total"] == 2
        assert final["plan_snapshot"]["sha256"] == preview.json()["sha256"]
        runs = [client.get(f"/api/runs/{case['run_id']}").json() for case in final["cases"]]
        assert [run["failed"] for run in runs] == [0, 1]
        assert all(run["suite_kind"] == "custom" and run["total"] == 1 for run in runs)
        assert all("not compatible" in run["model_status"] for run in runs)
        export = client.get(f"/api/campaigns/{final['id']}/export").json()
        assert export["campaign"] == final and len(export["runs"]) == 2
        with pytest.raises(ValueError, match="immutable"):
            changed = copy.deepcopy(final)
            changed["plan_snapshot"]["plan"]["name"] = "Edited later"
            application.state.storage.save_campaign(changed)


def test_pause_finishes_current_full_run_then_resume_skips_completed(application, monkeypatch):
    module = importlib.import_module("devicetestlab.app")
    original = module.run_suite
    entered, release = Event(), Event()
    calls = []

    def paused(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_suite", paused)
    with TestClient(application) as client:
        created = client.post("/api/campaigns", json=request()).json()
        identifier = created["id"]
        try:
            assert entered.wait(5)
            assert client.post("/api/runs", json={}).status_code == 409
            assert client.post("/api/experiment").status_code == 409
            assert client.post(f"/api/campaigns/{identifier}/pause").json()["state"] == "pausing"
            assert client.post(f"/api/campaigns/{identifier}/pause").status_code == 200
        finally:
            release.set()
        paused_campaign = wait_campaign(client, identifier, {"paused"}, application)
        assert paused_campaign["completed"] == 1
        assert paused_campaign["cases"][1]["attempts"] == []
        first_id = paused_campaign["cases"][0]["run_id"]
        assert client.get(f"/api/runs/{first_id}").json()["passed"] == 14
        assert client.post(f"/api/campaigns/{identifier}/resume").status_code == 202
        assert client.post(f"/api/campaigns/{identifier}/resume").status_code == 409
        final = wait_campaign(client, identifier, {"completed"}, application)
        assert final["cases"][0] == paused_campaign["cases"][0]
        assert len(calls) == 2


def campaign_fixture(storage, plan=None, count=1):
    from devicetestlab.plans import engine_fingerprint, plan_snapshot

    campaign = {
        "id": "saved-campaign",
        "name": "Saved conditions",
        "state": "running",
        "created_at": "2026-10-13T00:00:00Z",
        "finished_at": None,
        "plan_snapshot": plan_snapshot(plan),
        "engine_fingerprint": engine_fingerprint(),
        "cases": [
            {
                "id": f"case-{index}",
                "scenario": "healthy",
                "seed": 3,
                "state": "pending",
                "run_id": None,
                "attempts": [],
            }
            for index in range(count)
        ],
        "completed": 0,
        "total": count,
        "error": None,
        "pause_requested": False,
    }
    storage.save_campaign(campaign)
    return campaign


def link_attempt(storage, campaign, identifier="old-attempt"):
    case = campaign["cases"][0]
    run = {
        "id": identifier,
        "created_at": "2026-10-13T00:00:01Z",
        "state": "running",
        "campaign_id": campaign["id"],
        "campaign_case_id": case["id"],
        "tests": [],
        "events": [],
    }
    case.update(state="running", run_id=identifier)
    case["attempts"].append(identifier)
    storage.save_campaign_attempt(run, campaign)
    return run


def test_restart_does_not_autoresume_and_retry_creates_new_linked_attempt(application):
    storage = application.state.storage
    campaign = campaign_fixture(storage)
    link_attempt(storage, campaign)
    with TestClient(application) as client:
        restored = client.get(f"/api/campaigns/{campaign['id']}").json()
        assert restored["state"] == "interrupted"
        assert storage.get_run("old-attempt")["state"] == "interrupted"
        assert len(storage.list_runs()) == 1
        assert client.post(f"/api/campaigns/{campaign['id']}/resume").status_code == 202
        final = wait_campaign(client, campaign["id"], {"completed"}, application)
        assert final["cases"][0]["attempts"][0] == "old-attempt"
        assert len(final["cases"][0]["attempts"]) == 2
        assert final["cases"][0]["run_id"] != "old-attempt"
        assert storage.get_run("old-attempt")["state"] == "interrupted"


def test_atomic_link_rolls_back_when_attempt_insert_fails(application):
    storage = application.state.storage
    campaign = campaign_fixture(storage)
    link_attempt(storage, campaign)
    previous = storage.get_campaign(campaign["id"])
    with pytest.raises(sqlite3.IntegrityError):
        link_attempt(storage, campaign)
    assert storage.get_campaign(campaign["id"]) == previous
    assert len(storage.list_runs()) == 1


def test_atomic_link_does_not_leave_orphan_when_campaign_write_fails(application):
    storage = application.state.storage
    campaign = campaign_fixture(storage)
    previous = copy.deepcopy(campaign)
    with storage._connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_campaign_update BEFORE UPDATE ON campaigns "
            "BEGIN SELECT RAISE(ABORT, 'Injected update failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        link_attempt(storage, campaign, "attempt-that-must-rollback")
    assert storage.get_run("attempt-that-must-rollback") is None
    assert storage.get_campaign(campaign["id"]) == previous


def test_attempt_cap_is_enforced_before_every_new_case(application):
    storage = application.state.storage
    campaign = campaign_fixture(storage, count=2)
    case = campaign["cases"][0]
    for number in range(49):
        identifier = f"previous-{number}"
        storage.save_run(
            {
                "id": identifier,
                "created_at": f"2026-10-12T00:00:{number:02d}Z",
                "state": "interrupted",
                "tests": [],
                "events": [],
            }
        )
        case["attempts"].append(identifier)
    case.update(state="interrupted", run_id=case["attempts"][-1])
    storage.save_campaign(campaign)
    with TestClient(application) as client:
        assert client.post(f"/api/campaigns/{campaign['id']}/resume").status_code == 202
        final = wait_campaign(client, campaign["id"], {"error"}, application)
        assert final["completed"] == 1
        assert sum(len(case["attempts"]) for case in final["cases"]) == 50
        assert final["cases"][1]["attempts"] == []
        assert "attempt limit" in final["error"]
        assert client.post(f"/api/campaigns/{campaign['id']}/resume").status_code == 409


def test_recovery_reconciles_run_finished_before_campaign_update(application):
    storage = application.state.storage
    campaign = campaign_fixture(storage)
    run = link_attempt(storage, campaign)
    run.update(state="completed", finished_at="2026-10-13T00:00:02Z")
    storage.save_run(run)
    with TestClient(application) as client:
        final = client.get(f"/api/campaigns/{campaign['id']}").json()
        assert final["state"] == "completed"
        assert final["completed"] == 1
        assert final["cases"][0]["attempts"] == [run["id"]]
        assert client.post(f"/api/campaigns/{campaign['id']}/resume").status_code == 409


def test_resume_rejects_engine_change_and_completed_runs(application, monkeypatch):
    module = importlib.import_module("devicetestlab.campaigns")
    campaign = campaign_fixture(application.state.storage)
    with TestClient(application) as client:
        monkeypatch.setattr(module, "engine_fingerprint", lambda: "changed")
        response = client.post(f"/api/campaigns/{campaign['id']}/resume")
        assert response.status_code == 409 and "Engine changed" in response.json()["detail"]
        assert application.state.storage.list_runs() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "too many", "scenarios": ["healthy", "timeout"], "seeds": list(range(13))},
        {"name": "duplicates", "scenarios": ["healthy"], "seeds": [3, 3]},
        {"name": "invalid", "scenarios": ["healthy"], "seeds": [True]},
    ],
)
def test_campaign_limits_reject_without_queuing(application, payload):
    with TestClient(application) as client:
        assert client.post("/api/campaigns", json=payload).status_code == 422
        assert client.get("/api/campaigns").json()["campaigns"] == []


def test_custom_direct_run_rejects_ml_strategy(application):
    raw = json.loads((Path(__file__).parents[1] / "examples/plans/byte-roundtrip.json").read_text())
    with TestClient(application) as client:
        assert client.post("/api/runs", json={"plan": raw, "strategy": "ml"}).status_code == 422
        assert client.get("/api/runs").json()["runs"] == []
