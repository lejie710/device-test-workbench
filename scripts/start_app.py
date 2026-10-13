"""Start the local workbench without changing PowerShell execution policy."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8717)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend" / "dist" / "index.html").is_file():
        parser.error("frontend is not built; follow the setup instructions in README.md")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    import uvicorn

    print(f"Device Test Lab: http://127.0.0.1:{args.port}", flush=True)
    print("Press Ctrl+C to stop this server.", flush=True)
    uvicorn.run("devicetestlab.app:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
