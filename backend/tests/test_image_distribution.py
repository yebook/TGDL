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
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", api.parent / "verify-image-permissions.py")
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    (api / "prebuilt").mkdir(exist_ok=True)
    (api / "prebuilt/linux-amd64.tar.gz").write_bytes(b"identity archive fixture")
    (api / "prebuilt/manifest.json").write_text("identity manifest fixture")
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


@pytest.mark.parametrize("filename", ["linux-amd64.tar.gz", "manifest.json"])
def test_changed_bundle_changes_artifact_and_api_keys_without_self_reference(tmp_path: Path, filename: str) -> None:
    source = tmp_path / "deploy/bot-api"
    shutil.copytree(ROOT / "deploy/bot-api", source, ignore=shutil.ignore_patterns("prebuilt", "__pycache__"))
    shutil.copyfile(ROOT / ".dockerignore", tmp_path / ".dockerignore")
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", tmp_path / "deploy/verify-image-permissions.py")
    (source / "prebuilt").mkdir()
    for name in ("linux-amd64.tar.gz", "manifest.json"):
        (source / "prebuilt" / name).write_bytes(b"original artifact")
    before = [TOOL[name](tmp_path) for name in ("native_identity", "bundled_artifact_identity", "api_identity")]
    (source / "prebuilt" / filename).write_bytes(b"changed artifact")
    after = [TOOL[name](tmp_path) for name in ("native_identity", "bundled_artifact_identity", "api_identity")]
    assert before[0] == after[0]  # Native source identity never includes its own output.
    assert before[1] != after[1]
    assert before[2] != after[2]


def test_invalid_local_bundle_is_rejected_before_any_registry_cache_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "c" * 40
    for name, value in {
        "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": revision, "GITHUB_REPOSITORY": "yebook/TGDL",
    }.items():
        monkeypatch.setenv(name, value)
    namespace = TOOL["_publish_archived"].__globals__

    def reject_bundle(*args: object) -> None:
        raise ValueError("Bundled native binary is stale")

    with patch.dict(namespace, {
        "ROOT": tmp_path,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
    }), patch.dict(namespace["_NATIVE"], {"extract_bundle": reject_bundle}), patch.dict(namespace, {
        "remote_digest": lambda ref: pytest.fail("Registry cache must not hide stale bundle"),
    }):
        with pytest.raises(ValueError, match="stale"):
            TOOL["_publish_archived"](False)


@pytest.mark.parametrize("changed", ["source", "archive"])
def test_actual_source_or_archive_drift_rejected_before_registry_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    api = tmp_path / "deploy/bot-api"
    shutil.copytree(ROOT / "deploy/bot-api", api, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / ".dockerignore", tmp_path / ".dockerignore")
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", tmp_path / "deploy/verify-image-permissions.py")
    if changed == "source":
        with (api / "CacheCleanup.h").open("ab") as stream:
            stream.write(b"\n// changed native source\n")
        error = "stale"
    else:
        with (api / "prebuilt/linux-amd64.tar.gz").open("ab") as stream:
            stream.write(b"changed native archive")
        error = "archive checksum mismatch"
    revision = "c" * 40
    for name, value in {
        "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": revision, "GITHUB_REPOSITORY": "yebook/TGDL",
    }.items():
        monkeypatch.setenv(name, value)
    namespace = TOOL["_publish_archived"].__globals__
    with patch.dict(namespace, {
        "ROOT": tmp_path,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "remote_digest": lambda ref: pytest.fail("Registry reuse must not hide actual bundle drift"),
    }):
        with pytest.raises(ValueError, match=error):
            TOOL["_publish_archived"](True)


