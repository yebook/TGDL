from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.bot.messages import storage_warning
from app.core.config import Config
from app.core.errors import AppError
from app.db.models import Base, Job, MediaItem, MediaRequest
from app.db.session import Database
from app.storage.space import assess_space


@pytest.fixture
def space_session(config: Config):
    db = Database(config.database_url)
    Base.metadata.create_all(db.engine)
    with db.transaction() as session:
        yield session
    db.engine.dispose()


def roots(config: Config, monkeypatch: pytest.MonkeyPatch, *, free: int,
          same_device: bool = True, other_free: int = 100_000):
    device = config.bot_api_root.stat().st_dev
    root_device = device if same_device else device + 1
    targets = {
        "default": SimpleNamespace(root=SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=root_device)),
                                   check=lambda **_: {"free_bytes": free}),
        "other": SimpleNamespace(root=SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=device + 2)),
                                 check=lambda **_: {"free_bytes": other_free}),
        "alias": SimpleNamespace(root=SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=root_device)),
                                 check=lambda **_: {"free_bytes": free}),
    }
    monkeypatch.setattr("app.storage.space.shutil.disk_usage", lambda _: SimpleNamespace(free=free))
    return SimpleNamespace(get=lambda key: targets[key])


def active(session: Session, *, root: str = "default", mode: str = "local", size: int | None = 100,
           state: str = "fetching", phase: str = "fetching", progress: int = 0) -> Job:
    request = MediaRequest(bot_id=1, chat_id=1, user_id=1, collect_until=0)
    session.add(request)
    session.flush()
    item = MediaItem(request_id=request.id, bot_id=1, chat_id=1, message_id=int(request.id[:10], 16),
                     file_id=request.id, media_type="document", size_bytes=size)
    session.add(item)
    session.flush()
    job = Job(request_id=request.id, item_id=item.id, relative_path="file", root_id=root,
              acquisition_mode=mode, state=state, phase=phase, completed_bytes=progress)
    session.add(job)
    session.flush()
    return job


@pytest.mark.parametrize("offset,sufficient,missing", [(0, True, 0), (-1, False, 1)])
def test_same_device_counts_two_copies_and_reserve_once(config, space_session, monkeypatch,
                                                       offset, sufficient, missing):
    storage = roots(config, monkeypatch, free=1024 + 200 + offset)
    result = assess_space(space_session, storage, config, "default", 100, "local", "before_download")
    assert result.sufficient is sufficient
    assert len(result.volumes) == 1
    assert result.volumes[0].location == "target+cache"
    assert result.volumes[0].required_bytes == 200
    assert result.volumes[0].missing_bytes == missing
    if not sufficient:
        with pytest.raises(AppError, match="可用空间不足") as exc:
            result.require()
        assert exc.value.code == "NO_SPACE"
        assert exc.value.details == result.to_dict()


def test_different_devices_each_require_one_copy(config, space_session, monkeypatch):
    storage = roots(config, monkeypatch, free=1124, same_device=False)
    result = assess_space(space_session, storage, config, "default", 100, "local", "before_download")
    assert result.sufficient
    assert len(result.volumes) == 2
    assert [v.required_bytes for v in result.volumes] == [100, 100]


@pytest.mark.parametrize("mode,phase", [("cloud", "before_download"), ("local", "before_save")])
def test_cloud_and_complete_cache_only_require_target(config, space_session, monkeypatch, mode, phase):
    storage = roots(config, monkeypatch, free=1124, same_device=False)
    config.bot_api_root = Path("missing-api-cache")
    result = assess_space(space_session, storage, config, "default", 100, mode, phase)
    assert result.sufficient
    assert len(result.volumes) == 1
    assert result.volumes[0].required_bytes == 100


def test_concurrent_budget_follows_devices_and_saving_stage(config, space_session, monkeypatch):
    storage = roots(config, monkeypatch, free=10_000, same_device=False)
    active(space_session, root="alias", mode="cloud", size=100, phase="saving", state="saving")
    active(space_session, root="other", mode="local", size=200, progress=199)
    active(space_session, root="other", mode="local", size=300, state="saving", phase="saving")
    result = assess_space(space_session, storage, config, "default", 50, "local", "before_download")
    assert [v.concurrent_bytes for v in result.volumes] == [100, 200]


@pytest.mark.parametrize("state", ["fetching", "cancelling", "recovery_blocked"])
def test_uncertain_download_keeps_full_budget_and_excludes_current_owner(config, space_session, monkeypatch, state):
    storage = roots(config, monkeypatch, free=10_000)
    first = active(space_session, size=100, state=state, progress=100)
    active(space_session, size=200, state=state, progress=200)
    result = assess_space(space_session, storage, config, "default", 50, "local", "before_download", first.id)
    assert result.volumes[0].concurrent_bytes == 400


def test_unknown_size_uses_conservative_estimate(config, space_session, monkeypatch):
    storage = roots(config, monkeypatch, free=10_000)
    active(space_session, size=None)
    result = assess_space(space_session, storage, config, "default", None, "local", "before_download")
    assert result.volumes[0].required_bytes == 2048
    assert result.volumes[0].concurrent_bytes == 2048


def test_missing_cache_fails_closed_with_safe_warning(config, space_session, monkeypatch):
    storage = roots(config, monkeypatch, free=10_000)
    config.bot_api_root = config.bot_api_root / "secret-token" / "missing"
    result = assess_space(space_session, storage, config, "default", 100, "local", "before_download")
    assert not result.sufficient
    with pytest.raises(AppError) as exc:
        result.require()
    assert exc.value.code == "STORAGE_UNAVAILABLE"
    warning = storage_warning(result.to_dict())
    assert "缓存盘：无法检测空间" in warning
    assert "secret-token" not in warning


def test_one_copy_warning_includes_all_capacity_components(config, space_session, monkeypatch):
    storage = roots(config, monkeypatch, free=1124)
    result = assess_space(space_session, storage, config, "default", 100, "local", "before_download")
    warning = storage_warning(result.to_dict())
    assert "缓存与保存盘（同盘）" in warning
    assert "可用 1.1 KB" in warning
    assert "本次需要 200 B" in warning
    assert "安全余量 1.0 KB" in warning
    assert "尚缺 100 B" in warning
    assert "手动重试" in warning


def test_live_observation_only_checks_actual_reserve(config, space_session, monkeypatch):
    storage = roots(config, monkeypatch, free=1024)
    active(space_session, size=1_000_000)
    result = assess_space(space_session, storage, config, "default", 500_000, "local", "downloading")
    assert not result.sufficient
    assert result.observed().sufficient
    assert result.observed().volumes[0].required_bytes == 0
    storage = roots(config, monkeypatch, free=1023)
    result = assess_space(space_session, storage, config, "default", 0, "local", "downloading").observed()
    assert not result.sufficient
    assert result.volumes[0].missing_bytes == 1
