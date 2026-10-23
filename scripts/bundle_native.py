"""Export a freshly built project-native Docker target into the checked-in bundle.

Build first: docker build --target native -f deploy/bot-api/Dockerfile -t tgd-native:refresh .
Then: python scripts/bundle_native.py --image tgd-native:refresh
No registry or live container is modified.
"""
import argparse
import runpy
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Freshly built --target native image')
    args = parser.parse_args()
    native = runpy.run_path(str(ROOT / 'deploy/bot-api/native_identity.py'))
    container = subprocess.run(
        ['docker', 'create', '--network', 'none', '--entrypoint', '/bin/true', args.image],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    try:
        with tempfile.TemporaryDirectory(prefix='tgd-native-export-') as temporary:
            source = Path(temporary)
            for name in sorted(native['BUNDLE_MEMBERS']):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['docker', 'cp', f'{container}:/usr/local/{name}', str(target)], check=True)
            native['pack_bundle'](ROOT, source, ROOT / 'deploy/bot-api/prebuilt')
            native['extract_bundle'](ROOT, ROOT / 'deploy/bot-api/prebuilt', source / 'verify')
    finally:
        subprocess.run(['docker', 'rm', container], check=True, capture_output=True)
    print('Native bundle regenerated and verified; commit archive and manifest with native source changes.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
