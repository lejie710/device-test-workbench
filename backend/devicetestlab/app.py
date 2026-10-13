"""Local workbench API and one bounded background worker shared by all jobs."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .campaign_comparison import register_campaign_comparison_routes
from .campaigns import register_campaign_routes
from .engine import SCENARIOS, TESTS, run_suite, test_definition_snapshot
from .plans import custom_definition, engine_fingerprint, plan_snapshot, run_plan
from .runtime_lock import RuntimeOwnership
from .schemas import RunRequest
from .storage import Storage, utc_now

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = [
    {
        "id": "default",
        "label": "Catalog order",
        "description": "Run tests in the documented catalog order.",
    },
    {
        "id": "recent_first",
        "label": "Recent failures first",
        "description": "A history-based rule prioritizes likely failures and lower modeled cost.",
    },
    {
        "id": "ml",
        "label": "ML priority",
        "description": "Use the saved local model, with an explicit fallback when unavailable.",
    },
]
SUMMARY_KEYS = (
    "id",
    "created_at",
    "finished_at",
    "state",
    "scenario",
    "strategy",
    "seed",
    "passed",
    "failed",
    "errors",
    "total",
    "duration_ms",
    "first_failure_ms",
    "model_status",
)


def _pending_test(test: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": test["id"],
        "name": test["name"],
        "status": "pending",
        "duration_ms": 0,
        "modeled_cost_ms": test["modeled_cost_ms"],
        "expected": None,
        "actual": None,
        "message": "Waiting to execute.",
        "trace": [],
    }


def create_app(runtime_dir: Path | None = None, frontend_dir: Path | None = None) -> FastAPI:
    runtime = runtime_dir or Path(os.environ.get("DTL_RUNTIME_DIR", PROJECT_ROOT / "runtime"))
    storage = Storage(Path(runtime) / "history.sqlite3")
    experiment_dir = Path(runtime) / "experiment"
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dtl-worker")
    gate = Lock()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        ownership = RuntimeOwnership(Path(runtime))
        try:
            ownership.acquire()
        except BaseException:
            worker.shutdown(wait=True, cancel_futures=True)
            raise
        try:
            storage.recover_interrupted()
            yield
        finally:
            try:
                # Keep ownership until our worker has finished writing its evidence.
                worker.shutdown(wait=True, cancel_futures=True)
            finally:
                ownership.release()

    application = FastAPI(title="Device Test Lab", version=__version__, lifespan=lifespan)
    application.state.storage = storage
    application.state.worker_gate = gate

    def perform_run(run: dict[str, Any], release_gate: bool = True) -> None:
        started = perf_counter()
        try:
            from .ml_service import prioritize

            if run.get("suite_kind") == "custom":
                order = run["test_order"]
                status = "Custom plan: declared order; the standard model is not compatible."
            else:
                history = [
                    item
                    for item in storage.completed_history()
                    if item.get("suite_kind", "standard") == "standard"
                ]
                order, status = prioritize(run["strategy"], history, experiment_dir / "model")
            run.update(state="running", test_order=order, model_status=status)
            test_by_id = {item["id"]: item for item in run["tests"]}
            run["tests"] = [test_by_id[identifier] for identifier in order]
            run["events"].append(
                {"at": utc_now(), "level": "info", "message": f"Full suite started. {status}"}
            )
            storage.save_run(run)

            def on_result(result: dict[str, Any]) -> None:
                index = order.index(result["id"])
                run["tests"][index] = result
                counter = {"pass": "passed", "fail": "failed", "error": "errors"}[result["status"]]
                run[counter] += 1
                run["duration_ms"] = round((perf_counter() - started) * 1000, 3)
                if result["status"] == "fail" and run["first_failure_ms"] is None:
                    run["first_failure_ms"] = run["duration_ms"]
                run["events"].append(
                    {
                        "at": utc_now(),
                        "level": "error"
                        if result["status"] == "error"
                        else "warning"
                        if result["status"] == "fail"
                        else "info",
                        "message": f"{result['name']}: {result['message']}",
                        "test_id": result["id"],
                    }
                )
                storage.save_run(run)

            if run.get("suite_kind") == "custom":
                run_plan(run["plan_snapshot"], run["scenario"], run["seed"], on_result)
            else:
                run_suite(run["scenario"], run["seed"], order=order, on_result=on_result)
            if run["passed"] + run["failed"] + run["errors"] != run["total"]:
                raise RuntimeError(
                    "The runner returned before every catalog test produced a result."
                )
            run["state"] = "error" if run["errors"] else "completed"
            if run["errors"]:
                run["error"] = (
                    "One or more tests encountered runner errors; inspect their evidence."
                )
            run["duration_ms"] = round((perf_counter() - started) * 1000, 3)
            run["finished_at"] = utc_now()
            run["events"].append(
                {
                    "at": utc_now(),
                    "level": "info",
                    "message": (
                        f"Full suite finished: {run['passed']} passed, {run['failed']} failed, "
                        f"{run['errors']} runner errors."
                    ),
                }
            )
            storage.save_run(run)
        except Exception as exc:
            run.update(
                state="error",
                finished_at=utc_now(),
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
            run["events"].append({"at": utc_now(), "level": "error", "message": run["error"]})
            storage.save_run(run)
        finally:
            if release_gate:
                gate.release()

    def perform_experiment() -> None:
        try:
            from .ml_service import run_experiment

            storage.save_experiment(run_experiment(experiment_dir))
        except Exception as exc:
            storage.save_experiment({"state": "failed", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            gate.release()

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        return {"scenarios": SCENARIOS, "tests": TESTS, "strategies": STRATEGIES}

    @application.get("/api/runs")
    def runs() -> dict[str, Any]:
        return {
            "runs": [
                {
                    **{key: run[key] for key in SUMMARY_KEYS},
                    "suite_kind": run.get("suite_kind", "standard"),
                    "plan_name": run.get("plan_name", "Standard catalog"),
                }
                for run in storage.list_runs()
            ]
        }

    def make_run(request: RunRequest) -> dict[str, Any]:
        if request.plan is not None and request.strategy != "default":
            raise HTTPException(
                422, "Custom plans execute in declared order; choose default strategy."
            )
        snapshot = plan_snapshot(request.plan)
        definition = custom_definition(snapshot) if request.plan else test_definition_snapshot()
        tests = definition["tests"]
        run = {
            "id": str(uuid4()),
            "created_at": utc_now(),
            "finished_at": None,
            "state": "queued",
            **request.model_dump(exclude={"plan"}),
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "total": len(tests),
            "duration_ms": 0,
            "first_failure_ms": None,
            "model_status": "Ordering pending.",
            "test_order": [test["id"] for test in tests],
            "tests": [_pending_test(test) for test in tests],
            "events": [{"at": utc_now(), "level": "info", "message": "Run queued."}],
            "error": None,
            "definition_snapshot": definition,
            "suite_kind": "custom" if request.plan else "standard",
            "plan_snapshot": snapshot if request.plan else None,
            "plan_name": snapshot["name"],
            "engine_fingerprint": engine_fingerprint(),
            "simulation_notice": (
                "In-process virtual device; modeled timing is not physical hardware timing."
            ),
        }
        return run

    @application.post("/api/runs", status_code=202)
    def start_run(request: RunRequest) -> dict[str, Any]:
        run = make_run(request)
        if not gate.acquire(blocking=False):
            raise HTTPException(
                409, "A run, campaign or experiment is active. Wait for it to finish."
            )
        try:
            storage.save_run(run)
            response = json.loads(json.dumps(run))
            worker.submit(perform_run, run)
        except Exception:
            gate.release()
            raise
        return response

    @application.get("/api/runs/{identifier}")
    def run_detail(identifier: str) -> dict[str, Any]:
        run = storage.get_run(identifier)
        if run is None:
            raise HTTPException(404, "Run not found.")
        return run

    @application.get("/api/runs/{identifier}/export")
    def export_run(identifier: str) -> Response:
        run = run_detail(identifier)
        return Response(
            json.dumps(run, indent=2, allow_nan=False),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="device-test-run-{run["id"]}.json"'
            },
        )

    @application.get("/api/experiment")
    def experiment() -> dict[str, Any]:
        return storage.get_experiment()

    @application.post("/api/experiment", status_code=202)
    def start_experiment() -> dict[str, str]:
        if not gate.acquire(blocking=False):
            raise HTTPException(409, "A run or experiment is active. Wait for it to finish.")
        try:
            storage.save_experiment({"state": "running", "started_at": utc_now()})
            worker.submit(perform_experiment)
        except Exception:
            gate.release()
            raise
        return {"state": "running"}

    @application.get("/api/compare")
    def compare(left: str, right: str) -> dict[str, Any]:
        from .comparison import compare_runs

        return compare_runs(run_detail(left), run_detail(right))

    register_campaign_routes(application, storage, worker, gate, make_run, perform_run)
    register_campaign_comparison_routes(application, storage)

    frontend = frontend_dir or PROJECT_ROOT / "frontend" / "dist"
    if Path(frontend).is_dir():
        application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return application


app = create_app()
