"""Transactional JSON snapshots in SQLite, with crash recovery on service startup."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

TERMINAL_STATES = {"completed", "error", "interrupted"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Storage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    snapshot TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS experiment (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    snapshot TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_run(self, run: dict[str, Any]) -> None:
        payload = json.dumps(run, allow_nan=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT state, snapshot FROM runs WHERE id = ?", (run["id"],)
            ).fetchone()
            if existing and existing[0] in TERMINAL_STATES:
                if existing[1] == payload:
                    return
                raise ValueError("A finished run is immutable; create a new run instead.")
            connection.execute(
                """INSERT INTO runs(id, created_at, state, snapshot) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state=excluded.state, snapshot=excluded.snapshot""",
                (run["id"], run["created_at"], run["state"], payload),
            )

    def get_run(self, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot FROM runs WHERE id = ?", (identifier,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot FROM runs ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def completed_history(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot FROM runs WHERE state = 'completed' ORDER BY created_at, id"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_experiment(self, report: dict[str, Any]) -> None:
        payload = json.dumps(report, allow_nan=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO experiment(id, snapshot) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot""",
                (payload,),
            )

    def get_experiment(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT snapshot FROM experiment WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else {"state": "not_trained"}

    @staticmethod
    def _validate_campaign_update(old: dict, campaign: dict) -> None:
        if old["state"] == "completed" and old != campaign:
            raise ValueError("A completed campaign is immutable.")
        for key in ("name", "created_at", "plan_snapshot", "engine_fingerprint", "total"):
            if old[key] != campaign[key]:
                raise ValueError("Campaign conditions and plan snapshots are immutable.")
        if len(old["cases"]) != len(campaign["cases"]):
            raise ValueError("Campaign cases are immutable.")
        for before, after in zip(old["cases"], campaign["cases"], strict=True):
            if any(before[key] != after[key] for key in ("id", "scenario", "seed")):
                raise ValueError("Campaign case conditions are immutable.")
            if before["state"] == "completed" and before != after:
                raise ValueError("A completed case cannot be rerun or overwritten.")
            if after["attempts"][: len(before["attempts"])] != before["attempts"]:
                raise ValueError("Earlier campaign attempts must be preserved.")

    def save_campaign_attempt(self, run: dict[str, Any], campaign: dict[str, Any]) -> None:
        """Create an attempt and link it in one transaction, including after a restart."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot FROM campaigns WHERE id = ?", (campaign["id"],)
            ).fetchone()
            if row is None:
                raise ValueError("Campaign must exist before creating an attempt.")
            self._validate_campaign_update(json.loads(row[0]), campaign)
            case = next(
                (case for case in campaign["cases"] if case["id"] == run.get("campaign_case_id")),
                None,
            )
            if (
                run.get("campaign_id") != campaign["id"]
                or case is None
                or case["run_id"] != run["id"]
                or case["attempts"][-1] != run["id"]
            ):
                raise ValueError("Attempt must be linked to its owning campaign case.")
            connection.execute(
                "INSERT INTO runs(id, created_at, state, snapshot) VALUES (?, ?, ?, ?)",
                (run["id"], run["created_at"], run["state"], json.dumps(run, allow_nan=False)),
            )
            connection.execute(
                "UPDATE campaigns SET state = ?, snapshot = ? WHERE id = ?",
                (campaign["state"], json.dumps(campaign, allow_nan=False), campaign["id"]),
            )

    def save_campaign(self, campaign: dict[str, Any]) -> None:
        payload = json.dumps(campaign, allow_nan=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot FROM campaigns WHERE id = ?", (campaign["id"],)
            ).fetchone()
            if row:
                self._validate_campaign_update(json.loads(row[0]), campaign)
            connection.execute(
                """INSERT INTO campaigns(id, created_at, state, snapshot) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state=excluded.state, snapshot=excluded.snapshot""",
                (campaign["id"], campaign["created_at"], campaign["state"], payload),
            )

    def get_campaign(self, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot FROM campaigns WHERE id = ?", (identifier,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot FROM campaigns ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def recover_interrupted(self) -> int:
        """Preserve partial evidence; a restarted server must never imply a finished suite."""
        recovered = 0
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot FROM runs WHERE state IN ('queued', 'running')"
            ).fetchall()
            for row in rows:
                run = json.loads(row[0])
                run["state"] = "interrupted"
                run["finished_at"] = utc_now()
                run["error"] = "The service stopped before the full suite completed."
                run.setdefault("events", []).append(
                    {"at": utc_now(), "level": "warning", "message": run["error"]}
                )
                connection.execute(
                    "UPDATE runs SET state = ?, snapshot = ? WHERE id = ?",
                    ("interrupted", json.dumps(run, allow_nan=False), run["id"]),
                )
                recovered += 1
            row = connection.execute("SELECT snapshot FROM experiment WHERE id = 1").fetchone()
            if row and json.loads(row[0]).get("state") == "running":
                report = {
                    "state": "failed",
                    "error": "The service stopped before the ordering experiment completed.",
                }
                connection.execute(
                    "UPDATE experiment SET snapshot = ? WHERE id = 1", (json.dumps(report),)
                )
            campaigns = connection.execute(
                "SELECT snapshot FROM campaigns WHERE state IN ('queued','running','pausing')"
            ).fetchall()
            for row in campaigns:
                campaign = json.loads(row[0])
                for case in campaign["cases"]:
                    if case["state"] == "running":
                        saved = connection.execute(
                            "SELECT state FROM runs WHERE id = ?", (case["run_id"],)
                        ).fetchone()
                        case["state"] = (
                            "completed" if saved and saved[0] == "completed" else "interrupted"
                        )
                campaign["completed"] = sum(
                    case["state"] == "completed" for case in campaign["cases"]
                )
                complete = campaign["completed"] == campaign["total"]
                campaign["state"] = "completed" if complete else "interrupted"
                campaign["finished_at"] = utc_now() if complete else None
                campaign["error"] = (
                    None if complete else "Service stopped. Resume remaining runs explicitly."
                )
                campaign["pause_requested"] = False
                connection.execute(
                    "UPDATE campaigns SET state = ?, snapshot = ? WHERE id = ?",
                    (campaign["state"], json.dumps(campaign, allow_nan=False), campaign["id"]),
                )
        return recovered
