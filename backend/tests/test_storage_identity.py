import json
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.storage.files import Storage


def make_storage(tmp_path: Path, *, root_name: str = "downloads") -> Storage:
    root, data = tmp_path / root_name, tmp_path / "data"
    root.mkdir()
    data.mkdir(exist_ok=True)
    return Storage(root, data, 0)


def read_identity(storage: Storage) -> dict[str, int]:
    return json.loads(storage.identity_path.read_text())


def test_identity_stays_in_data_and_survives_storage_recreation(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    assert storage.check()["available"]
    record = read_identity(storage)
    assert set(record) == {"device", "inode"}
    assert record == storage._root_identity()
    recreated = Storage(storage.root, storage.data_dir, 0)
    assert recreated.check()["available"]
    assert read_identity(recreated) == record
    assert list(storage.root.iterdir()) == []


def test_new_storage_instance_enrolls_current_configured_root(tmp_path: Path) -> None:
    original = make_storage(tmp_path)
    original.check()
    old_record = read_identity(original)
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    switched = Storage(replacement, original.data_dir, 0)
    assert switched.check()["available"]
    record = read_identity(switched)
    assert record == switched._root_identity()
    assert record != old_record


def test_new_storage_instance_checks_migrated_file_by_relative_path(tmp_path: Path) -> None:
    original = make_storage(tmp_path)
    original.check()
    relative = Path("day") / "video.mp4"
    source = original.root / relative
    source.parent.mkdir()
    source.write_bytes(b"abcdef")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    target = replacement / relative
    target.parent.mkdir()
    source.replace(target)

    switched = Storage(replacement, original.data_dir, 0)
    assert switched.existing(relative.as_posix(), size=6)


def test_same_storage_instance_rejects_replaced_root(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.check()
    original = storage.root.with_name("original")
    storage.root.rename(original)
    storage.root.mkdir()
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_CHANGED"


def test_initialize_cannot_replace_an_enrolled_baseline(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.initialize()
    identity = storage.identity_path.read_bytes()
    storage.root.rename(storage.root.with_name("original"))
    storage.root.mkdir()
    with pytest.raises(AppError) as error:
        storage.initialize()
    assert error.value.code == "STORAGE_CHANGED"
    assert storage.identity_path.read_bytes() == identity
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_CHANGED"


@pytest.mark.parametrize("entrypoint", ["initialize", "check"])
def test_failed_identity_write_is_retried(tmp_path: Path, monkeypatch, entrypoint: str) -> None:
    storage = make_storage(tmp_path)
    write = storage._write_identity
    calls = 0

    def flaky_write(identity: dict[str, int]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("identity write denied")
        write(identity)

    monkeypatch.setattr(storage, "_write_identity", flaky_write)
    with pytest.raises(PermissionError):
        getattr(storage, entrypoint)()
    assert storage.check()["available"]
    assert calls == 2
    assert read_identity(storage) == storage._root_identity()


@pytest.mark.parametrize("entrypoint", ["initialize", "check"])
def test_failed_identity_write_still_pins_original_root(tmp_path: Path, monkeypatch, entrypoint: str) -> None:
    storage = make_storage(tmp_path)

    def denied_write(identity: dict[str, int]) -> None:
        raise PermissionError("identity write denied")

    monkeypatch.setattr(storage, "_write_identity", denied_write)
    with pytest.raises(PermissionError):
        getattr(storage, entrypoint)()
    storage.root.rename(storage.root.with_name("original"))
    storage.root.mkdir()
    for operation in (storage.check, storage.initialize):
        with pytest.raises(AppError) as error:
            operation()
        assert error.value.code == "STORAGE_CHANGED"


def test_initialize_overwrites_legacy_or_corrupt_identity(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.identity_path.write_text(json.dumps({"device": 1, "inode": 2, "configured_path": "/old"}))
    storage.initialize()
    assert read_identity(storage) == storage._root_identity()

    storage.identity_path.write_bytes(b"not json")
    storage.initialize()
    assert read_identity(storage) == storage._root_identity()


def test_root_missing_and_symlink_fail_closed(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.check()
    original = storage.root.with_name("original")
    storage.root.rename(original)
    with pytest.raises(FileNotFoundError):
        storage.check()

    storage.root.mkdir()
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_CHANGED"

    storage.root.rmdir()
    try:
        storage.root.symlink_to(original, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    with pytest.raises(AppError) as error:
        storage.check()
    assert error.value.code == "STORAGE_UNAVAILABLE"


def test_identity_file_is_recreated_without_changing_enrolled_root(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.check()
    expected = storage._root_identity()
    storage.identity_path.unlink()
    storage.initialize()
    assert read_identity(storage) == expected


def test_unrelated_old_marker_is_untouched(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    marker = storage.root / ".tgd-root"
    marker.write_bytes(b"unrecognized user file")
    storage.check()
    assert marker.read_bytes() == b"unrecognized user file"
