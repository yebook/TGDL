"""Distribution checks use fake registry responses and temporary source files."""
import hashlib
import json
import runpy
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = runpy.run_path(str(ROOT / "scripts/publish_images.py"))


def test_api_identity_covers_native_runtime_and_exclusions(tmp_path: Path) -> None:
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    (api / "supervisor.py").write_text("version = 1\n")
    identity = TOOL["api_identity"](tmp_path)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/app.ts").write_text("app-only change")
    assert TOOL["api_identity"](tmp_path) == identity
    (api / "supervisor.py").write_text("version = 2\n")
    assert TOOL["api_identity"](tmp_path) != identity
    (api / "supervisor.py").write_text("version = 1\n")
    (tmp_path / ".dockerignore").write_text("changed exclusions\n")
    assert TOOL["api_identity"](tmp_path) != identity


@pytest.mark.parametrize("error,absent", [
    ("ERROR: ghcr.io/yebook/tgdl-app:version: not found", True),
    ("manifest unknown", True),
    ("unauthorized: authentication required", False),
    ("lookup ghcr.io: no such host", False),
    ("certificate not found in trust store", False),
    ("429 Too Many Requests", False),
])
def test_only_manifest_absence_is_treated_as_buildable(error: str, absent: bool) -> None:
    response = subprocess.CompletedProcess([], 1, "", error)
    with patch("subprocess.run", return_value=response):
        if absent:
            assert TOOL["remote_digest"]("ghcr.io/example/image:version") is None
        else:
            with pytest.raises(RuntimeError, match="Registry inspection failed"):
                TOOL["remote_digest"]("ghcr.io/example/image:version")


