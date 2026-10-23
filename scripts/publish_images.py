"""Manual GHCR publication helper. Never reads application settings/runtime data."""
import argparse
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


# The same standard-library helper is invoked by both Dockerfile build-time gates.
_NATIVE = runpy.run_path(str(ROOT / "deploy/bot-api/native_identity.py"))
dockerfile_sections = _NATIVE["dockerfile_sections"]
native_identity = _NATIVE["native_identity"]


def api_identity(root: Path) -> str:
    """Runtime scripts/version reuse the independently compiled native input identity."""
    _, runtime = dockerfile_sections(root)
    files = [f"deploy/bot-api/{name}" for name in ("entrypoint.sh", "supervisor.py", "init-volumes.py")]
    return input_identity(root, files, (native_identity(root) + "\n" + runtime).encode())


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
        if re.search(r"manifest unknown|: not found(?:\s|$)", result.stderr, re.IGNORECASE):
            return None
        raise RuntimeError(f"Registry inspection failed for {reference}: {result.stderr.strip()}")
    digest = json.loads(result.stdout)["digest"]
    if not DIGEST.fullmatch(digest):
        raise ValueError("Registry returned an invalid manifest digest")
    return digest


def bundle(root: Path, destination: Path, app: str, api: str, revision: str, identity: str, native: str) -> None:
    for reference in (app, api, native):
        if "@" not in reference or not DIGEST.fullmatch(reference.rsplit("@", 1)[1]):
            raise ValueError("Deployment bundles require immutable digest references")
    destination.mkdir(parents=True, exist_ok=False)
    for filename in ("compose.yaml", ".env.example"):
        shutil.copyfile(root / filename, destination / filename)
    (destination / "images.env").write_text(f"TGD_APP_IMAGE={app}\nTGD_API_IMAGE={api}\n", encoding="utf-8")
    (destination / "images.json").write_text(json.dumps({
        "revision": revision, "platform": "linux/amd64", "api_input_sha256": identity,
        "app": app, "bot_api": api, "native_source": native,
    }, indent=2) + "\n", encoding="utf-8")
    (destination / "START.txt").write_text(
        "Copy .env.example to .env and configure directories/UID/GID.\n"
        "docker compose --env-file .env --env-file images.env pull\n"
        "docker compose --env-file .env --env-file images.env up -d --no-build\n"
        "images.env pins the compatible pair. Keep it for upgrades/redeployment.\n"
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
    subprocess.run(["docker", "buildx", "imagetools", "create", "-t", target, source], check=True)


def ensure_native(root: Path, repository: str, revision: str, common: list[str]) -> str:
    repo = f"ghcr.io/{repository}-bot-api-native"
    reference = f"{repo}:input-{native_identity(root)}"
    digest = remote_digest(reference)
    if digest is None:
        cache = f"type=registry,ref={repo}:buildcache-amd64"
        subprocess.run([
            "docker", "buildx", "build", ".", "-f", "deploy/bot-api/Dockerfile",
            "--target", "native", "-t", reference, "--build-arg", "TGD_BUILD_JOBS=2",
            "--label", f"org.opencontainers.image.source=https://github.com/{repository}",
            "--label", f"io.tgdl.build.revision={revision}",
            "--cache-from", cache, "--cache-to", cache + ",mode=max", *common,
        ], check=True)
        digest = remote_digest(reference)
    if digest is None:
        raise RuntimeError("Native publication did not produce a manifest")
    return f"{repo}@{digest}"


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
    version = json.loads((ROOT / "backend/app/version.json").read_text())["version"]
    repos = [f"ghcr.io/{repository}-app", f"ghcr.io/{repository}-bot-api"]
    commit_refs = [f"{repo}:sha-{revision}" for repo in repos]
    release_refs = [f"{repo}:{version}" for repo in repos] if release else []
    identity = api_identity(ROOT)
    identities = [app_identity(ROOT), identity]
    content_refs = [f"{repo}:input-{key}" for repo, key in zip(repos, identities, strict=True)]
    # Content keys make partial failures resumable without overwriting commit tags.
    digests = [remote_digest(reference) for reference in content_refs]
    # Reject occupied immutable aliases before spending time compiling TDLib.
    # A missing content artifact cannot prove that an existing alias matches.
    for references in (commit_refs, release_refs):
        for target, expected in zip(references, digests, strict=False):
            existing = remote_digest(target)
            if existing is not None and existing != expected:
                raise ValueError(f"Refusing to overwrite conflicting immutable image tag: {target}")
    common = ["--platform", "linux/amd64", "--provenance", "mode=max", "--sbom", "true", "--push"]
    native = ensure_native(ROOT, repository, revision, common)
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
            command += ["--build-arg", f"TGD_NATIVE_IMAGE={native}"]
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
    for target, source in aliases:
        ensure_alias(target, source)
    bundle(ROOT, ROOT / "deployment-bundle", pinned[0], pinned[1], revision, identity, native)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="Also create numeric tags (never overwrite)")
    publish(parser.parse_args().release)
