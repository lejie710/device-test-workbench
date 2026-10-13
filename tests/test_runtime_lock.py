"""A second service cannot mistake another live worker for an interrupted run."""

import importlib
import os
import subprocess
import sys
import time
from threading import Event

import pytest
from devicetestlab.runtime_lock import RuntimeOwnership
from fastapi.testclient import TestClient


def test_ownership_is_exclusive_and_reusable_after_release(tmp_path):
    first = RuntimeOwnership(tmp_path)
    second = RuntimeOwnership(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            second.acquire()
    finally:
        first.release()
    assert first.path.exists()  # File presence alone is not evidence of a held OS lock.
    second.acquire()
    second.release()
    second.release()


def test_process_exit_releases_lock_without_deleting_its_file(tmp_path):
    code = (
        "import os, sys; from pathlib import Path; "
        "from devicetestlab.runtime_lock import RuntimeOwnership; "
        "owner = RuntimeOwnership(Path(sys.argv[1])); owner.acquire(); "
        "print('ownership acquired', flush=True); os._exit(0)"
    )
    child = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert "ownership acquired" in child.stdout
    owner = RuntimeOwnership(tmp_path)
    assert owner.path.exists()
    owner.acquire()
    owner.release()


def test_second_app_is_rejected_and_first_worker_still_finishes(tmp_path, monkeypatch):
    monkeypatch.setenv("DTL_RUNTIME_DIR", str(tmp_path / "module_runtime"))
    module = importlib.import_module("devicetestlab.app")
    runtime = tmp_path / "shared"
    first_app = module.create_app(runtime, tmp_path / "no_frontend")
    second_app = module.create_app(runtime, tmp_path / "no_frontend")
    entered, release = Event(), Event()
    original = module.run_suite

    def paused_suite(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Test did not release the first service's worker."
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_suite", paused_suite)
    with TestClient(first_app) as client:
        response = client.post("/api/runs", json={"scenario": "healthy", "seed": 0})
        identifier = response.json()["id"]
        try:
            assert entered.wait(5)
            with pytest.raises(RuntimeError, match="already in use"), TestClient(second_app):
                pytest.fail("A second service must fail before accepting requests.")
            assert client.get(f"/api/runs/{identifier}").json()["state"] == "running"
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(f"/api/runs/{identifier}").json()
            if result["state"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert result["state"] == "completed", result.get("error")
        assert result["passed"] == result["total"] == 14
        assert result["failed"] == result["errors"] == 0
    # A fresh instance can start once the first lifespan has drained its worker.
    with TestClient(module.create_app(runtime, tmp_path / "no_frontend")) as restarted:
        assert restarted.get(f"/api/runs/{identifier}").json()["state"] == "completed"


def test_recovery_failure_releases_runtime_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("DTL_RUNTIME_DIR", str(tmp_path / "module_runtime"))
    module = importlib.import_module("devicetestlab.app")
    app = module.create_app(tmp_path / "runtime", tmp_path / "no_frontend")

    def failed_recovery():
        raise ValueError("Injected recovery failure")

    monkeypatch.setattr(app.state.storage, "recover_interrupted", failed_recovery)
    with pytest.raises(ValueError, match="Injected recovery failure"), TestClient(app):
        pytest.fail("Recovery failure must prevent this startup.")
    with TestClient(module.create_app(tmp_path / "runtime", tmp_path / "no_frontend")) as client:
        assert client.get("/api/health").status_code == 200
