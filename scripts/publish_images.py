"""Build once in GHCR and mirror verified artifacts to Docker Hub."""
import argparse
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


# The same standard-library helper is invoked by both Dockerfile build-time gates.
_NATIVE = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
dockerfile_sections = _NATIVE["dockerfile_sections"]
native_identity = _NATIVE["native_identity"]


def bundled_artifact_identity(root: Path) -> str:
    """Bind registry reuse to both native inputs and the actual checked-in artifact."""
    files = [f"deploy/bot-api/prebuilt/{name}" for name in ("linux-amd64.tar.gz", "manifest.json")]
    return input_identity(root, files, native_identity(root).encode())


def verify_bundled_native(root: Path) -> None:
    # Validate even when registries already contain every requested artifact.
    with tempfile.TemporaryDirectory(prefix="tgdl-native-check-") as temporary:
        _NATIVE["extract_bundle"](root, root / "deploy/bot-api/prebuilt", Path(temporary))


def api_identity(root: Path) -> str:
    """Runtime identity binds scripts/version to the actual verified Git bundle."""
    _, runtime = dockerfile_sections(root)
    files = [f"deploy/bot-api/{name}" for name in ("entrypoint.sh", "supervisor.py", "init-volumes.py")]
    return input_identity(root, files, (bundled_artifact_identity(root) + "\n" + runtime).encode())


def app_identity(root: Path) -> str:
    tracked = run("git", "ls-files", "backend", "frontend").splitlines()
    return input_identity(root, [".dockerignore", "Dockerfile", "deploy/app-entrypoint.py", *tracked])


def input_identity(root: Path, names: list[str], extra: bytes = b"") -> str:
    digest = hashlib.sha256()
    # Fixed publication metadata is part of artifact identity too.
    digest.update(b"tgdl-publication-v1;linux/amd64;source=https://github.com/yebook/tgdl")
    digest.update(len(extra).to_bytes(8, "big") + extra)
    for relative in sorted(names):
        name = relative.encode()
        content = (root / relative).read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def remote_digest(reference: str) -> str | None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{json .Manifest}}"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        # Authentication, rate limits and network failure must never mean 'absent'.
        blocked = re.search(
            r"unauthorized|denied|insufficient_scope|authentication|toomanyrequests|too many requests|429|"
            r"certificate|tls|no such host|connection|timeout|timed out",
            result.stderr, re.IGNORECASE,
        )
        if not blocked and re.search(r"manifest unknown|: not found(?:\s|$)", result.stderr, re.IGNORECASE):
            return None
        raise RuntimeError(f"Registry inspection failed for {reference}: {result.stderr.strip()}")
    digest = json.loads(result.stdout)["digest"]
    if not DIGEST.fullmatch(digest):
        raise ValueError("Registry returned an invalid manifest digest")
    return digest


def bundle(
    root: Path, destination: Path, app: str, api: str, revision: str, identity: str,
    native_inputs: str, native_artifact: str,
    mirrors: list[str] | None = None,
) -> None:
    for reference in (app, api, *(mirrors or [])):
        if "@" not in reference or not DIGEST.fullmatch(reference.rsplit("@", 1)[1]):
            raise ValueError("Deployment bundles require immutable digest references")
    if mirrors is not None and len(mirrors) != 2:
        raise ValueError("Deployment bundles require exactly two mirrored images")
    if any(not re.fullmatch(r"[0-9a-f]{64}", key) for key in (native_inputs, native_artifact)):
        raise ValueError("Deployment bundles require native source and artifact SHA256 identities")
    destination.mkdir(parents=True, exist_ok=False)
    for filename in ("compose.yaml", ".env.example"):
        shutil.copyfile(root / filename, destination / filename)
    alternate: dict[str, str] = {}
    if mirrors:
        (destination / "images.ghcr.env").write_text(
            f"TGD_APP_IMAGE={app}\nTGD_API_IMAGE={api}\nTGD_PULL_POLICY=missing\n", encoding="utf-8",
        )
        alternate = dict(zip(("app", "bot_api"), (app, api), strict=True))
        app, api = mirrors
    (destination / "images.env").write_text(
        f"TGD_APP_IMAGE={app}\nTGD_API_IMAGE={api}\nTGD_PULL_POLICY=missing\n", encoding="utf-8",
    )
    (destination / "images.json").write_text(json.dumps({
        "revision": revision, "platform": "linux/amd64", "api_input_sha256": identity,
        "app": app, "bot_api": api,
        "native_input_sha256": native_inputs, "native_artifact_sha256": native_artifact,
        "ghcr_alternate": alternate,
    }, indent=2) + "\n", encoding="utf-8")
    (destination / "START.txt").write_text(
        "Copy .env.example to .env and configure directories/UID/GID.\n"
        "docker compose --env-file .env --env-file images.env pull\n"
        "docker compose --env-file .env --env-file images.env up -d --no-build\n"
        "images.env pins the compatible pair. Keep it for upgrades/redeployment.\n"
        "The shared compose.yaml supports source builds too; this bundle uses --no-build.\n"
        "images.env uses public Docker Hub images when mirrored.\n"
        "Optional images.ghcr.env requires public GHCR packages or registry read access.\n"
        "linux/amd64 only. This bundle contains no user configuration or media.\n",
        encoding="utf-8",
    )
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                        for path in sorted(destination.iterdir()))
    (destination / "SHA256SUMS").write_text(checksums, encoding="utf-8")


