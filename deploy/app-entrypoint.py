"""Preserve group readability of shared state without root or a Docker socket."""

import os

os.umask(0o027)
os.execvp(
    "uvicorn",
    [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--workers",
        "1",
        "--no-access-log",
    ],
)
