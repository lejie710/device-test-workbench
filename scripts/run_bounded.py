"""Run an owned command with a timeout, optional deadline and Windows tree cleanup."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

WINDOWS_BOOTSTRAP = (
    "import subprocess, sys; "
    "token = sys.stdin.buffer.read(1); "
    "sys.stdin.close(); "
    "sys.exit(125) if token != b'G' else None; "
    "child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, "
    "creationflags=0x08000000 | 0x00000200); "
    "sys.exit(child.wait())"
)


class WindowsJob:
    """A private Windows job closes every process assigned to it and its children."""

    def __init__(self) -> None:
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", IoCounters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: subprocess.Popen) -> None:
        # CPython's Windows Popen retains the owned process handle for waiting/cleanup.
        process_handle = int(getattr(process, "_handle"))
        if not self.kernel.AssignProcessToJobObject(self.handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--deadline", help="An ISO 8601 timestamp with timezone")
    parser.add_argument("--record", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a command after --")
    limit = args.timeout
    if args.deadline:
        deadline = datetime.fromisoformat(args.deadline.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            parser.error("deadline must include a timezone")
        limit = min(limit, (deadline - datetime.now(UTC)).total_seconds())
    if limit <= 0:
        print("Deadline reached; command was not started.", flush=True)
        return 124

    environment = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        environment[name] = "1"
    started = time.monotonic()
    job = WindowsJob() if os.name == "nt" else None
    process = None
    outcome = "error"
    code = 1
    try:
        flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
        # The target cannot create children before its bootstrap owns the job.
        launch = [sys.executable, "-u", "-c", WINDOWS_BOOTSTRAP, *command] if job else command
        process = subprocess.Popen(
            launch,
            env=environment,
            stdin=subprocess.PIPE if job else subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
        try:
            if job:
                try:
                    job.assign(process)
                except OSError:
                    process.terminate()
                    process.wait(timeout=5)
                    raise
            remaining = limit - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, limit)
            if job:
                assert process.stdin is not None
                process.stdin.write(b"G")
                process.stdin.flush()
                process.stdin.close()
            code = process.wait(timeout=max(0, limit - (time.monotonic() - started)))
            outcome = "completed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            outcome, code = "deadline", 124
            print("Command time budget reached; closing its owned process group.", flush=True)
        except KeyboardInterrupt:
            outcome, code = "interrupted", 130
    finally:
        if process is not None and process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass  # A closed bootstrap pipe must not prevent owned-job cleanup.
        if job:
            job.close()
        elif sys.platform != "win32" and process is not None and process.poll() is None:
            import signal

            os.killpg(process.pid, signal.SIGTERM)
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now(UTC).isoformat(),
                        "pid": process.pid if process else None,
                        "pid_role": "owned Windows bootstrap" if job else "target command",
                        "outcome": outcome,
                        "exit_code": code,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "effective_timeout_seconds": round(limit, 3),
                        "command": command,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
