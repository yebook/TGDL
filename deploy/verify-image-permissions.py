"""Offline image check; run with Python as each configured container UID/GID.

Use a disposable container with --network none, --read-only, no capabilities,
and no user volumes. Set TGD_DATA_DIR=/tmp/tgd-check and provide writable /tmp.
The app or api argument selects the production image being checked.
"""

import importlib
import os
import runpy
import stat
import sys
from pathlib import Path


def check(path: Path) -> None:
    mode = path.stat().st_mode
    if path.is_dir():
        assert mode & 0o555 == 0o555, f"Directory is not traversable: {path}"
        assert os.access(path, os.R_OK | os.X_OK), path
    else:
        assert mode & 0o444 == 0o444, f"File is not readable: {path}"
        with path.open("rb") as stream:
            stream.read(1)
    assert not mode & (stat.S_IWGRP | stat.S_IWOTH), f"Image content is writable: {path}"


def check_app_import(backend: Path) -> None:
    # Python executing /verify-permissions.py adds /, not WORKDIR, to sys.path.
    # Match uvicorn's explicit application directory instead of relying on cwd
    # or PYTHONPATH (stdin-based checks accidentally hid this difference).
    sys.path.insert(0, str(backend))
    module = importlib.import_module("app.main")
    expected = (backend / "app/main.py").resolve()
    assert module.__file__ is not None, "Application module has no source location"
    actual = Path(module.__file__).resolve()
    assert actual == expected, f"Imported unexpected application module: {actual}"


def main() -> None:
    if sys.argv[1] == "app":
        root = Path("/app")
        for path in (root, *root.rglob("*")):
            check(path)
        entrypoint = root / "entrypoint.py"
        compile(entrypoint.read_bytes(), str(entrypoint), "exec")
        if "--build" not in sys.argv:
            check_app_import(root / "backend")
        assert Path("/app/frontend/dist/index.html").read_bytes()
        assert os.access("/app/backend/.venv/bin/uvicorn", os.X_OK)
    elif sys.argv[1] == "api":
        for name in ("/usr", "/usr/local", "/usr/local/lib"):
            check(Path(name))
        for name in ("/entrypoint.sh", "/usr/local/lib/tgd-api-supervisor.py", "/usr/local/lib/tgd-init-volumes.py"):
            path = Path(name)
            check(path)
            if path.suffix == ".py":
                runpy.run_path(str(path), run_name="permission_check")
        assert os.access("/entrypoint.sh", os.X_OK)
    else:
        raise ValueError("Expected app or api")

    print(f"PASS {sys.argv[1]} image: readable code/assets, executable tools, UID:GID {os.getuid()}:{os.getgid()}")


if __name__ == "__main__":
    main()
