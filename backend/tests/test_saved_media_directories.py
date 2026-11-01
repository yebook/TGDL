import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_saved_media import remove, saved

from app.db.models import FileDeletionOperation, MediaRequest
from app.storage import saved_media
from app.storage.files import Storage

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Real no-follow directory descriptors require POSIX")
DAY = "2026-11-01"


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    root, data = tmp_path / "downloads", tmp_path / "data"
    root.mkdir()
    data.mkdir()
    result = Storage(root, data, minimum_free=0)
    result.initialize()
    return result


def output(storage: Storage, relative: str = f"{DAY}/group/video.mp4") -> tuple[Path, dict[str, object]]:
    path = storage.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"abcdef")
    entry: dict[str, object] = {"relative_path": relative, "size_bytes": 6,
                                "sha256": hashlib.sha256(b"abcdef").hexdigest()}
    if len(path.relative_to(storage.root).parts) == 3:
        entry["prune_directory"] = path.parent.relative_to(storage.root).as_posix()
    return path, entry


def group_saved(client: TestClient, path: str = f"{DAY}/group/video.mp4") -> tuple[str, Path]:
    request, output = saved(client, path)
    with client.app.state.db.transaction() as session:
        row = session.get(MediaRequest, request)
        row.kind = "group"
        row.destination = str(Path(path).parent).replace("\\", "/")
    return request, output


def test_prune_empty_group_preserves_date_root_and_unrelated_directory(storage: Storage) -> None:
    path, entry = output(storage)
    unrelated = storage.root / "unrelated"
    unrelated.mkdir()
    identity = storage._read_identity()
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert not path.parent.exists() and (storage.root / DAY).is_dir()
    assert unrelated.is_dir() and storage._root_identity() == identity


def test_shared_parent_survives_until_last_file_is_removed(storage: Storage) -> None:
    first, first_entry = output(storage)
    second, second_entry = output(storage, f"{DAY}/group/second.mp4")
    assert saved_media.remove_verified(storage, first_entry) == "deleted"
    assert not first.exists() and second.read_bytes() == b"abcdef"
    assert saved_media.remove_verified(storage, second_entry) == "deleted"
    assert not first.parent.exists() and (storage.root / DAY).is_dir() and storage.root.is_dir()


def test_nonempty_ancestor_stops_pruning_and_keeps_empty_sibling(storage: Storage) -> None:
    _, entry = output(storage)
    sibling = storage.root / f"{DAY}/other-empty"
    sibling.mkdir()
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert not (storage.root / f"{DAY}/group").exists()
    assert sibling.is_dir() and (storage.root / DAY).is_dir()


def test_root_level_file_never_removes_root(storage: Storage) -> None:
    path, entry = output(storage, "video.mp4")
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert not path.exists() and storage.root.is_dir()


def test_single_file_preserves_date_directory(storage: Storage) -> None:
    path, entry = output(storage, f"{DAY}/video.mp4")
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert not path.exists() and path.parent.is_dir()


@pytest.mark.parametrize("boundary", [None, "", "unrelated", DAY, f"{DAY}/other"])
def test_absent_or_mismatched_journal_boundary_preserves_group(storage: Storage, boundary: str | None) -> None:
    path, entry = output(storage)
    entry.pop("prune_directory")
    if boundary is not None:
        entry["prune_directory"] = boundary
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert path.parent.is_dir() and path.parent.parent.is_dir()


@pytest.mark.parametrize("kind", ["missing", "different_hash", "unverified"])
def test_no_pruning_without_proven_file_removal(storage: Storage, kind: str) -> None:
    path, entry = output(storage)
    if kind == "missing":
        path.unlink()
    elif kind == "different_hash":
        path.write_bytes(b"ABCDEF")
    else:
        entry.update(size_bytes=-1, sha256=None)
    assert saved_media.remove_verified(storage, entry) == ("missing" if kind == "missing" else "retained_unverified")
    assert path.parent.is_dir()


def test_file_parent_fsync_failure_keeps_uncertainty_and_directory(storage: Storage, monkeypatch) -> None:
    path, entry = output(storage)

    def failed(_descriptor):
        raise OSError("injected file-parent durability failure")

    monkeypatch.setattr(saved_media.os, "fsync", failed)
    assert saved_media.remove_verified(storage, entry) == "uncertain"
    assert not path.exists() and path.parent.is_dir()


