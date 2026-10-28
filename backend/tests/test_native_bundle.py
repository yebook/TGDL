"""Fail-closed checked-in native bundle and deterministic regeneration contracts."""
import gzip
import hashlib
import io
import json
import runpy
import shutil
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
H = runpy.run_path(str(ROOT / 'deploy/bot-api/native_identity.py'))


@pytest.fixture
def bundle_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / 'root'
    api = root / 'deploy/bot-api'
    api.mkdir(parents=True)
    shutil.copyfile(ROOT / '.dockerignore', root / '.dockerignore')
    for name in (*H['INPUTS'][1:], 'deploy/bot-api/Dockerfile'):
        shutil.copyfile(ROOT / name, root / name)
    source = tmp_path / 'native'
    for name in H['BUNDLE_MEMBERS']:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'verified fixture file\n')
    (source / 'share/tgdl-source.json').write_text(json.dumps(H['source_pins'](root)))
    H['write_manifest'](root, source / 'share/tgdl-native.json', source / 'share/tgdl-source.json', source / 'bin/telegram-bot-api')
    bundle = tmp_path / 'bundle'
    H['pack_bundle'](root, source, bundle)
    return root, source, bundle


def test_checked_in_native_bundle_has_current_inputs_and_binary_checksum(tmp_path: Path) -> None:
    H['extract_bundle'](ROOT, ROOT / 'deploy/bot-api/prebuilt', tmp_path / 'verified')
    binary = tmp_path / 'verified/bin/telegram-bot-api'
    assert binary.read_bytes()[:4] == b'\x7fELF'
    assert int.from_bytes(binary.read_bytes()[18:20], 'little') == 62  # ELF x86-64.
    manifest = json.loads((ROOT / 'deploy/bot-api/prebuilt/manifest.json').read_text())
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == manifest['files']['bin/telegram-bot-api']


def test_bundle_is_deterministic_and_extracts_readable_files(bundle_tree: tuple[Path, Path, Path], tmp_path: Path) -> None:
    root, source, bundle = bundle_tree
    second = tmp_path / 'second'
    H['pack_bundle'](root, source, second)
    assert (bundle / 'linux-amd64.tar.gz').read_bytes() == (second / 'linux-amd64.tar.gz').read_bytes()
    assert (bundle / 'manifest.json').read_bytes() == (second / 'manifest.json').read_bytes()
    H['extract_bundle'](root, bundle, tmp_path / 'out')
    assert (tmp_path / 'out/bin/telegram-bot-api').read_bytes() == (source / 'bin/telegram-bot-api').read_bytes()


@pytest.mark.parametrize('changed', ['binary', 'patch', 'revision', 'archive', 'missing-manifest', 'missing-archive'])
def test_bundle_rejects_stale_missing_and_tampered_before_writing(bundle_tree: tuple[Path, Path, Path], tmp_path: Path, changed: str) -> None:
    root, _, bundle = bundle_tree
    if changed == 'patch':
        (root / 'deploy/bot-api/CacheCleanup.h').write_text('changed')
    elif changed == 'revision':
        path = root / 'deploy/bot-api/Dockerfile'
        path.write_text(path.read_text().replace('bc9c263e2bfee06aaab41e82db51a103376030bc', 'a' * 40))
    elif changed == 'missing-manifest':
        (bundle / 'manifest.json').unlink()
    elif changed == 'missing-archive':
        (bundle / 'linux-amd64.tar.gz').unlink()
    elif changed == 'archive':
        with (bundle / 'linux-amd64.tar.gz').open('ab') as stream:
            stream.write(b'tampered')
    else:
        value = json.loads((bundle / 'manifest.json').read_text())
        value['files']['bin/telegram-bot-api'] = 'a' * 64
        (bundle / 'manifest.json').write_text(json.dumps(value))
    out = tmp_path / 'out'
    with pytest.raises((ValueError, FileNotFoundError)):
        H['extract_bundle'](root, bundle, out)
    assert not out.exists()


@pytest.mark.parametrize('unsafe', ['traversal', 'symlink', 'duplicate', 'oversized', 'missing'])
def test_bundle_rejects_unsafe_archive_even_with_updated_outer_checksum(bundle_tree: tuple[Path, Path, Path], tmp_path: Path, unsafe: str) -> None:
    root, _, bundle = bundle_tree
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        member = tarfile.TarInfo('../outside' if unsafe == 'traversal' else 'bin/telegram-bot-api')
        member.size = 1
        if unsafe == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside'
            member.size = 0
        if unsafe == 'oversized':
            member.size = 200 * 1024 * 1024
            tar.addfile(member)
        elif unsafe != 'missing':
            tar.addfile(member, io.BytesIO(b'x'))
        if unsafe == 'duplicate':
            tar.addfile(member, io.BytesIO(b'x'))
    archive = gzip.compress(data.getvalue(), mtime=0)
    (bundle / 'linux-amd64.tar.gz').write_bytes(archive)
    metadata = json.loads((bundle / 'manifest.json').read_text())
    metadata['archive_sha256'] = hashlib.sha256(archive).hexdigest()
    metadata['files']['bin/telegram-bot-api'] = hashlib.sha256(b'x').hexdigest()
    (bundle / 'manifest.json').write_text(json.dumps(metadata))
    out = tmp_path / 'out'
    with pytest.raises(ValueError):
        H['extract_bundle'](root, bundle, out)
    assert not out.exists()


def test_pack_refuses_changed_binary_after_manifest(bundle_tree: tuple[Path, Path, Path], tmp_path: Path) -> None:
    root, source, _ = bundle_tree
    (source / 'bin/telegram-bot-api').write_bytes(b'different binary')
    with pytest.raises(ValueError, match='checksum'):
        H['pack_bundle'](root, source, tmp_path / 'new-bundle')


def test_extract_rejects_destination_symlink_before_any_write(bundle_tree: tuple[Path, Path, Path], tmp_path: Path) -> None:
    root, _, bundle = bundle_tree
    outside = tmp_path / 'outside'
    outside.mkdir()
    out = tmp_path / 'out'
    out.mkdir()
    try:
        (out / 'share').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Host does not allow directory symlinks')
    with pytest.raises(ValueError, match='symlink'):
        H['extract_bundle'](root, bundle, out)
    assert not (out / 'bin').exists()
    assert not list(outside.iterdir())


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
    before = H["native_identity"](tmp_path)
    if changed == "product-version":
        dockerfile = api / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text().replace('image.version="0.1.0"', 'image.version="0.2.0"'))
    elif changed == "app-only":
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend/app.ts").write_text("app-only update")
    else:
        path = api / changed
        path.write_text(path.read_text() + "\n# runtime-only change\n")
    assert H["native_identity"](tmp_path) == before


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
    before = H["native_identity"](tmp_path)
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
    assert H["native_identity"](tmp_path) != before


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