@pytest.mark.parametrize("error,absent", [
    ("ERROR: ghcr.io/yebook/tgdl-app:version: not found", True),
    ("manifest unknown", True),
    ("unauthorized: authentication required", False),
    ("lookup ghcr.io: no such host", False),
    ("certificate not found in trust store", False),
    ("429 Too Many Requests", False),
    ("insufficient_scope: authorization failed; docker.io/example/repo: not found", False),
    ("unauthorized: manifest unknown", False),
    ("denied: requested access; manifest unknown", False),
    ("connection refused; example/image: not found", False),
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
    TOOL["_bundle_archived"](tmp_path, output, app, api, "c" * 40, "d" * 64, "e" * 64, "f" * 64)
    assert {path.name for path in output.iterdir()} == {
        "compose.yaml", ".env.example", "images.env", "images.json", "START.txt", "SHA256SUMS",
    }
    assert (output / "images.env").read_text() == (
        f"TGD_APP_IMAGE={app}\nTGD_API_IMAGE={api}\nTGD_PULL_POLICY=missing\n"
    )
    assert json.loads((output / "images.json").read_text())["platform"] == "linux/amd64"
    metadata = json.loads((output / "images.json").read_text())
    assert metadata["native_input_sha256"] == "e" * 64
    assert metadata["native_artifact_sha256"] == "f" * 64
    assert "native_source" not in metadata
    for line in (output / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ")
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checksum
    second = tmp_path / "same-inputs"
    TOOL["_bundle_archived"](tmp_path, second, app, api, "c" * 40, "d" * 64, "e" * 64, "f" * 64)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    with pytest.raises(ValueError, match="immutable digest"):
        TOOL["_bundle_archived"](tmp_path, tmp_path / "bad", "image:latest", api,
                       "c" * 40, "d" * 64, "e" * 64, "f" * 64)


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

    namespace = TOOL["_publish_archived"].__globals__
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
        "ROOT": tmp_path, "verify_bundled_native": lambda root: None,
        "bundled_artifact_identity": lambda root: "f" * 64, "run": fake_run, "api_identity": lambda root: "d" * 64,
        "app_identity": lambda root: "e" * 64, "native_identity": lambda root: "f" * 64,
        "remote_digest": registry.get,
        "_bundle_archived": lambda *args: bundles.append(args),
    }), patch("subprocess.run", side_effect=fake_command):
        with pytest.raises(subprocess.CalledProcessError):
            TOOL["_publish_archived"](True)
        assert not any(":sha-" in key for key in registry)
        TOOL["_publish_archived"](True)
        assert sum("tgdl-bot-api:" in tag for tag in builds) == 1
        assert sum("tgdl-app:" in tag for tag in builds) == 2
        TOOL["_publish_archived"](True)
        assert len(builds) == 3  # One API build and failed+retried app, no standalone native build.
        assert len(bundles) == 2
        registry["ghcr.io/yebook/tgdl-app:0.1.0"] = "sha256:" + "f" * 64
        with pytest.raises(ValueError, match="conflicting immutable"):
            TOOL["_publish_archived"](True)


def test_smoke_failure_never_promotes_or_emits_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = TOOL["_publish_archived"].__globals__
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
        "ROOT": tmp_path, "verify_bundled_native": lambda root: None,
        "bundled_artifact_identity": lambda root: "f" * 64,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "api_identity": lambda root: "d" * 64,
        "app_identity": lambda root: "e" * 64,
        "native_identity": lambda root: "f" * 64,
        "remote_digest": lambda ref: "sha256:" + "a" * 64 if ":input-" in ref else None,
    }), patch("subprocess.run", side_effect=fake_command):
        with pytest.raises(subprocess.CalledProcessError):
            TOOL["_publish_archived"](True)
    assert not any("create" in call for call in calls)
    assert not (tmp_path / "deployment-bundle").exists()


