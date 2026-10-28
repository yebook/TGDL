import json
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.storage.files import Storage


def make_storage(tmp_path: Path) -> Storage:
    root, data = tmp_path / "downloads", tmp_path / "data"
    root.mkdir()
    data.mkdir()
    return Storage(root, data, 0)


def test_identity_stays_in_data_and_survives_storage_recreation(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    assert storage.check()["available"]
    assert list(storage.root.iterdir()) == []
    identity = storage.identity_path.read_bytes()
    second = Storage(storage.root, storage.data_dir, 0)
    assert second.check()["available"]
    assert storage.identity_path.read_bytes() == identity
    assert list(storage.root.iterdir()) == []


def test_root_missing_replaced_and_symlink_fail_closed(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.check()
    identity = storage.identity_path.read_bytes()
    original = storage.root.with_name("original")
    storage.root.rename(original)
    with pytest.raises(FileNotFoundError):
        storage.check()
    assert not storage.root.exists()
    storage.root.mkdir()
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_CHANGED"
    assert storage.identity_path.read_bytes() == identity
    storage.root.rmdir()
    try:
        storage.root.symlink_to(original, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_UNAVAILABLE"


@pytest.mark.parametrize("identity", [b"not json", b"[]", b'{"marker":"old","device":1}',
                                     b'{"device":true,"inode":1}', b'{"device":1,"inode":0}', b"x" * 513])
def test_invalid_identity_is_actionable_and_never_reenrolled(tmp_path: Path, identity: bytes) -> None:
    storage = make_storage(tmp_path)
    storage.identity_path.write_bytes(identity)
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code in {"STORAGE_CHANGED", "UNSAFE_PATH"}
    assert storage.identity_path.read_bytes() == identity
    assert list(storage.root.iterdir()) == []


def test_changed_device_fails_closed_and_unrelated_old_marker_is_untouched(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    marker = storage.root / ".tgd-root"
    marker.write_bytes(b"unrecognized user file")
    storage.check()
    identity = json.loads(storage.identity_path.read_bytes())
    identity["device"] += 1
    storage.identity_path.write_text(json.dumps(identity))
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_CHANGED"
    assert marker.read_bytes() == b"unrecognized user file"
