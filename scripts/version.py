"""Synchronize release mirrors from backend/app/version.json (Python 3.12+)."""
import argparse
import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_version(root: Path) -> str:
    # Reuse the runtime validator without importing the application or dependencies.
    metadata = runpy.run_path(str(root / "backend/app/version.py"))
    return str(metadata["VERSION"])


def planned_updates(root: Path) -> dict[Path, str]:
    version = release_version(root)
    targets = [
        ("backend/pyproject.toml", r'(?m)^(version = ")[^"]+("\r?$)', version, 1),
        ("backend/uv.lock", r'(name = "tgd-backend"\r?\nversion = ")[^"]+(")', version, 1),
        ("frontend/package.json", r'(?m)^(  "version": ")[^"]+(",?\r?$)', version, 1),
        ("compose.yaml", r'(?m)^(    image: \$\{TGD_(?:APP|API)_IMAGE:-tgd-(?:app|bot-api):)[0-9.]+(\}\r?$)', version, 3),
        (".env.example", r'(?m)^(# TGD_(?:APP|API)_IMAGE=ghcr\.io/yebook/tgdl-(?:app|bot-api):)[0-9.]+(\r?$)', version, 2),
        ("Dockerfile", r'(LABEL org.opencontainers.image.version=")[^"]+(")', version, 1),
        ("deploy/bot-api/Dockerfile", r'(LABEL org.opencontainers.image.version=")[^"]+(")', version, 1),
        ("deploy/bot-api/Dockerfile.verify-native", r'(?m)^(ARG TGDL_API_IMAGE=tgd-bot-api:)[^\s]+(\r?$)', version, 1),
        ("deploy/bot-api/Dockerfile.verify-progress", r'(?m)^(ARG TGDL_API_IMAGE=tgd-bot-api:)[^\s]+(\r?$)', version, 1),
    ]
    updates = {}
    for relative, pattern, replacement, expected in targets:
        path = root / relative
        current = path.read_bytes().decode("utf-8")
        def replace_version(match: re.Match[str], value: str = replacement) -> str:
            return match[1] + value + match[2]

        candidate, count = re.subn(pattern, replace_version, current)
        if count != expected:
            raise ValueError(f"Unexpected release manifest structure: {relative}")
        if candidate != current:
            updates[path] = candidate
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if generated release mirrors differ")
    mode.add_argument("--write", action="store_true", help="Update release mirrors after validating all targets")
    args = parser.parse_args()
    try:
        updates = planned_updates(ROOT)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Version metadata validation failed: {error}\n")
    if args.write:
        for path, content in updates.items():
            path.write_bytes(content.encode("utf-8"))
    elif updates:
        print("Release mirrors differ; run python scripts/version.py --write:")
        for path in updates:
            print(path.relative_to(ROOT).as_posix())
        return 1
    print(f"Release metadata consistent: {release_version(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