@pytest.mark.parametrize("occupied", ["0.1.0", "sha-" + "c" * 40])
def test_immutable_conflict_fails_before_expensive_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, occupied: str,
) -> None:
    namespace = TOOL["_publish_archived"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yebook/TGDL")
    with patch.dict(namespace, {
        "ROOT": tmp_path, "verify_bundled_native": lambda root: None,
        "bundled_artifact_identity": lambda root: "f" * 64,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "api_identity": lambda root: "d" * 64,
        "app_identity": lambda root: "e" * 64,
        "remote_digest": lambda ref: "sha256:" + "a" * 64 if ref.endswith(":" + occupied) else None,
    }), patch("subprocess.run") as command:
        with pytest.raises(ValueError, match="conflicting immutable"):
            TOOL["_publish_archived"](True)
        command.assert_not_called()
    assert not (tmp_path / "deployment-bundle").exists()

@pytest.mark.parametrize("changed", [
    "supervisor.py", "entrypoint.sh", "init-volumes.py", "product-version", "app-only",
])
def test_runtime_and_app_edits_preserve_native_identity(tmp_path: Path, changed: str) -> None:
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", api.parent / "verify-image-permissions.py")
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    (api / "prebuilt").mkdir(exist_ok=True)
    (api / "prebuilt/linux-amd64.tar.gz").write_bytes(b"identity archive fixture")
    (api / "prebuilt/manifest.json").write_text("identity manifest fixture")
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
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", api.parent / "verify-image-permissions.py")
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    (api / "prebuilt").mkdir(exist_ok=True)
    (api / "prebuilt/linux-amd64.tar.gz").write_bytes(b"identity archive fixture")
    (api / "prebuilt/manifest.json").write_text("identity manifest fixture")
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


def test_api_build_uses_git_bundle_without_consulting_standalone_native_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = TOOL["_publish_archived"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yebook/TGDL")
    registry = {
        "ghcr.io/yebook/tgdl-bot-api-native:input-" + "f" * 64: "sha256:" + "b" * 64,
        "ghcr.io/yebook/tgdl-app:input-" + "e" * 64: "sha256:" + "a" * 64,
    }
    commands: list[list[str]] = []
    inspected: list[str] = []

    def inspect(reference: str) -> str | None:
        inspected.append(reference)
        assert "-bot-api-native" not in reference
        return registry.get(reference)

    def fake_command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:3] == ["docker", "buildx", "build"]:
            registry[args[args.index("-t") + 1]] = "sha256:" + "d" * 64
        elif args[:4] == ["docker", "buildx", "imagetools", "create"]:
            registry[args[args.index("-t") + 1]] = args[-1].rsplit("@", 1)[1]
        return subprocess.CompletedProcess(args, 0, "", "")

    with patch.dict(namespace, {
        "ROOT": tmp_path, "verify_bundled_native": lambda root: None,
        "bundled_artifact_identity": lambda root: "f" * 64,
        "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "api_identity": lambda root: "d" * 64, "native_identity": lambda root: "f" * 64,
        "app_identity": lambda root: "e" * 64, "remote_digest": inspect, "_bundle_archived": lambda *args: None,
    }), patch("subprocess.run", side_effect=fake_command):
        TOOL["_publish_archived"](False)
    builds = [call for call in commands if call[:3] == ["docker", "buildx", "build"]]
    assert len(builds) == 1
    assert "--target" not in builds[0]
    assert "TGD_NATIVE_IMAGE=bundled-native" in builds[0]
    assert "TGD_BUILD_JOBS=2" not in builds[0]
    assert inspected
    assert not any("-bot-api-native" in argument for command in commands for argument in command)

@pytest.mark.parametrize("case", ["matching", "missing", "stale", "wrong-parent", "wrong-tdlib", "invalid-format"])
def test_native_manifest_requires_current_sources_and_both_pinned_revisions(tmp_path: Path, case: str) -> None:
    helper = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", api.parent / "verify-image-permissions.py")
    (tmp_path / ".dockerignore").write_text(".runtime/\n")
    for path in (ROOT / "deploy/bot-api").iterdir():
        if path.is_file():
            shutil.copyfile(path, api / path.name)
    (api / "prebuilt").mkdir(exist_ok=True)
    (api / "prebuilt/linux-amd64.tar.gz").write_bytes(b"identity archive fixture")
    (api / "prebuilt/manifest.json").write_text("identity manifest fixture")
    source = tmp_path / "source.json"
    source.write_text(json.dumps(helper["source_pins"](tmp_path)))
    manifest = tmp_path / "native.json"
    binary = tmp_path / "binary"
    binary.write_bytes(b"synthetic native binary")
    helper["write_manifest"](tmp_path, manifest, source, binary)
    if case == "matching":
        helper["verify_manifest"](tmp_path, manifest, binary)
        # Runtime-only edits keep the exact compiled native contract.
        (api / "supervisor.py").write_text("runtime-only edit\n")
        helper["verify_manifest"](tmp_path, manifest, binary)
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
        helper["verify_manifest"](tmp_path, manifest, binary)


def test_native_manifest_writer_rejects_unexpected_tdlib_checkout(tmp_path: Path) -> None:
    helper = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
    api = tmp_path / "deploy/bot-api"
    api.mkdir(parents=True)
    shutil.copyfile(ROOT / "deploy/verify-image-permissions.py", api.parent / "verify-image-permissions.py")
    shutil.copyfile(ROOT / "deploy/bot-api/Dockerfile", api / "Dockerfile")
    revisions = helper["source_pins"](tmp_path)
    revisions["tdlib_revision"] = "a" * 40
    source = tmp_path / "source.json"
    source.write_text(json.dumps(revisions))
    output = tmp_path / "native.json"
    with pytest.raises(ValueError, match="source pins"):
        helper["write_manifest"](tmp_path, output, source, tmp_path / "binary")
    assert not output.exists()


@pytest.mark.parametrize("architecture", ["aarch64", "arm64", "i686"])
def test_native_manifest_rejects_unsupported_architecture_before_io(tmp_path: Path, architecture: str) -> None:
    helper = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
    with patch("platform.machine", return_value=architecture):
        with pytest.raises(ValueError, match="linux/amd64 only"):
            helper["write_manifest"](tmp_path, tmp_path / "native.json", tmp_path / "source.json", tmp_path / "binary")
        with pytest.raises(ValueError, match="linux/amd64 only"):
            helper["verify_manifest"](tmp_path, tmp_path / "native.json", tmp_path / "binary")


@pytest.mark.parametrize("state", ["missing", "matching", "conflict", "wrong-copy"])
def test_mirror_requires_exact_digest_and_never_rebuilds(state: str) -> None:
    namespace = TOOL["mirror_image"].__globals__
    digest = "sha256:" + "a" * 64
    target = "docker.io/example/tgdl-app:input-abc"
    source = "ghcr.io/yebook/tgdl-app@" + digest
    existing = digest if state == "matching" else "sha256:" + "b" * 64 if state == "conflict" else None
    values = [existing, "sha256:" + "c" * 64 if state == "wrong-copy" else digest]
    with patch.dict(namespace, {"remote_digest": lambda ref: values.pop(0)}), patch("subprocess.run") as command:
        if state == "conflict":
            with pytest.raises(ValueError, match="conflicting immutable"):
                TOOL["mirror_image"](target, source)
            command.assert_not_called()
        elif state == "wrong-copy":
            with pytest.raises(RuntimeError, match="mirror digest mismatch"):
                TOOL["mirror_image"](target, source)
        else:
            assert TOOL["mirror_image"](target, source) == "docker.io/example/tgdl-app@" + digest
            assert command.call_count == (0 if state == "matching" else 1)
        if command.called:
            arguments = command.call_args.args[0]
            assert arguments[:5] == ["skopeo", "copy", "--all", "--preserve-digests", "--authfile"]
            assert arguments[-2:] == [f"docker://{source}", f"docker://{target}"]


def test_anonymous_verification_uses_no_runner_credentials() -> None:
    raw = b'{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()

    def inspect(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "--no-creds" in arguments
        authfile = Path(arguments[arguments.index("--authfile") + 1])
        assert json.loads(authfile.read_text()) == {"auths": {}}
        return subprocess.CompletedProcess(arguments, 0, raw, b"")

    with patch("subprocess.run", side_effect=inspect):
        TOOL["verify_public"]("docker.io/example/tgdl-app@" + digest)
        with pytest.raises(RuntimeError, match="Anonymous registry digest mismatch"):
            TOOL["verify_public"]("docker.io/example/tgdl-app@sha256:" + "a" * 64)


def test_mirror_uses_explicit_docker_login_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = TOOL["mirror_image"].__globals__
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    digest = "sha256:" + "a" * 64
    values = [None, digest]
    with patch.dict(namespace, {"remote_digest": lambda ref: values.pop(0)}), patch("subprocess.run") as command:
        TOOL["mirror_image"]("docker.io/example/tgdl-app:input-abc", "ghcr.io/example/image@" + digest)
    arguments = command.call_args.args[0]
    assert arguments[arguments.index("--authfile") + 1] == str(tmp_path / "config.json")


def test_bundle_includes_independently_pinned_dockerhub_pair(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / ".env.example").write_text("TGD_UID=1000\n")
    primary = [f"ghcr.io/yebook/tgdl-{name}@sha256:" + "a" * 64 for name in ("app", "bot-api")]
    mirrors = [reference.replace("ghcr.io/yebook/", "docker.io/example/") for reference in primary]
    output = tmp_path / "bundle"
    TOOL["_bundle_archived"](tmp_path, output, *primary, "c" * 40, "d" * 64, "e" * 64, "f" * 64, mirrors)
    assert (output / "images.env").read_text() == (
        f"TGD_APP_IMAGE={mirrors[0]}\nTGD_API_IMAGE={mirrors[1]}\nTGD_PULL_POLICY=missing\n"
    )
    assert (output / "images.ghcr.env").read_text() == (
        f"TGD_APP_IMAGE={primary[0]}\nTGD_API_IMAGE={primary[1]}\nTGD_PULL_POLICY=missing\n"
    )
    metadata = json.loads((output / "images.json").read_text())
    assert metadata["native_input_sha256"] == "e" * 64
    assert metadata["native_artifact_sha256"] == "f" * 64
    assert metadata["ghcr_alternate"] == dict(zip(("app", "bot_api"), primary, strict=True))
    assert "native_source" not in metadata
    expected_names = {
        "compose.yaml", ".env.example", "images.env", "images.ghcr.env", "images.json", "START.txt",
    }
    assert {path.name for path in output.iterdir()} == expected_names | {"SHA256SUMS"}
    checksums = dict(line.split("  ")[::-1] for line in (output / "SHA256SUMS").read_text().splitlines())
    assert set(checksums) == expected_names
    for name, checksum in checksums.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize("failure", ["none", "mirror", "private", "collision", "permission"])
def test_dual_registry_publication_resumes_without_rebuilding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    namespace = TOOL["_publish_archived"].__globals__
    metadata = tmp_path / "backend/app/version.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('{"version":"0.1.0"}')
    revision = "c" * 40
    for name, value in {
        "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": revision,
        "GITHUB_REPOSITORY": "yebook/TGDL", "DOCKERHUB_USERNAME": "example",
    }.items():
        monkeypatch.setenv(name, value)
    registry: dict[str, str] = {}
    if failure == "collision":
        registry["docker.io/example/tgdl-app:0.1.0"] = "sha256:" + "9" * 64
    calls: list[list[str]] = []
    bundles: list[tuple[object, ...]] = []
    public: list[str] = []
    pending_failure = failure

    def inspect(reference: str) -> str | None:
        assert "-bot-api-native" not in reference  # Covers preflight and every retry/cache lookup.
        return registry.get(reference)

    def command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal pending_failure
        calls.append(args)
        if args[:3] == ["docker", "buildx", "build"]:
            tag = args[args.index("-t") + 1]
            registry[tag] = "sha256:" + hashlib.sha256(tag.encode()).hexdigest()
        elif args[:2] == ["skopeo", "copy"]:
            if pending_failure == "mirror" and "tgdl-bot-api:input" in args[-1]:
                pending_failure = "none"
                raise subprocess.CalledProcessError(1, args)
            registry[args[-1].removeprefix("docker://")] = args[-2].rsplit("@", 1)[1]
        elif args[:4] == ["docker", "buildx", "imagetools", "create"]:
            registry[args[args.index("-t") + 1]] = args[-1].rsplit("@", 1)[1]
        elif "/verify-permissions.py" in args and pending_failure == "permission":
            pending_failure = "none"
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def check_public(reference: str) -> None:
        nonlocal pending_failure
        public.append(reference)
        if pending_failure == "private":
            pending_failure = "none"
            raise RuntimeError("repository is private")

    with patch.dict(namespace, {
        "ROOT": tmp_path, "verify_bundled_native": lambda root: None,
        "bundled_artifact_identity": lambda root: "f" * 64, "run": lambda *args: revision if args == ("git", "rev-parse", "HEAD") else "",
        "app_identity": lambda root: "e" * 64, "api_identity": lambda root: "d" * 64,
        "native_identity": lambda root: "f" * 64, "remote_digest": inspect,
        "_bundle_archived": lambda *args: bundles.append(args), "verify_public": check_public,
    }), patch("subprocess.run", side_effect=command):
        if failure != "none":
            with pytest.raises((ValueError, RuntimeError, subprocess.CalledProcessError)):
                TOOL["_publish_archived"](True)
            assert not bundles
            assert not any(":sha-" in reference for reference in registry)
            if failure == "collision":
                assert not calls  # No image build or other expensive subprocess.
                return
        TOOL["_publish_archived"](True)
        TOOL["_publish_archived"](True)
    builds = [args for args in calls if args[:3] == ["docker", "buildx", "build"]]
    assert len(builds) == 2  # Only final app/API images, each built once including retries.
    assert len([args for args in calls if args[:2] == ["skopeo", "copy"]]) == (3 if failure == "mirror" else 2)
    assert len(bundles) == 2 and len(bundles[0][-1]) == 2
    assert len(set(public)) == 2
    assert len(public) == (5 if failure == "private" else 4)  # Two public checks per complete run.
    assert not any("-bot-api-native" in argument for command in calls for argument in command)
    for name in ("app", "bot-api"):
        assert f"docker.io/example/tgdl-{name}:0.1.0" in registry


def test_active_remote_publication_and_bundles_fail_before_any_io(tmp_path: Path) -> None:
    with patch("subprocess.run") as command:
        with pytest.raises(RuntimeError, match="temporarily disabled"):
            TOOL["publish"](False)
        with pytest.raises(RuntimeError, match="temporarily disabled"):
            TOOL["bundle"](ROOT, tmp_path / "bundle")
        command.assert_not_called()
    assert not (tmp_path / "bundle").exists()
