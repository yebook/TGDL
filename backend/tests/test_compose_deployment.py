"""Parse the actual shared Compose file without building or touching the daemon."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def compose(directory: Path, *arguments: str) -> dict[str, Any]:
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is not installed")
    # Do not inherit a developer's image overrides, .env, credentials or project name.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TGD_", "COMPOSE_"))}
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, env=environment)
    if version.returncode:
        pytest.skip("Docker Compose CLI is not available")
    command = ["docker", "compose", "--env-file", str(directory / ".env.example")]
    result = subprocess.run(
        [*command, "-f", str(directory / "compose.yaml"), *arguments],
        capture_output=True, text=True, encoding="utf-8", env=environment, check=True,
    )
    return dict(json.loads(result.stdout))


def test_source_uses_one_file_local_images_and_bundled_native() -> None:
    services = compose(ROOT, "config", "--format", "json")["services"]
    version = json.loads((ROOT / "backend/app/version.json").read_text())["version"]
    assert services["app"]["image"] == f"tgd-app-source:{version}"
    assert services["bot-api"]["image"] == f"tgd-bot-api-source:{version}"
    assert services["init-volumes"]["image"] == services["bot-api"]["image"]
    assert services["app"]["pull_policy"] == services["bot-api"]["pull_policy"] == "build"
    assert services["init-volumes"]["pull_policy"] == "never"
    assert services["app"]["build"]["dockerfile"] == "Dockerfile"
    assert "build" not in services["init-volumes"]  # One exporter per API tag.
    assert services["bot-api"]["build"]["args"] == {
        "TGD_NATIVE_IMAGE": "bundled-native", "TGD_BUILD_JOBS": "2",
    }
    assert not (ROOT / "compose.build.yaml").exists()


def test_source_build_plan_never_uses_a_remote_production_image() -> None:
    targets = compose(ROOT, "build", "--print")["target"]
    assert set(targets) == {"app", "bot-api"}
    assert targets["app"]["tags"][0].startswith("tgd-app-source:")
    assert targets["bot-api"]["tags"][0].startswith("tgd-bot-api-source:")
    assert targets["bot-api"]["args"]["TGD_NATIVE_IMAGE"] == "bundled-native"
