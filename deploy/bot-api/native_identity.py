"""Shared build-time identity for the project's patched native Bot API/TDLib artifact.

Uses only the supplied source tree and a standard-library Python interpreter; no Git,
application state, secrets, network or product version is required at verification time.
"""
import argparse
import hashlib
import json
import platform
import re
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


def write_manifest(root: Path, output: Path, source: Path) -> None:
    require_supported_architecture()
    revisions = source_revisions(root, json.loads(source.read_text(encoding="utf-8")))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "format": 1, "platform": PLATFORM, "native_input_sha256": native_identity(root), **revisions,
    }, indent=2) + "\n", encoding="utf-8")


def verify_manifest(root: Path, manifest: Path) -> None:
    require_supported_architecture()
    value = json.loads(manifest.read_text(encoding="utf-8"))
    expected_fields = {"format", "platform", "native_input_sha256", "bot_api_revision", "tdlib_revision"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError("Missing or invalid native build manifest; rebuild the matching project-native image")
    if type(value["format"]) is not int or value["format"] != 1 or value["platform"] != PLATFORM:
        raise ValueError("Unsupported native build manifest/platform")
    identity = value["native_input_sha256"]
    if not isinstance(identity, str) or not SHA256.fullmatch(identity) or identity != native_identity(root):
        raise ValueError("Native image does not match current source inputs; select or rebuild the matching native image")
    source_revisions(root, {key: value[key] for key in ("bot_api_revision", "tdlib_revision")})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    write = subparsers.add_parser("write")
    write.add_argument("--root", type=Path, required=True)
    write.add_argument("--output", type=Path, required=True)
    write.add_argument("--source", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "write":
            write_manifest(args.root, args.output, args.source)
        else:
            verify_manifest(args.root, args.manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, ValueError) and str(error).endswith("linux/amd64 only"):
            parser.exit(1, str(error) + "\n")
        parser.exit(1, "Native artifact/source verification failed; use the matching project-native image or rebuild it.\n")
    print("Native artifact/source verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