def ensure_alias(target: str, source: str) -> None:
    existing = remote_digest(target)
    expected = source.rsplit("@", 1)[1]
    if existing == expected:
        return
    if existing is not None:
        raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {target}")
    subprocess.run([
        "docker", "buildx", "imagetools", "create", "--prefer-index=false", "-t", target, source,
    ], check=True)


def mirror_image(target: str, source: str) -> str:
    """Copy all platform/attestation manifests and require byte-identical digests."""
    expected = source.rsplit("@", 1)[1]
    existing = remote_digest(target)
    if existing is not None and existing != expected:
        raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {target}")
    if existing is None:
        subprocess.run([
            "skopeo", "copy", "--all", "--preserve-digests", "--authfile",
            str(Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"),
            f"docker://{source}", f"docker://{target}",
        ], check=True)
    if remote_digest(target) != expected:
        raise RuntimeError(f"Registry mirror digest mismatch: {target}")
    return f"{target.rsplit(':', 1)[0]}@{expected}"


def verify_public(reference: str) -> None:
    """Ignore runner login credentials; deployment images must be anonymously readable."""
    with tempfile.TemporaryDirectory(prefix="tgdl-public-registry-") as directory:
        authfile = Path(directory) / "auth.json"
        authfile.write_text('{"auths":{}}', encoding="utf-8")
        result = subprocess.run([
            "skopeo", "inspect", "--raw", "--no-creds", "--authfile", str(authfile),
            f"docker://{reference}",
        ], check=True, capture_output=True)
    if "sha256:" + hashlib.sha256(result.stdout).hexdigest() != reference.rsplit("@", 1)[1]:
        raise RuntimeError(f"Anonymous registry digest mismatch: {reference}")


def publish(release: bool) -> None:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publication is permitted only from main")
    revision = os.environ["GITHUB_SHA"]
    repository = os.environ["GITHUB_REPOSITORY"].lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or repository != "yebook/tgdl":
        raise ValueError("Unexpected publication repository/revision")
    if run("git", "rev-parse", "HEAD") != revision or run("git", "status", "--porcelain"):
        raise ValueError("Publication requires a clean checkout of the workflow revision")
    run("python", "scripts/version.py", "--check")
    verify_bundled_native(ROOT)
    version = json.loads((ROOT / "backend/app/version.json").read_text())["version"]
    repos = [f"ghcr.io/{repository}-app", f"ghcr.io/{repository}-bot-api"]
    namespace = os.environ.get("DOCKERHUB_USERNAME", "")
    if namespace and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,38}", namespace):
        raise ValueError("Invalid Docker Hub namespace")
    mirror_repos = [f"docker.io/{namespace}/tgdl-{name}" for name in ("app", "bot-api")]
    commit_refs = [f"{repo}:sha-{revision}" for repo in repos]
    release_refs = [f"{repo}:{version}" for repo in repos] if release else []
    identity = api_identity(ROOT)
    identities = [app_identity(ROOT), identity]
    content_refs = [f"{repo}:input-{key}" for repo, key in zip(repos, identities, strict=True)]
    # Content keys make partial failures resumable without overwriting commit tags.
    digests = [remote_digest(reference) for reference in content_refs]
    # Reject occupied immutable aliases before spending time building images.
    # A missing content artifact cannot prove that an existing alias matches.
    for references in (commit_refs, release_refs):
        for target, expected in zip(references, digests, strict=False):
            existing = remote_digest(target)
            if existing is not None and existing != expected:
                raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {target}")
    # Include mirror content/commit/release conflicts in preflight as well.
    if namespace:
        for repo, expected, key in zip(
            mirror_repos, digests, identities, strict=True,
        ):
            tags = [f"input-{key}", f"sha-{revision}"]
            if release:
                tags.append(version)
            for tag in tags:
                existing = remote_digest(f"{repo}:{tag}")
                if existing is not None and existing != expected:
                    raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {repo}:{tag}")
    common = ["--platform", "linux/amd64", "--provenance", "mode=max", "--sbom", "true", "--push"]
    for index in (1, 0):
        if digests[index] is not None:
            continue
        dockerfile = "deploy/bot-api/Dockerfile" if index else "Dockerfile"
        cache = f"type=registry,ref={repos[index]}:buildcache-amd64"
        command = ["docker", "buildx", "build", ".", "-f", dockerfile, "-t", content_refs[index],
                   "--label", f"org.opencontainers.image.source=https://github.com/{repository}",
                   "--label", f"io.tgdl.build.revision={revision}",
                   "--cache-from", cache, "--cache-to", cache + ",mode=max", *common]
        if index:
            command += ["--build-arg", "TGD_NATIVE_IMAGE=bundled-native"]
        else:
            command += ["--label", f"org.opencontainers.image.revision={revision}"]
        subprocess.run(command, check=True)
        digests[index] = remote_digest(content_refs[index])
        if digests[index] is None:
            raise RuntimeError("Publication did not produce a manifest")
    pinned = [f"{repo}@{digest}" for repo, digest in zip(repos, digests, strict=True)]
    # Check the entire pair before assigning aliases. Repeated matching aliases are safe.
    aliases = list(zip(commit_refs, pinned, strict=True)) + list(zip(release_refs, pinned, strict=False))
    for target, source in aliases:
        existing = remote_digest(target)
        if existing is not None and existing != source.rsplit("@", 1)[1]:
            raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {target}")
    # No-secret, offline smoke of the actual production pair before user-facing aliases.
    for source in pinned:
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", source], check=True)
    permissions = ROOT / "deploy/verify-image-permissions.py"
    for kind, source in zip(("app", "api"), pinned, strict=True):
        for user in ("1000:1000", "12345:23456"):
            subprocess.run([
                "docker", "run", "--rm", "--network", "none", "--read-only", "--user", user,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-e", "TGD_DATA_DIR=/tmp/tgd-check",
                "--mount", f"type=bind,src={permissions},dst=/verify-permissions.py,readonly",
                "--entrypoint", "python" if kind == "app" else "python3", source,
                "/verify-permissions.py", kind,
            ], check=True)
    subprocess.run(["python", "scripts/docker_acceptance.py", "--run", "--image", pinned[0]], check=True)
    verifier = ROOT / "deploy/bot-api/verify-runtime-identity.py"
    subprocess.run(["docker", "run", "--rm", "--init", "--network", "none", "--read-only",
                    "--user", "1000:1000", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                    "--tmpfs", "/run/tgd-api-control:rw,noexec,nosuid,size=4m,mode=0700,uid=1000,gid=1000",
                    "--tmpfs", "/var/lib/telegram-bot-api:rw,noexec,nosuid,size=4m,uid=1000,gid=1000",
                    "--mount", f"type=bind,src={verifier},dst=/verify.py,readonly",
                    "--entrypoint", "python3", pinned[1], "/verify.py"], check=True)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                    "--entrypoint", "/usr/local/bin/telegram-bot-api", pinned[1], "--version"], check=True)
    mirrors: list[str] = []
    if namespace:
        for repo, source, key in zip(
            mirror_repos, pinned, identities, strict=True,
        ):
            mirrors.append(mirror_image(f"{repo}:input-{key}", source))
        # Neither registry gets user-facing aliases if public pulls are unavailable.
        for source in mirrors:
            verify_public(source)
        for repo, source in zip(mirror_repos, mirrors, strict=True):
            aliases.append((f"{repo}:sha-{revision}", source))
            if release:
                aliases.append((f"{repo}:{version}", source))
    for target, source in aliases:
        existing = remote_digest(target)
        if existing is not None and existing != source.rsplit("@", 1)[1]:
            raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {target}")
    for target, source in aliases:
        ensure_alias(target, source)
    bundle(ROOT, ROOT / "deployment-bundle", pinned[0], pinned[1], revision, identity,
           native_identity(ROOT), bundled_artifact_identity(ROOT), mirrors or None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="Also create numeric tags (never overwrite)")
    publish(parser.parse_args().release)
