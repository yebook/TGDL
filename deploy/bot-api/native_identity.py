"""Shared build-time identity for the project's patched native Bot API/TDLib artifact.

Uses only the supplied source tree and a standard-library Python interpreter; no Git,
application state, secrets, network or product version is required at verification time.
"""
import argparse
import gzip
import hashlib
import io
import json
import platform
import re
import tarfile
from pathlib import Path

PLATFORM = "linux/amd64"
MARKER = "# TGD_NATIVE_INPUTS_END"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
INPUTS = (
    ".dockerignore", "deploy/bot-api/native_identity.py",
    "deploy/bot-api/native-proxy.patch", "deploy/bot-api/native-progress.patch",
    "deploy/bot-api/ProgressCache.h", "deploy/bot-api/native-cache-cleanup.patch",
    "deploy/bot-api/CacheCleanup.h",
)


def require_supported_architecture() -> None:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("TGDL native artifacts currently support linux/amd64 only")


def dockerfile_sections(root: Path) -> tuple[str, str]:
    text = (root / "deploy/bot-api/Dockerfile").read_text(encoding="utf-8")
    if text.count(MARKER) != 1:
        raise ValueError("Expected one explicit native/runtime Dockerfile boundary")
    native, runtime = text.split(MARKER)
    native = re.sub(r"(?m)^ARG TGD_NATIVE_IMAGE=[^\n]*\n", "", native)
    return native, runtime


def native_identity(root: Path) -> str:
    native, _ = dockerfile_sections(root)
    digest = hashlib.sha256()
    digest.update(b"tgdl-native-v1;linux/amd64;source=https://github.com/yebook/tgdl")
    pieces = [("Dockerfile.native", native.encode()), *(
        (name, (root / name).read_bytes()) for name in INPUTS
    )]
    for name, content in pieces:
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def source_pins(root: Path) -> dict[str, str]:
    native, _ = dockerfile_sections(root)
    result = {}
    for key, argument in (("bot_api_revision", "TELEGRAM_BOT_API_REV"), ("tdlib_revision", "TELEGRAM_TDLIB_REV")):
        pins = set(re.findall(rf"(?m)^ARG {argument}=([0-9a-f]{{40}})$", native))
        if len(pins) != 1:
            raise ValueError(f"Expected one consistent pinned source revision: {argument}")
        result[key] = pins.pop()
    return result


def source_revisions(root: Path, value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"bot_api_revision", "tdlib_revision"}:
        raise ValueError("Invalid native source revision metadata")
    if any(not isinstance(item, str) or not SHA40.fullmatch(item) for item in value.values()):
        raise ValueError("Invalid native source revision metadata")
    if value != source_pins(root):
        raise ValueError("Native Bot API/TDLib revisions do not match the source pins")
    return {"bot_api_revision": value["bot_api_revision"], "tdlib_revision": value["tdlib_revision"]}


def write_manifest(root: Path, output: Path, source: Path, binary: Path) -> None:
    require_supported_architecture()
    revisions = source_revisions(root, json.loads(source.read_text(encoding="utf-8")))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "format": 2, "platform": PLATFORM, "native_input_sha256": native_identity(root), **revisions,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }, indent=2) + "\n", encoding="utf-8")