def test_bundle_pins_pair_and_includes_only_deployment_inputs(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / ".env.example").write_text("TGD_UID=1000\n")
    (tmp_path / ".env").write_text("PRIVATE_SECRET=do-not-export\n")
    app = "ghcr.io/yebook/tgdl-app@sha256:" + "a" * 64
    api = "ghcr.io/yebook/tgdl-bot-api@sha256:" + "b" * 64
    output = tmp_path / "bundle"
    native = "ghcr.io/yebook/tgdl-bot-api-native@sha256:" + "e" * 64
    TOOL["bundle"](tmp_path, output, app, api, "c" * 40, "d" * 64, native)
    assert {path.name for path in output.iterdir()} == {
        "compose.yaml", ".env.example", "images.env", "images.json", "START.txt", "SHA256SUMS",
    }
    assert (output / "images.env").read_text() == f"TGD_APP_IMAGE={app}\nTGD_API_IMAGE={api}\n"
    assert json.loads((output / "images.json").read_text())["platform"] == "linux/amd64"
    assert json.loads((output / "images.json").read_text())["native_source"] == native
    for line in (output / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ")
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checksum
    with pytest.raises(ValueError, match="immutable digest"):
        TOOL["bundle"](tmp_path, tmp_path / "bad", "image:latest", api, "c" * 40, "d" * 64, native)


@pytest.mark.parametrize("existing,conflict", [(None, False), ("a", False), ("b", True)])
def test_alias_can_resume_partial_publication_but_never_replace(existing: str | None, conflict: bool) -> None:
    namespace = TOOL["ensure_alias"].__globals__
    digest = None if existing is None else "sha256:" + existing * 64
    with patch.dict(namespace, {"remote_digest": lambda reference: digest}), patch("subprocess.run") as command:
        if conflict:
            with pytest.raises(ValueError, match="conflicting immutable"):
                TOOL["ensure_alias"]("image:0.1.0", "image@sha256:" + "a" * 64)
            command.assert_not_called()
        else:
            TOOL["ensure_alias"]("image:0.1.0", "image@sha256:" + "a" * 64)
            assert command.call_count == (1 if existing is None else 0)


def test_partial_build_failure_resumes_using_completed_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    namespace = TOOL["publish"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yebook/TGDL")
    assert os.environ["GITHUB_REF"] == "refs/heads/main"
    registry: dict[str, str] = {}
    builds: list[str] = []
    fail_app = True
    bundles: list[tuple[object, ...]] = []

    def fake_run(*args: str) -> str:
        return revision if args == ("git", "rev-parse", "HEAD") else ""

    def fake_command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal fail_app
        if args[:3] == ["docker", "buildx", "build"]:
            tag = args[args.index("-t") + 1]
            builds.append(tag)
            if "tgdl-app:" in tag and fail_app:
                fail_app = False
                raise subprocess.CalledProcessError(1, args)
            registry[tag] = "sha256:" + ("a" if "tgdl-app:" in tag else "b") * 64
        elif args[:4] == ["docker", "buildx", "imagetools", "create"]:
            registry[args[args.index("-t") + 1]] = args[-1].rsplit("@", 1)[1]
        return subprocess.CompletedProcess(args, 0, "", "")

    with patch.dict(namespace, {
        "ROOT": tmp_path, "run": fake_run, "api_identity": lambda root: "d" * 64,
        "app_identity": lambda root: "e" * 64, "native_identity": lambda root: "f" * 64,
        "remote_digest": registry.get,
        "bundle": lambda *args: bundles.append(args),
    }), patch("subprocess.run", side_effect=fake_command):
        with pytest.raises(subprocess.CalledProcessError):
            TOOL["publish"](True)
        assert not any(":sha-" in key for key in registry)
        TOOL["publish"](True)
        assert sum("tgdl-bot-api:" in tag for tag in builds) == 1
        assert sum("tgdl-app:" in tag for tag in builds) == 2
        TOOL["publish"](True)
        assert sum("tgdl-bot-api-native:" in tag for tag in builds) == 1
        assert len(builds) == 4
        assert len(bundles) == 2
        registry["ghcr.io/yebook/tgdl-app:0.1.0"] = "sha256:" + "f" * 64
        with pytest.raises(ValueError, match="conflicting immutable"):
            TOOL["publish"](True)


def test_smoke_failure_never_promotes_or_emits_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = TOOL["publish"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yebook/TGDL")
    calls: list[list[str]] = []

    def fake_command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "scripts/docker_acceptance.py" in args:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, "", "")

    with patch.dict(namespace, {
        "ROOT": tmp_path,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "api_identity": lambda root: "d" * 64,
        "app_identity": lambda root: "e" * 64,
        "native_identity": lambda root: "f" * 64,
        "remote_digest": lambda ref: "sha256:" + "a" * 64 if ":input-" in ref else None,
    }), patch("subprocess.run", side_effect=fake_command):
        with pytest.raises(subprocess.CalledProcessError):
            TOOL["publish"](True)
    assert not any("create" in call for call in calls)
    assert not (tmp_path / "deployment-bundle").exists()


@pytest.mark.parametrize("occupied", ["0.1.0", "sha-" + "c" * 40])
def test_immutable_conflict_fails_before_expensive_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, occupied: str,
) -> None:
    namespace = TOOL["publish"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yebook/TGDL")
    with patch.dict(namespace, {
        "ROOT": tmp_path,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "api_identity": lambda root: "d" * 64,
        "app_identity": lambda root: "e" * 64,
        "remote_digest": lambda ref: "sha256:" + "a" * 64 if ref.endswith(":" + occupied) else None,
    }), patch("subprocess.run") as command:
        with pytest.raises(ValueError, match="conflicting immutable"):
            TOOL["publish"](True)
        command.assert_not_called()
    assert not (tmp_path / "deployment-bundle").exists()

@pytest.mark.parametrize("changed", [
    "supervisor.py", "entrypoint.sh", "init-volumes.py", "product-version", "app-only",
])
def test_runtime_and_app_edits_preserve_native_identity(tmp_path: Path, changed: str) -> None:
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    before = TOOL["native_identity"](tmp_path)
    runtime_before = TOOL["api_identity"](tmp_path)
    if changed == "product-version":
        dockerfile = api / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text().replace('image.version="0.1.0"', 'image.version="0.2.0"'))
    elif changed == "app-only":
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend/app.ts").write_text("app-only update")
    else:
        path = api / changed
        path.write_text(path.read_text() + "\n# runtime-only change\n")
    assert TOOL["native_identity"](tmp_path) == before
    assert (TOOL["api_identity"](tmp_path) == runtime_before) == (changed == "app-only")


@pytest.mark.parametrize("changed", [
    "native-proxy.patch", "native-progress.patch", "ProgressCache.h", "native-cache-cleanup.patch",
    "CacheCleanup.h", "upstream-revision", "tdlib-revision", "compiler-toolchain", ".dockerignore",
])
def test_native_inputs_invalidate_native_artifact(tmp_path: Path, changed: str) -> None:
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    before = TOOL["native_identity"](tmp_path)
    if changed in {"upstream-revision", "tdlib-revision", "compiler-toolchain"}:
        path = api / "Dockerfile"
        old, new = {
            "upstream-revision": ("e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1", "a" * 40),
            "tdlib-revision": ("bc9c263e2bfee06aaab41e82db51a103376030bc", "a" * 40),
            "compiler-toolchain": ("build-essential", "new-toolchain"),
        }[changed]
        path.write_text(path.read_text().replace(old, new))
    else:
        path = tmp_path / changed if changed == ".dockerignore" else api / changed
        path.write_text(path.read_text() + "\n# native input changed\n")
    assert TOOL["native_identity"](tmp_path) != before


def test_cached_native_artifact_is_explicit_base_when_runtime_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = TOOL["publish"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yebook/TGDL")
    native = "ghcr.io/yebook/tgdl-bot-api-native@sha256:" + "b" * 64
    registry = {
        "ghcr.io/yebook/tgdl-bot-api-native:input-" + "f" * 64: "sha256:" + "b" * 64,
        "ghcr.io/yebook/tgdl-app:input-" + "e" * 64: "sha256:" + "a" * 64,
    }
    commands: list[list[str]] = []

    def fake_command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:3] == ["docker", "buildx", "build"]:
            registry[args[args.index("-t") + 1]] = "sha256:" + "d" * 64
        elif args[:4] == ["docker", "buildx", "imagetools", "create"]:
            registry[args[args.index("-t") + 1]] = args[-1].rsplit("@", 1)[1]
        return subprocess.CompletedProcess(args, 0, "", "")

    with patch.dict(namespace, {
        "ROOT": tmp_path,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "api_identity": lambda root: "d" * 64, "native_identity": lambda root: "f" * 64,
        "app_identity": lambda root: "e" * 64, "remote_digest": registry.get, "bundle": lambda *args: None,
    }), patch("subprocess.run", side_effect=fake_command):
        TOOL["publish"](False)
    builds = [call for call in commands if call[:3] == ["docker", "buildx", "build"]]
    assert len(builds) == 1
    assert "--target" not in builds[0]
    assert f"TGD_NATIVE_IMAGE={native}" in builds[0]
    assert "TGD_BUILD_JOBS=2" not in builds[0]

@pytest.mark.parametrize("case", ["matching", "missing", "stale", "wrong-parent", "wrong-tdlib", "invalid-format"])
def test_native_manifest_requires_current_sources_and_both_pinned_revisions(tmp_path: Path, case: str) -> None:
    helper = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    source = tmp_path / "source.json"
    source.write_text(json.dumps(helper["source_pins"](tmp_path)))
    manifest = tmp_path / "native.json"
    helper["write_manifest"](tmp_path, manifest, source)
    if case == "matching":
        helper["verify_manifest"](tmp_path, manifest)
        # Runtime-only edits keep the exact compiled native contract.
        (api / "supervisor.py").write_text("runtime-only edit\n")
        helper["verify_manifest"](tmp_path, manifest)
        return
    if case == "missing":
        manifest.unlink()
        expected_error: type[Exception] = FileNotFoundError
    else:
        expected_error = ValueError
        metadata = json.loads(manifest.read_text())
        if case == "stale":
            (api / "CacheCleanup.h").write_text("modified native source\n")
        elif case == "wrong-parent":
            metadata["bot_api_revision"] = "a" * 40
        elif case == "wrong-tdlib":
            metadata["tdlib_revision"] = "a" * 40
        else:
            metadata["format"] = True
        manifest.write_text(json.dumps(metadata))
    with pytest.raises(expected_error):
        helper["verify_manifest"](tmp_path, manifest)


def test_native_manifest_writer_rejects_unexpected_tdlib_checkout(tmp_path: Path) -> None:
    helper = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    shutil.copyfile(ROOT / "deploy/bot-api/Dockerfile", api / "Dockerfile")
    revisions = helper["source_pins"](tmp_path)
    revisions["tdlib_revision"] = "a" * 40
    source = tmp_path / "source.json"
    source.write_text(json.dumps(revisions))
    output = tmp_path / "native.json"
    with pytest.raises(ValueError, match="source pins"):
        helper["write_manifest"](tmp_path, output, source)
    assert not output.exists()


@pytest.mark.parametrize("architecture", ["aarch64", "arm64", "i686"])
def test_native_manifest_rejects_unsupported_architecture_before_io(tmp_path: Path, architecture: str) -> None:
    helper = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
    with patch("platform.machine", return_value=architecture):
        with pytest.raises(ValueError, match="linux/amd64 only"):
            helper["write_manifest"](tmp_path, tmp_path / "native.json", tmp_path / "source.json")
        with pytest.raises(ValueError, match="linux/amd64 only"):
            helper["verify_manifest"](tmp_path, tmp_path / "native.json")
