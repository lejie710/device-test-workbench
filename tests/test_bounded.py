"""Verify deadline refusal and Windows cleanup affects only the owned process tree."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER = PROJECT_ROOT / "scripts" / "run_bounded.py"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run_helper(*arguments):
    return subprocess.run(
        [sys.executable, str(HELPER), *arguments],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=HIDDEN,
    )


def test_past_deadline_does_not_start_target(tmp_path):
    marker = tmp_path / "must_not_exist.txt"
    deadline = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    result = run_helper(
        "--deadline",
        deadline,
        "--",
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')",
        str(marker),
    )
    assert result.returncode == 124
    assert "command was not started" in result.stdout
    assert not marker.exists()


@pytest.mark.parametrize("expected_code", [0, 7])
def test_normal_command_returns_its_own_exit_code(tmp_path, expected_code):
    record = tmp_path / "completed.json"
    result = run_helper(
        "--timeout",
        "5",
        "--record",
        str(record),
        "--",
        sys.executable,
        "-c",
        f"raise SystemExit({expected_code})",
    )
    assert result.returncode == expected_code, result.stderr
    evidence = json.loads(record.read_text(encoding="utf-8"))
    assert evidence["exit_code"] == expected_code
    assert evidence["outcome"] == ("completed" if expected_code == 0 else "failed")


def load_helper():
    spec = importlib.util.spec_from_file_location("tested_bounded_helper", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership regression")
def test_target_waits_until_job_assignment_finishes(tmp_path, monkeypatch):
    module = load_helper()
    original = module.WindowsJob.assign
    marker = tmp_path / "target_started.txt"

    def slow_assignment(self, process):
        time.sleep(0.3)
        assert not marker.exists(), "Target escaped before assignment to the owned job."
        original(self, process)

    monkeypatch.setattr(module.WindowsJob, "assign", slow_assignment)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(HELPER),
            "--timeout",
            "5",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')",
            str(marker),
        ],
    )
    assert module.main() == 0
    assert marker.read_text() == "started"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership regression")
def test_failed_assignment_never_starts_target(tmp_path, monkeypatch):
    module = load_helper()
    marker = tmp_path / "must_not_start.txt"

    def denied_assignment(self, process):
        time.sleep(0.3)
        raise PermissionError("Injected assignment denial")

    monkeypatch.setattr(module.WindowsJob, "assign", denied_assignment)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(HELPER),
            "--timeout",
            "5",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')",
            str(marker),
        ],
    )
    with pytest.raises(PermissionError, match="Injected assignment denial"):
        module.main()
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows gated-start deadline regression")
def test_budget_exhausted_during_assignment_does_not_release_target(tmp_path, monkeypatch):
    module = load_helper()
    original = module.WindowsJob.assign
    marker = tmp_path / "must_not_start_late.txt"

    def slow_assignment(self, process):
        time.sleep(0.2)
        original(self, process)

    monkeypatch.setattr(module.WindowsJob, "assign", slow_assignment)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(HELPER),
            "--timeout",
            "0.1",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')",
            str(marker),
        ],
    )
    assert module.main() == 124
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree cleanup regression")
def test_timeout_stops_nested_child_but_preserves_unrelated_sentinel(tmp_path):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    pid_file = tmp_path / "owned_pids.json"
    record = tmp_path / "timeout.json"
    child_code = "import time; time.sleep(20)"
    target_code = (
        "import json, os, subprocess, sys, time; from pathlib import Path; "
        "child = subprocess.Popen([sys.executable, '-c', sys.argv[2]], "
        "creationflags=0x08000000); "
        "Path(sys.argv[1]).write_text(json.dumps({'parent':os.getpid(),'child':child.pid})); "
        "time.sleep(20)"
    )
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=HIDDEN,
    )
    helper = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "--timeout",
            "2",
            "--record",
            str(record),
            "--",
            sys.executable,
            "-c",
            target_code,
            str(pid_file),
            child_code,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=HIDDEN,
    )
    handles = []
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and helper.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), "The owned test process did not record its child."
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        for pid in pids.values():
            # SYNCHRONIZE grants read-only waiting, with no termination permission.
            handle = kernel.OpenProcess(0x00100000, False, pid)
            assert handle, ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            assert kernel.WaitForSingleObject(handle, 0) == 0x102
        assert helper.wait(timeout=10) == 124
        evidence = json.loads(record.read_text(encoding="utf-8"))
        assert evidence["outcome"] == "deadline"
        assert evidence["pid_role"] == "owned Windows bootstrap"
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 2000) == 0
        assert sentinel.poll() is None, "Cleanup affected an independently created process."
    finally:
        if helper.poll() is None:
            helper.terminate()
            helper.wait(timeout=5)
        # A failed check still waits for the explicitly bounded fixtures to finish.
        for handle in handles:
            kernel.WaitForSingleObject(handle, 25000)
            kernel.CloseHandle(handle)
        if sentinel.poll() is None:
            sentinel.terminate()
        sentinel.wait(timeout=5)