def verify_manifest(root: Path, manifest: Path, binary: Path) -> None:
    require_supported_architecture()
    value = json.loads(manifest.read_text(encoding="utf-8"))
    expected_fields = {"format", "platform", "native_input_sha256", "bot_api_revision", "tdlib_revision", "binary_sha256"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError("Missing or invalid native build manifest; rebuild the matching project-native image")
    if type(value["format"]) is not int or value["format"] != 2 or value["platform"] != PLATFORM:
        raise ValueError("Unsupported native build manifest/platform")
    identity = value["native_input_sha256"]
    if not isinstance(identity, str) or not SHA256.fullmatch(identity) or identity != native_identity(root):
        raise ValueError("Native image does not match current source inputs; select or rebuild the matching native image")
    source_revisions(root, {key: value[key] for key in ("bot_api_revision", "tdlib_revision")})
    if value["binary_sha256"] != hashlib.sha256(binary.read_bytes()).hexdigest():
        raise ValueError("Native binary checksum does not match its build manifest")


BUNDLE_MEMBERS = {
    "bin/telegram-bot-api", "licenses/LICENSE_1_0.txt", "licenses/tdlib/LICENSE_1_0.txt",
    "share/tgdl-source.json", "share/tgdl-native.json",
}


def pack_bundle(root: Path, source: Path, output: Path) -> None:
    """Package only a previously verified native target, without any runtime data."""
    verify_manifest(root, source / "share/tgdl-native.json", source / "bin/telegram-bot-api")
    source_revisions(root, json.loads((source / "share/tgdl-source.json").read_text()))
    output.mkdir(parents=True, exist_ok=True)
    checksums = {}
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(BUNDLE_MEMBERS):
            path = source / name
            if not path.is_file() or path.is_symlink():
                raise ValueError("Missing or unsafe native bundle file")
            data = path.read_bytes()
            checksums[name] = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.startswith("bin/") else 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    compressed = gzip.compress(archive.getvalue(), compresslevel=6, mtime=0)
    (output / "linux-amd64.tar.gz").write_bytes(compressed)
    metadata = {
        "format": 1, "platform": PLATFORM, "native_input_sha256": native_identity(root),
        **source_pins(root), "archive_sha256": hashlib.sha256(compressed).hexdigest(), "files": checksums,
    }
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_bundle(root: Path, bundle: Path, destination: Path) -> None:
    """Fail closed before writing anything if provenance, archive or files differ."""
    require_supported_architecture()
    value = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    fields = {"format", "platform", "native_input_sha256", "bot_api_revision", "tdlib_revision", "archive_sha256", "files"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid bundled native manifest")
    if type(value["format"]) is not int or value["format"] != 1 or value["platform"] != PLATFORM:
        raise ValueError("Unsupported bundled native manifest/platform")
    if value["native_input_sha256"] != native_identity(root):
        raise ValueError("Bundled native binary is stale: rebuild and regenerate it for the current native inputs")
    source_revisions(root, {key: value[key] for key in ("bot_api_revision", "tdlib_revision")})
    checksums = value["files"]
    if not isinstance(checksums, dict) or set(checksums) != BUNDLE_MEMBERS:
        raise ValueError("Unexpected bundled native files")
    path = bundle / "linux-amd64.tar.gz"
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Native bundle exceeds size limit")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != value["archive_sha256"]:
        raise ValueError("Bundled native archive checksum mismatch")
    verified = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if member.name not in BUNDLE_MEMBERS or member.name in verified or not member.isfile():
                raise ValueError("Unsafe native archive member")
            limit = 100 * 1024 * 1024 if member.name.startswith("bin/") else 1024 * 1024
            if member.size < 1 or member.size > limit:
                raise ValueError("Invalid native archive member size")
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("Invalid native archive member")
            content = stream.read(limit + 1)
            if len(content) != member.size or hashlib.sha256(content).hexdigest() != checksums[member.name]:
                raise ValueError("Bundled native file checksum mismatch")
            verified[member.name] = content
    if set(verified) != BUNDLE_MEMBERS:
        raise ValueError("Incomplete native archive")
    # Check embedded provenance too, before writing any file.
    inner = json.loads(verified["share/tgdl-native.json"])
    expected = {key: value[key] for key in ("format", "platform", "native_input_sha256", "bot_api_revision", "tdlib_revision")}
    expected["format"] = 2
    expected["binary_sha256"] = checksums["bin/telegram-bot-api"]
    if inner != expected:
        raise ValueError("Bundled native provenance mismatch")
    source_revisions(root, json.loads(verified["share/tgdl-source.json"]))
    # Validate every target before writing: never traverse an existing symlink.
    for name in verified:
        target = destination / name
        for candidate in (target, *target.parents):
            if candidate.is_symlink():
                raise ValueError("Unsafe native destination symlink")
    for name, content in verified.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if name.startswith("bin/") else 0o644)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    write = subparsers.add_parser("write")
    write.add_argument("--root", type=Path, required=True)
    write.add_argument("--output", type=Path, required=True)
    write.add_argument("--source", type=Path, required=True)
    write.add_argument("--binary", type=Path, default=Path("/usr/local/bin/telegram-bot-api"))
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--binary", type=Path, default=Path("/usr/local/bin/telegram-bot-api"))
    extract = subparsers.add_parser("extract")
    extract.add_argument("--root", type=Path, required=True)
    extract.add_argument("--bundle", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "write":
            write_manifest(args.root, args.output, args.source, args.binary)
        elif args.action == "extract":
            extract_bundle(args.root, args.bundle, args.destination)
        else:
            verify_manifest(args.root, args.manifest, args.binary)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        if isinstance(error, ValueError) and str(error).endswith("linux/amd64 only"):
            parser.exit(1, str(error) + "\n")
        parser.exit(1, "Native artifact/source verification failed; use the matching project-native image or rebuild it.\n")
    print("Native artifact/source verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
