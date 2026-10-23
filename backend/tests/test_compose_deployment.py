"""Parse the actual shared Compose file without building or touching the daemon."""
import json
import os
import runpy
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
    assert services["app"]["image"] == f"tgd-app:{version}"
    assert services["bot-api"]["image"] == f"tgd-bot-api:{version}"
    assert services["init-volumes"]["image"] == services["bot-api"]["image"]
    assert all(service["pull_policy"] == "never" for service in services.values())
    assert services["app"]["build"]["dockerfile"] == "Dockerfile"
    assert services["bot-api"]["build"] == services["init-volumes"]["build"]
    assert services["bot-api"]["build"]["args"] == {
        "TGD_NATIVE_IMAGE": "bundled-native", "TGD_BUILD_JOBS": "2",
    }
    assert not (ROOT / "compose.build.yaml").exists()


def test_source_build_plan_never_uses_a_remote_production_image() -> None:
    targets = compose(ROOT, "build", "--print")["target"]
    assert set(targets) == {"app", "bot-api", "init-volumes"}
    assert targets["app"]["tags"][0].startswith("tgd-app:")
    assert targets["bot-api"]["tags"][0].startswith("tgd-bot-api:")
    assert targets["bot-api"] == targets["init-volumes"]
    assert targets["bot-api"]["args"]["TGD_NATIVE_IMAGE"] == "bundled-native"


@pytest.mark.parametrize("image_env", ["images.env", "images.ghcr.env"])
def test_image_bundle_uses_identical_compose_without_source(tmp_path: Path, image_env: str) -> None:
    tool = runpy.run_path(str(ROOT / "scripts/publish_images.py"))
    primary = ["ghcr.io/yebook/tgdl-app@sha256:" + "a" * 64,
               "ghcr.io/yebook/tgdl-bot-api@sha256:" + "b" * 64]
    mirrors = [reference.replace("ghcr.io/yebook/", "docker.io/example/") for reference in primary]
    output = tmp_path / "deployment"
    tool["bundle"](ROOT, output, *primary, "c" * 40, "d" * 64, "e" * 64, "f" * 64, mirrors)
    assert (output / "compose.yaml").read_bytes() == (ROOT / "compose.yaml").read_bytes()
    assert not (output / "Dockerfile").exists()
    assert not (output / "deploy").exists()
    services = compose(output, "config", "--format", "json", image_env=image_env)["services"]
    app, api = mirrors if image_env == "images.env" else primary
    assert services["app"]["image"] == app
    assert services["bot-api"]["image"] == services["init-volumes"]["image"] == api
    assert all(service["pull_policy"] == "missing" for service in services.values())
    instructions = (output / "START.txt").read_text()
    assert "up -d --no-build" in instructions
    assert "--build\n" not in instructions