def test_pruning_fsync_failure_stops_without_relabeling_deleted_file(storage: Storage, monkeypatch) -> None:
    path, entry = output(storage)
    original = os.fsync
    calls = 0

    def failed(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory-prune durability failure")
        original(descriptor)

    monkeypatch.setattr(saved_media.os, "fsync", failed)
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert calls == 2 and not path.parent.exists() and (storage.root / DAY).is_dir()


def test_permission_failure_stops_pruning_without_relabeling_deleted_file(storage: Storage) -> None:
    path, entry = output(storage)
    parent = path.parent.parent
    parent.chmod(0o500)
    try:
        assert saved_media.remove_verified(storage, entry) == "deleted"
        assert not path.exists() and path.parent.is_dir()
    finally:
        parent.chmod(0o700)


def test_concurrent_new_entry_makes_rmdir_preserve_parent(storage: Storage, monkeypatch) -> None:
    path, entry = output(storage)
    original = os.rmdir
    calls = 0

    def new_entry(name, *, dir_fd=None):
        nonlocal calls
        calls += 1
        assert name == "group" and dir_fd is not None
        (path.parent / "concurrent").write_bytes(b"preserved")
        original(name, dir_fd=dir_fd)

    monkeypatch.setattr(saved_media.os, "rmdir", new_entry)
    monkeypatch.setattr(saved_media.os, "supports_dir_fd", os.supports_dir_fd | {new_entry})
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert calls == 1 and (path.parent / "concurrent").read_bytes() == b"preserved"


@pytest.mark.parametrize("kind", ["parent", "ancestor", "root", "symlink"])
def test_replaced_directories_after_unlink_are_preserved(storage: Storage, monkeypatch, kind: str) -> None:
    path, entry = output(storage)
    original = os.fsync
    calls = 0
    target = {"parent": path.parent, "ancestor": path.parent.parent,
              "root": storage.root, "symlink": path.parent}[kind]
    moved = target.with_name(target.name + "-old")

    def replacement(descriptor):
        nonlocal calls
        original(descriptor)
        calls += 1
        if calls == 1:
            target.rename(moved)
            if kind == "symlink":
                target.symlink_to(moved, target_is_directory=True)
            else:
                target.mkdir()

    monkeypatch.setattr(saved_media.os, "fsync", replacement)
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert calls == 1 and moved.is_dir()
    assert target.is_symlink() if kind == "symlink" else target.is_dir()


def test_post_prune_date_replacement_is_never_removed(storage: Storage, monkeypatch) -> None:
    path, entry = output(storage)
    day = path.parent.parent
    moved = day.with_name("old-day")
    original = os.fsync
    calls = 0

    def replaced(descriptor):
        nonlocal calls
        original(descriptor)
        calls += 1
        if calls == 2:
            day.rename(moved)
            day.mkdir()

    monkeypatch.setattr(saved_media.os, "fsync", replaced)
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert calls == 2 and not path.parent.exists()
    assert day.is_dir() and moved.is_dir()


def test_unsupported_rmdir_keeps_file_deletion_and_directories(storage: Storage, monkeypatch) -> None:
    path, entry = output(storage)
    monkeypatch.setattr(saved_media.os, "supports_dir_fd", os.supports_dir_fd - {os.rmdir})
    assert saved_media.remove_verified(storage, entry) == "deleted"
    assert not path.exists() and path.parent.is_dir()


def test_ancestor_replacement_during_hash_retains_file(storage: Storage, monkeypatch) -> None:
    path, entry = output(storage)
    original = saved_media.hash_stream
    day = path.parent.parent
    old = day.with_name("old-day")

    def swapped(stream):
        result = original(stream)
        day.rename(old)
        day.mkdir()
        (old / "group").rename(day / "group")
        return result

    monkeypatch.setattr(saved_media, "hash_stream", swapped)
    assert saved_media.remove_verified(storage, entry) == "retained_unverified"
    assert path.read_bytes() == b"abcdef" and old.is_dir()


def test_explicit_service_deletion_prunes_group_and_replay_preserves_new_directory(authenticated: TestClient) -> None:
    request, path = group_saved(authenticated, f"{DAY}/任务标题/video.mp4")
    operation = uuid4().hex
    response = remove(authenticated, [request], operation)
    assert response.status_code == 200 and response.json()["files"]["deleted"] == 1
    assert not path.parent.exists() and path.parent.parent.is_dir()
    with authenticated.app.state.db.sessions() as session:
        entry = session.get(FileDeletionOperation, operation).entries[0]
        assert entry["prune_directory"] == f"{DAY}/任务标题"
    path.parent.mkdir(parents=True)
    assert remove(authenticated, [request], operation).json() == response.json()
    assert path.parent.is_dir()


def test_record_only_removal_preserves_empty_directory(authenticated: TestClient) -> None:
    request, path = group_saved(authenticated)
    path.unlink()
    assert authenticated.post("/api/v1/tasks/delete", json={"ids": [request]}).status_code == 200
    assert path.parent.is_dir()


def test_shared_service_reference_keeps_parent_until_last_reference_deleted(authenticated: TestClient) -> None:
    first, path = group_saved(authenticated)
    # The remaining single-file alias must use the original StoredFile owner's
    # frozen group destination, even after that original request is soft-deleted.
    second, _ = saved(authenticated, f"{DAY}/group/video.mp4", alias=True)
    with authenticated.app.state.db.transaction() as session:
        session.get(MediaRequest, second).destination = DAY
    assert remove(authenticated, [first]).json()["files"]["retained_shared"] == 1
    assert path.parent.is_dir() and path.read_bytes() == b"abcdef"
    assert remove(authenticated, [second]).json()["files"]["deleted"] == 1
    assert not path.parent.exists() and path.parent.parent.is_dir()


@pytest.mark.parametrize("directory", ["2026-11/group", "videos/2026-11/group", "2026-02-30/group", "20261101/group"])
def test_historical_or_invalid_group_layout_preserves_directories(authenticated: TestClient, directory: str) -> None:
    request, path = group_saved(authenticated, f"{directory}/video.mp4")
    response = remove(authenticated, [request])
    assert response.status_code == 200 and response.json()["files"]["deleted"] == 1
    assert not path.exists() and path.parent.is_dir()
    with authenticated.app.state.db.sessions() as session:
        entry = session.get(FileDeletionOperation, response.json()["operation_id"]).entries[0]
        assert "prune_directory" not in entry


@pytest.mark.parametrize("kind", ["single", "mismatched_destination", "single_date_directory"])
def test_unproven_service_directory_is_preserved(authenticated: TestClient, kind: str) -> None:
    request, path = saved(authenticated, f"{DAY}/video.mp4" if kind == "single_date_directory"
                          else f"{DAY}/group/video.mp4")
    with authenticated.app.state.db.transaction() as session:
        row = session.get(MediaRequest, request)
        row.kind = "group" if kind == "mismatched_destination" else "single"
        row.destination = f"{DAY}/different" if kind == "mismatched_destination" else DAY
    assert remove(authenticated, [request]).json()["files"]["deleted"] == 1
    assert not path.exists() and path.parent.is_dir()
