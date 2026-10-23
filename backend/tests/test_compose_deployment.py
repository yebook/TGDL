"""Parse the actual shared Compose file without building or touching the daemon."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def compose(directory: Path, *arguments: str, image_env: str | None = None) -> dict[str, Any]:
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is not installed")
    # Do not inherit a developer's image overrides, .env, credentials or project name.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TGD_", "COMPOSE_"))}
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, env=environment)
    if version.returncode:
        pytest.skip("Docker Compose CLI is not available")
    command = ["docker", "compose", "--env-file", str(directory / ".env.example")]
    if image_env:
        command += ["--env-file", str(directory / image_env)]
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


def test_old_remote_overrides_cannot_redirect_source_deployment(tmp_path: Path) -> None:
    shutil.copyfile(ROOT / "compose.yaml", tmp_path / "compose.yaml")
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env.example")
    (tmp_path / "images.env").write_text(
        "TGD_APP_IMAGE=ghcr.io/example/old-app:latest\n"
        "TGD_API_IMAGE=ghcr.io/example/old-api:latest\n"
        "TGD_PULL_POLICY=always\nTGD_NATIVE_IMAGE=ghcr.io/example/old-native:latest\n"
    )
    services = compose(tmp_path, "config", "--format", "json", image_env="images.env")["services"]
    assert services["app"]["pull_policy"] == services["bot-api"]["pull_policy"] == "build"
    assert services["init-volumes"]["pull_policy"] == "never"
    assert all(service["image"].startswith("tgd-") and "-source:" in service["image"]
               for service in services.values())
    assert services["bot-api"]["build"]["args"]["TGD_NATIVE_IMAGE"] == "bundled-native"
