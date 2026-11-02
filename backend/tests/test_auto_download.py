import base64
import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_config_backup import PASSWORD, export, save
from test_group_collection import clock
from test_pipeline import FakeClient, message, setup_pipeline

from app.core.config import Config
from app.db.models import Job, LocalApiRuntime, MediaRequest, Notification
from app.schemas.backup import BackupEnvelope
from app.schemas.contracts import SettingsPatch
from app.services.config_backup import AAD, ConfigBackupService


def enable(state, **values):
    patch = SettingsPatch(revision=state.settings.read()[0], auto_download=True, **values)
    state.settings.save(patch, state.settings.candidate(patch))


def test_default_off_and_existing_confirmation_unchanged(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    assert state.settings.read()[1].auto_download is False
    ingest.message(7, message(), "one")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        request_id, nonce, deadline = request.id, request.nonce, request.expires_at
        assert request.status == "awaiting_confirmation"
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    enable(state)
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).expires_at == deadline
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"


async def test_automatic_file_download_and_replay(client: TestClient, config: Config) -> None:
    state, ingest = setup_pipeline(client)
    enable(state, filename_template="auto_{name}")
    source = config.bot_api_root / "movie.mp4"
    source.write_bytes(b"media!")
    ingest.message(7, message(), "one")
    state.tasks.freeze_due()
    ingest.message(7, message(), "replay")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        request_id, nonce = request.id, request.nonce
        assert request.status == "queued" and request.expires_at is None
        jobs = list(session.scalars(select(Job)))
        assert len(jobs) == 1
        assert jobs[0].relative_path.endswith("/auto_movie1.mp4")
        notifications = list(session.scalars(select(Notification)))
        assert len(notifications) == 1
        assert notifications[0].event_key == f"queued:{request_id}"
        assert not notifications[0].payload.get("confirmation")
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    fake = FakeClient(source)
    await state.runtime.worker.run(fake, state.runtime.worker.claim())
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "succeeded"
    assert (config.download_root / detail["items"][0]["relative_path"]).read_bytes() == b"media!"
    assert fake.calls == 1


def test_automatic_group_waits_for_collection(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    state, ingest = setup_pipeline(client)
    enable(state)
    now = clock(monkeypatch)
    ingest.message(7, message(1, "group"), "one")
    now[0] += 4
    ingest.message(7, message(2, "group", "audio"), "two")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(MediaRequest)).status == "collecting"
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    now[0] += 3
    state.tasks.freeze_due()
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(MediaRequest)).status == "queued"
        jobs = list(session.scalars(select(Job)))
        assert len(jobs) == 2
        assert len({job.relative_path.rpartition("/")[0] for job in jobs}) == 1
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_auto_admission_respects_maintenance_and_current_allowlist(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    enable(state)
    ingest.message(7, message(), "one")
    with state.db.transaction() as session:
        row = session.get(LocalApiRuntime, 1)
        row.values = row.values | {"cache_maintenance": {"state": "uncertain"}}
    state.tasks.freeze_due()
    with state.db.transaction() as session:
        assert session.scalar(select(MediaRequest)).status == "collecting"
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        session.get(LocalApiRuntime, 1).values = {}
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(MediaRequest)).status == "queued"
        assert session.scalar(select(func.count()).select_from(Job)) == 1
    ingest.message(7, message(2), "two")
    enable(state, allowed_user_ids=[])
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(MediaRequest).where(MediaRequest.status == "awaiting_confirmation")) is not None
        assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_auto_setting_persistence_environment_and_backup(authenticated: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client = authenticated
    assert client.get("/api/v1/settings").json()["values"]["auto_download"] is False
    save(client, auto_download=True)
    backup = export(client)
    save(client, auto_download=False)
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/backup/restore", json={"backup": backup, "password": PASSWORD, "revision": revision})
    assert response.status_code == 200
    assert response.json()["values"]["auto_download"] is True
    monkeypatch.setenv("TGD_AUTO_DOWNLOAD", "false")
    current = client.get("/api/v1/settings").json()
    assert current["values"]["auto_download"] is False
    assert current["editable"]["auto_download"] is False
    assert client.patch("/api/v1/settings", json={"revision": current["revision"], "auto_download": True}).status_code == 400
    monkeypatch.setenv("TGD_AUTO_DOWNLOAD", "true")
    assert client.get("/api/v1/settings").json()["values"]["auto_download"] is True


def test_legacy_backup_restores_manual_download(authenticated: TestClient) -> None:
    service = ConfigBackupService(authenticated.app.state.settings)
    backup = export(authenticated)
    payload = service.decrypt(BackupEnvelope.model_validate(backup), PASSWORD).model_dump()
    del payload["settings"]["auto_download"]
    nonce = base64.b64decode(backup["nonce"])
    key = service._key(PASSWORD, base64.b64decode(backup["salt"]))
    backup["ciphertext"] = base64.b64encode(AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), AAD)).decode()
    save(authenticated, auto_download=True)
    revision = authenticated.app.state.settings.read()[0]
    response = authenticated.post("/api/v1/settings/backup/restore", json={"backup": backup, "password": PASSWORD, "revision": revision})
    assert response.status_code == 200, response.text
    assert response.json()["values"]["auto_download"] is False
