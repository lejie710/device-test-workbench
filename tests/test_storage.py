"""Check restart recovery and retained evidence rather than SQLite implementation details."""

import json
import sqlite3

import pytest
from devicetestlab.storage import Storage


def snapshot(identifier="one", state="running"):
    return {
        "id": identifier,
        "created_at": "2026-01-01T00:00:00Z",
        "state": state,
        "finished_at": None,
        "tests": [{"id": "one", "status": "pass"}],
        "events": [],
        "configuration": {"seed": 42},
        "definition_snapshot": {"version": "old"},
    }


def test_evidence_survives_reopening_and_a_new_run(tmp_path):
    path = tmp_path / "history.sqlite3"
    storage = Storage(path)
    finished = snapshot(state="completed")
    storage.save_run(finished)
    storage.save_run(snapshot("two"))
    reopened = Storage(path)
    assert reopened.get_run("one") == finished
    assert len(reopened.list_runs()) == 2
    assert reopened.completed_history() == [finished]


def test_finished_run_cannot_be_overwritten_with_changed_results(tmp_path):
    storage = Storage(tmp_path / "history.sqlite3")
    finished = snapshot(state="completed")
    storage.save_run(finished)
    finished["tests"][0]["status"] = "fail"
    with pytest.raises(ValueError, match="immutable"):
        storage.save_run(finished)
    assert storage.get_run("one")["tests"][0]["status"] == "pass"


def test_restart_marks_unfinished_work_without_discarding_partial_evidence(tmp_path):
    storage = Storage(tmp_path / "history.sqlite3")
    storage.save_run(snapshot("running"))
    storage.save_run(snapshot("queued", "queued"))
    storage.save_run(snapshot("done", "completed"))
    storage.save_experiment({"state": "running"})
    assert storage.recover_interrupted() == 2
    for identifier in ("running", "queued"):
        restored = storage.get_run(identifier)
        assert restored["state"] == "interrupted"
        assert restored["tests"] == snapshot()["tests"]
        assert restored["finished_at"]
        assert restored["error"]
    assert storage.get_run("done")["state"] == "completed"
    assert storage.get_experiment()["state"] == "failed"
    assert storage.recover_interrupted() == 0


def test_lookups_do_not_interpret_identifiers_as_queries(tmp_path):
    storage = Storage(tmp_path / "history.sqlite3")
    storage.save_run(snapshot())
    assert storage.get_run("' OR 1=1 --") is None
    assert storage.get_run("one") is not None


def test_legacy_database_adds_campaigns_without_rewriting_saved_evidence(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    original = snapshot(state="completed")
    run_bytes = json.dumps(original, indent=3)
    report = {"state": "completed", "methods": [{"id": "default", "cost": 388.578}]}
    report_bytes = json.dumps(report, indent=5)
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE runs(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "state TEXT NOT NULL, snapshot TEXT NOT NULL);"
        "CREATE TABLE experiment(id INTEGER PRIMARY KEY CHECK(id=1), snapshot TEXT NOT NULL);"
    )
    connection.execute(
        "INSERT INTO runs VALUES (?,?,?,?)",
        (original["id"], original["created_at"], "completed", run_bytes),
    )
    connection.execute("INSERT INTO experiment VALUES (1,?)", (report_bytes,))
    connection.commit()
    connection.close()
    storage = Storage(path)
    assert storage.recover_interrupted() == 0
    assert storage.get_run(original["id"]) == original
    assert storage.get_experiment() == report
    assert storage.list_campaigns() == []
    with storage._connect() as connection:
        assert connection.execute("SELECT snapshot FROM runs").fetchone()[0] == run_bytes
        assert connection.execute("SELECT snapshot FROM experiment").fetchone()[0] == report_bytes
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
