"""Release contract across runtime, build inputs and a root-free deployment."""
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.version import VERSION, load_release

ROOT = Path(__file__).resolve().parents[2]
version_tool = runpy.run_path(str(ROOT / "scripts/version.py"))
MANIFESTS = (
    "backend/app/version.py", "backend/app/version.json", "backend/pyproject.toml", "backend/uv.lock",
    "frontend/package.json", "compose.yaml", "compose.build.yaml", ".env.example", "Dockerfile", "deploy/bot-api/Dockerfile",
    "deploy/bot-api/Dockerfile.verify-native", "deploy/bot-api/Dockerfile.verify-progress",
)


def test_release_matches_runtime_and_build_inputs(authenticated: TestClient) -> None:
    status = authenticated.get("/api/v1/system/status")
    assert status.status_code == 200
    assert status.json()["version"] == VERSION
    assert status.json()["storage"]["selected_root_id"] == authenticated.get(
        "/api/v1/settings").json()["values"]["download_root_id"]
    assert authenticated.get("/openapi.json").json()["info"]["version"] == VERSION
    assert version_tool["planned_updates"](ROOT) == {}


@pytest.mark.parametrize("version", ["0.1.0-alpha", "0.01.0", "1.0", "v1.0.0", "1.0.0+build", 1])
def test_release_rejects_nonnumeric_versions(tmp_path: Path, version: object) -> None:
    path = tmp_path / "version.json"
    path.write_text(json.dumps({"version": version}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid release metadata"):
        load_release(path)


def test_release_runs_with_only_deployed_app_files(tmp_path: Path) -> None:
    deployed = tmp_path / "app"
    deployed.mkdir()
    for name in ("__init__.py", "version.py", "version.json"):
        shutil.copy2(ROOT / "backend/app" / name, deployed / name)
    result = subprocess.run(
        [sys.executable, "-E", "-X", "utf8", "-c",
         "from app.version import VERSION; print(VERSION)"],
        cwd=tmp_path, check=True, capture_output=True, text=True, encoding="utf-8",
        env=os.environ | {"PYTHONIOENCODING": "utf-8", "TGD_VERSION": "99.0.0"},
    )
    assert result.stdout.strip() == VERSION


def test_bump_plans_all_mirrors_without_touching_dependencies(tmp_path: Path) -> None:
    for relative in MANIFESTS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    metadata = tmp_path / "backend/app/version.json"
    metadata.write_text(json.dumps({"version": "0.2.0"}), encoding="utf-8")
    changes = version_tool["planned_updates"](tmp_path)
    assert len(changes) == 10
    for path, content in changes.items():
        path.write_bytes(content.encode("utf-8"))
    assert version_tool["planned_updates"](tmp_path) == {}
    original_lock = (ROOT / "backend/uv.lock").read_text(encoding="utf-8")
    lock = (tmp_path / "backend/uv.lock").read_text(encoding="utf-8")
    assert lock.replace('name = "tgd-backend"\nversion = "0.2.0"',
                        f'name = "tgd-backend"\nversion = "{VERSION}"') == original_lock
    malformed = tmp_path / "Dockerfile"
    malformed.write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected release manifest structure"):
        version_tool["planned_updates"](tmp_path)
