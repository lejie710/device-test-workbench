"""Exercise HTTP validation, asynchronous work, evidence export and startup recovery."""

import importlib
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient


def wait_for_run(client, identifier):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/runs/{identifier}").json()
        if result["state"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    pytest.fail("Background run did not finish within 10 seconds.")


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv("DTL_RUNTIME_DIR", str(tmp_path / "module_runtime"))
    create_app = importlib.import_module("devicetestlab.app").create_app
    return create_app(runtime_dir=tmp_path, frontend_dir=tmp_path / "missing_frontend")


@pytest.mark.parametrize(
    "scenario", ["healthy", "corrupt_read", "ignored_write", "stuck_state", "timeout"]
)
def test_completed_run_and_export_preserve_actual_evidence(application, scenario):
    with TestClient(application) as client:
        response = client.post(
            "/api/runs", json={"scenario": scenario, "strategy": "default", "seed": 3}
        )
        assert response.status_code == 202
        assert response.json()["state"] == "queued"
        completed = wait_for_run(client, response.json()["id"])
        assert completed["state"] == "completed", completed.get("error")
        assert completed["passed"] + completed["failed"] == completed["total"]
        assert completed["errors"] == 0
        assert (completed["failed"] == 0) == (scenario == "healthy")
        assert (completed["first_failure_ms"] is None) == (scenario == "healthy")
        exported = client.get(f"/api/runs/{completed['id']}/export")
        assert exported.json() == completed
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.json()["definition_snapshot"]["sha256"]
        assert client.get("/api/runs").json()["runs"][0]["id"] == completed["id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"scenario": "not_a_scenario"},
        {"strategy": "skip_tests"},
        {"seed": -1},
        {"seed": 2147483648},
        {"seed": True},
        {"seed": "3"},
        {"model_path": "elsewhere"},
    ],
)
def test_invalid_request_cannot_queue_work(application, payload):
    with TestClient(application) as client:
        assert client.post("/api/runs", json=payload).status_code == 422
        assert client.get("/api/runs").json() == {"runs": []}


def test_active_run_excludes_both_another_run_and_training(application, monkeypatch):
    module = importlib.import_module("devicetestlab.app")
    original = module.run_suite
    entered, release = Event(), Event()

    def paused_suite(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Test did not release the background worker."
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_suite", paused_suite)
    with TestClient(application) as client:
        try:
            first = client.post("/api/runs", json={})
            assert first.status_code == 202
            assert entered.wait(5)
            assert client.get("/api/health").json()["status"] == "ok"
            assert client.post("/api/runs", json={}).status_code == 409
            assert client.post("/api/experiment").status_code == 409
        finally:
            release.set()
        assert wait_for_run(client, first.json()["id"])["state"] == "completed"
        second = client.post("/api/runs", json={})
        assert second.status_code == 202
        assert wait_for_run(client, second.json()["id"])["state"] == "completed"


def test_failed_experiment_is_visible_and_releases_worker(application, monkeypatch):
    from devicetestlab import ml_service

    def broken_experiment(output_dir):
        raise RuntimeError("Injected experiment error")

    monkeypatch.setattr(ml_service, "run_experiment", broken_experiment)
    with TestClient(application) as client:
        assert client.post("/api/experiment").status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            report = client.get("/api/experiment").json()
            if report["state"] == "failed":
                break
            time.sleep(0.01)
        assert report["state"] == "failed"
        assert "Injected experiment error" in report["error"]
        response = client.post("/api/runs", json={})
        assert response.status_code == 202
        assert wait_for_run(client, response.json()["id"])["state"] == "completed"


def test_active_experiment_excludes_both_runs_and_second_training(application, monkeypatch):
    from devicetestlab import ml_service

    entered, release = Event(), Event()

    def paused_experiment(output_dir):
        entered.set()
        assert release.wait(5), "Test did not release the background experiment."
        return {"state": "completed", "findings": ["Test-controlled experiment."]}

    monkeypatch.setattr(ml_service, "run_experiment", paused_experiment)
    with TestClient(application) as client:
        try:
            assert client.post("/api/experiment").status_code == 202
            assert entered.wait(5)
            assert client.get("/api/experiment").json()["state"] == "running"
            assert client.post("/api/runs", json={}).status_code == 409
            assert client.post("/api/experiment").status_code == 409
        finally:
            release.set()


def test_incomplete_suite_cannot_be_recorded_as_completed(application, monkeypatch):
    module = importlib.import_module("devicetestlab.app")
    monkeypatch.setattr(module, "run_suite", lambda *args, **kwargs: [])
    with TestClient(application) as client:
        queued = client.post("/api/runs", json={}).json()
        result = wait_for_run(client, queued["id"])
        assert result["state"] == "error"
        assert "every catalog test" in result["error"]
        assert result["passed"] == result["failed"] == 0
        assert all(test["status"] == "pending" for test in result["tests"])


def test_missing_api_paths_remain_404_when_frontend_is_served(tmp_path, monkeypatch):
    monkeypatch.setenv("DTL_RUNTIME_DIR", str(tmp_path / "module_runtime"))
    create_app = importlib.import_module("devicetestlab.app").create_app
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>Device Test Lab</h1>", encoding="utf-8")
    with TestClient(create_app(tmp_path / "runtime", frontend)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/not_a_route").status_code == 404
        assert client.get("/api/runs/not_a_run").status_code == 404


def test_app_startup_preserves_unfinished_run_as_interrupted(application):
    storage = application.state.storage
    storage.save_run(
        {
            "id": "unfinished",
            "created_at": "2026-01-01T00:00:00Z",
            "state": "running",
            "events": [],
            "tests": [{"id": "initial_status", "status": "pass"}],
        }
    )
    with TestClient(application) as client:
        restored = client.get("/api/runs/unfinished").json()
        assert restored["state"] == "interrupted"
        assert restored["tests"][0]["status"] == "pass"
        assert restored["finished_at"]
