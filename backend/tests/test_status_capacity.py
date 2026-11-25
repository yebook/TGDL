from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.core.config import Config
from app.core.errors import AppError
from app.storage import status as status_module

SAMPLED = "/api/v1/system/status?sample_storage=true"
FRESH = "/api/v1/system/status"


@pytest.fixture
def capacity_fixture(config: Config, monkeypatch: pytest.MonkeyPatch):
    clock = [100.0]
    free = {config.download_root: 20_000, config.bot_api_root: 30_000}
    counts: Counter[Path] = Counter()
    original = status_module.shutil.disk_usage

    def disk_usage(path: Path):
        if path not in free:
            return original(path)
        counts[path] += 1
        if free[path] < 0:
            raise OSError("disk usage unavailable")
        return SimpleNamespace(free=free[path], total=100_000)

    monkeypatch.setattr(status_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(status_module.shutil, "disk_usage", disk_usage)
    return clock, free, counts


def snapshot(client: TestClient, path: str = SAMPLED):
    response = client.get(path)
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("sampled, samples_per_path", [(True, 4), (False, 20)])
def test_status_sampling_affects_only_capacity(
    authenticated: TestClient, config: Config, capacity_fixture, monkeypatch: pytest.MonkeyPatch,
    sampled: bool, samples_per_path: int,
) -> None:
    clock, free, counts = capacity_fixture
    runtime = authenticated.app.state.runtime
    queue = Mock(return_value=(0, 0, 0))
    monkeypatch.setattr(runtime, "counts", queue)
    diagnostics = authenticated.app.state.network_diagnostics
    protocol = Mock(wraps=diagnostics.protocol_status)
    monkeypatch.setattr(diagnostics, "protocol_status", protocol)

    for i in range(20):
        queue.return_value = (i, 0, i + 1)
        result = snapshot(authenticated, SAMPLED if sampled else FRESH)
        assert result["queue"]["active"] == i and result["queue"]["pending"] == i + 1
        expected_age = i % 5 if sampled else 0
        assert result["storage"]["roots"][0]["free_bytes"] == 20_000 + i - expected_age
        assert result["storage"]["cache"]["free_bytes"] == 30_000 + i - expected_age
        clock[0] += 3
        free[config.download_root] += 1
        free[config.bot_api_root] += 1

    assert protocol.call_count == 20
    assert counts == {config.download_root: samples_per_path, config.bot_api_root: samples_per_path}


def test_unsampled_status_forces_a_new_read_and_reseeds_automatic_status(
    authenticated: TestClient, config: Config, capacity_fixture,
) -> None:
    clock, free, counts = capacity_fixture
    snapshot(authenticated)
    free.update({config.download_root: 21_000, config.bot_api_root: 31_000})
    clock[0] += 3
    result = snapshot(authenticated, FRESH)["storage"]
    assert result["roots"][0]["free_bytes"] == 21_000
    assert result["cache"]["free_bytes"] == 31_000
    free[config.download_root] = 22_000
    assert snapshot(authenticated)["storage"]["roots"][0]["free_bytes"] == 21_000
    assert counts == {config.download_root: 2, config.bot_api_root: 2}
    assert snapshot(authenticated, FRESH)["storage"]["roots"][0]["free_bytes"] == 22_000
    assert counts == {config.download_root: 3, config.bot_api_root: 3}


def test_missing_or_replaced_path_invalidates_a_warm_sample_immediately(
    authenticated: TestClient, config: Config, tmp_path: Path, capacity_fixture,
) -> None:
    _, free, counts = capacity_fixture
    root, cache = config.download_root, config.bot_api_root
    assert snapshot(authenticated)["storage"]["roots"][0]["available"]
    assert root.parent.resolve() == cache.parent.resolve() == tmp_path.resolve()
    original_root = root.rename(tmp_path / "original-downloads")
    original_cache = cache.rename(tmp_path / "original-cache")

    status = snapshot(authenticated)["storage"]
    assert status["roots"][0]["available"] is False
    assert status["roots"][0]["free_bytes"] is None and status["cache"]["free_bytes"] is None
    assert counts == {root: 1, cache: 1}

    root.mkdir()
    cache.mkdir()
    free.update({root: 24_000, cache: 34_000})
    status = snapshot(authenticated)["storage"]
    assert status["roots"][0]["available"] is False  # A new inode is not the enrolled root.
    assert status["cache"]["free_bytes"] == 34_000  # A new cache path must be sampled, not reused.
    assert counts == {root: 1, cache: 2}

    root.rmdir()
    cache.rmdir()
    original_root.rename(root)
    original_cache.rename(cache)
    free.update({root: 25_000, cache: 35_000})
    status = snapshot(authenticated)["storage"]
    assert status["roots"][0]["free_bytes"] == 25_000 and status["cache"]["free_bytes"] == 35_000
    assert counts == {root: 2, cache: 3}


def test_live_checks_ignore_display_sample_and_errors_are_not_cached(
    authenticated: TestClient, config: Config, capacity_fixture,
) -> None:
    _, free, counts = capacity_fixture
    root, cache = config.download_root, config.bot_api_root
    snapshot(authenticated)
    free.update({root: 0, cache: -1})
    with pytest.raises(AppError, match="可用空间不足") as caught:
        authenticated.app.state.storage.get("default").check()
    assert caught.value.code == "NO_SPACE"
    assert snapshot(authenticated)["storage"]["roots"][0]["available"] is True
    assert counts == {root: 2, cache: 1}

    status = snapshot(authenticated, FRESH)["storage"]
    assert status["roots"][0]["available"] is False and status["cache"]["free_bytes"] is None
    free[cache] = 32_000
    assert snapshot(authenticated)["storage"]["cache"]["free_bytes"] == 32_000
    assert counts == {root: 3, cache: 3}
