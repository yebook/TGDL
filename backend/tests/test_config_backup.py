import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Config
from app.core.errors import AppError
from app.db.models import Admin, MediaRequest, RequestSafety, Settings
from app.main import create_app
from app.schemas.backup import BackupEnvelope
from app.services.config_backup import MAX_BACKUP_REQUEST_BYTES, ConfigBackupService

PREFIX = "/api/v1/settings/backup/"
PASSWORD = "backup-password"
TOKEN = "123456:synthetic_test_token"
API_HASH = "ab" * 16


def export(client: TestClient) -> dict:
    response = client.post(PREFIX + "export", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["backup"]


def save(client: TestClient, **values: object) -> None:
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.patch("/api/v1/settings", json={"revision": revision, **values})
    assert response.status_code == 200, response.text


def test_encrypted_roundtrip_and_offline_redacted_preview(authenticated: TestClient) -> None:
    client = authenticated
    save(client, bot_token=TOKEN, allowed_user_ids=[42], telegram_api_hash=API_HASH,
         proxy={"host": "proxy.example", "password": "synthetic-proxy-password"}, download_concurrency=5)
    backup = export(client)
    assert backup != export(client)  # Fresh salt and nonce, even for identical settings.
    for secret in [TOKEN, API_HASH, "synthetic-proxy-password"]:
        assert secret not in json.dumps(backup)
    state = client.app.state
    save(client, download_concurrency=2, clear_bot_token=True, clear_telegram_api_hash=True, clear_proxy_password=True)
    request = {"password": PASSWORD, "backup": backup}
    with patch("app.bot.client.TelegramClient.identity", side_effect=AssertionError("Must stay offline")):
        preview = client.post(PREFIX + "preview", json=request)
        assert preview.status_code == 200, preview.text
        assert preview.json()["configured"] == {"bot_token": True, "telegram_api_hash": True, "proxy_password": True}
        assert TOKEN not in preview.text and API_HASH not in preview.text
        result = client.post(PREFIX + "restore", json=request | {"revision": preview.json()["revision"]})
    assert result.status_code == 200, result.text
    _, settings, _ = state.settings.read()
    assert (settings.bot_token, settings.telegram_api_hash, settings.proxy.password, settings.download_concurrency) == (
        TOKEN, API_HASH, "synthetic-proxy-password", 5)
    assert client.get("/api/v1/auth/me").status_code == 200


def test_restoring_empty_secrets_clears_instead_of_preserving(authenticated: TestClient) -> None:
    backup = export(authenticated)
    save(authenticated, bot_token=TOKEN, allowed_user_ids=[42], telegram_api_hash=API_HASH,
         proxy={"password": "saved-password"})
    revision = authenticated.get("/api/v1/settings").json()["revision"]
    result = authenticated.post(PREFIX + "restore", json={"password": PASSWORD, "backup": backup, "revision": revision})
    assert result.status_code == 200, result.text
    _, current, _ = authenticated.app.state.settings.read()
    assert not current.bot_token and not current.telegram_api_hash and not current.proxy.password


@pytest.mark.parametrize("damage", ["password", "ciphertext", "version", "extra", "salt"])
def test_invalid_backup_never_changes_settings_or_exposes_secrets(authenticated: TestClient, damage: str) -> None:
    backup = export(authenticated)
    password = PASSWORD
    if damage == "password":
        password = "wrong-password"
    elif damage == "ciphertext":
        backup["ciphertext"] = "A" * len(backup["ciphertext"])
    elif damage == "version":
        backup["version"] = 2
    elif damage == "salt":
        backup["salt"] = "!" * 24
    else:
        backup["database"] = TOKEN
    before = authenticated.app.state.settings.read()[:2]
    response = authenticated.post(PREFIX + "restore", json={"backup": backup, "password": password, "revision": before[0]})
    assert response.status_code == 422
    assert TOKEN not in response.text and password not in response.text
    assert authenticated.app.state.settings.read()[:2] == before


def test_revision_conflict_and_nonconfig_state_preserved(authenticated: TestClient) -> None:
    backup = export(authenticated)
    old = authenticated.get("/api/v1/settings").json()["revision"]
    save(authenticated, download_concurrency=7)
    response = authenticated.post(PREFIX + "restore", json={"backup": backup, "password": PASSWORD, "revision": old})
    assert response.status_code == 409
    assert authenticated.app.state.settings.read()[1].download_concurrency == 7


def test_environment_secrets_not_exported_and_target_overrides_preserved(authenticated: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigBackupService(authenticated.app.state.settings)
    monkeypatch.setenv("TGD_BOT_TOKEN", TOKEN)
    backup = export(authenticated)
    decoded = service.decrypt(BackupEnvelope.model_validate(backup), PASSWORD)
    assert "bot_token" not in decoded.settings
    assert "bot_token" in decoded.excluded_fields
    monkeypatch.delenv("TGD_BOT_TOKEN")
    save(authenticated, allowed_user_ids=[42], bot_token="999:target_token")
    revision = authenticated.get("/api/v1/settings").json()["revision"]
    # Preserve the target token: restore would otherwise empty the original whitelist.
    monkeypatch.setenv("TGD_ALLOWED_USER_IDS", "[42]")
    response = authenticated.post(PREFIX + "restore", json={"backup": backup, "password": PASSWORD, "revision": revision})
    assert response.status_code == 200, response.text
    assert authenticated.app.state.settings.read()[1].bot_token == "999:target_token"


def test_auth_csrf_origin_password_and_request_bounds(authenticated: TestClient) -> None:
    client = authenticated
    for password in ["12345", "x" * 129]:
        assert client.post(PREFIX + "export", json={"password": password}).status_code == 422
    assert client.post(PREFIX + "export", json={"password": PASSWORD}, headers={"Origin": "https://foreign.example"}).status_code == 403
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post(PREFIX + "export", json={"password": PASSWORD}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    response = client.post(PREFIX + "export", content=b" " * (MAX_BACKUP_REQUEST_BYTES + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    client.cookies.clear()
    assert client.post(PREFIX + "export", json={"password": PASSWORD}).status_code == 401


def test_invalid_plaintext_and_kdf_busy(authenticated: TestClient) -> None:
    service = ConfigBackupService(authenticated.app.state.settings)
    backup = service.export(PASSWORD)["backup"]
    assert service._kdf_slot.acquire(blocking=False)
    try:
        with pytest.raises(AppError, match="正在处理其他配置备份"):
            service.decrypt(BackupEnvelope.model_validate(backup), PASSWORD)
    finally:
        service._kdf_slot.release()
    import base64

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.services.config_backup import AAD
    envelope = copy.deepcopy(backup)
    key = service._key(PASSWORD, base64.b64decode(envelope["salt"]))
    incomplete = service.decrypt(BackupEnvelope.model_validate(backup), PASSWORD).model_dump()
    incomplete["settings"]["proxy"].pop("password")
    for invalid in [b'{"settings":{}}', b'{"created_at":"today","settings":{},"excluded_fields":[]}',
                    json.dumps(incomplete).encode()]:
        envelope["ciphertext"] = base64.b64encode(AESGCM(key).encrypt(base64.b64decode(envelope["nonce"]), invalid, AAD)).decode()
        with pytest.raises(AppError, match="备份密码不正确"):
            service.decrypt(BackupEnvelope.model_validate(envelope), PASSWORD)


def test_missing_target_root_is_atomic(authenticated: TestClient, config: Config) -> None:
    backup = export(authenticated)
    before = authenticated.app.state.settings.read()[:2]
    config.download_root.rmdir()
    for endpoint in ("preview", "restore"):
        request = {"backup": backup, "password": PASSWORD}
        if endpoint == "restore":
            request["revision"] = before[0]
        response = authenticated.post(PREFIX + endpoint, json=request)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert authenticated.app.state.settings.read()[:2] == before


def test_success_keeps_tasks_media_and_safety_state(authenticated: TestClient, config: Config) -> None:
    backup = export(authenticated)
    state = authenticated.app.state
    media = config.download_root / "preserved.mp4"
    media.write_bytes(b"media-is-not-configuration")
    with state.db.transaction() as session:
        session.add(RequestSafety(credential="synthetic-credential", values={"auth_paused": True, "retry_at": 12345}))
        session.add(MediaRequest(id="preserved-task", bot_id=7, chat_id=42, user_id=42, collect_until=0,
                                 status="succeeded", title="private title"))
    revision = state.settings.read()[0]
    response = authenticated.post(PREFIX + "restore", json={"backup": backup, "password": PASSWORD, "revision": revision})
    assert response.status_code == 200, response.text
    assert media.read_bytes() == b"media-is-not-configuration"
    with state.db.sessions() as session:
        assert session.get(MediaRequest, "preserved-task").title == "private title"
        assert session.get(RequestSafety, "synthetic-credential").values == {"auth_paused": True, "retry_at": 12345}


def test_restore_is_portable_across_independent_master_keys_and_administrators(
    authenticated: TestClient, config: Config, tmp_path: Path,
) -> None:
    save(authenticated, bot_token=TOKEN, allowed_user_ids=[42], telegram_api_hash=API_HASH,
         proxy={"host": "proxy.example", "password": "portable-proxy-secret"}, download_concurrency=6)
    backup = export(authenticated)
    target_root = tmp_path / "independent-instance"
    for name in ("data", "downloads", "cache"):
        (target_root / name).mkdir(parents=True)
    target_config = Config(data_dir=target_root / "data", download_root=target_root / "downloads",
                           bot_api_root=target_root / "cache", static_dir=target_root / "static",
                           minimum_free_bytes=1024, run_background=False)
    with TestClient(create_app(target_config)) as target:
        credentials = {"username": "new-administrator", "password": "target-password"}
        response = target.post("/api/v1/setup", json=credentials | {
            "bootstrap_secret": (target_config.data_dir / "bootstrap.key").read_text()})
        assert response.status_code == 200
        response = target.post("/api/v1/auth/login", json=credentials)
        assert response.status_code == 200
        target.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        target_key = (target_config.data_dir / "master.key").read_bytes()
        assert target_key != (config.data_dir / "master.key").read_bytes()
        with target.app.state.db.sessions() as session:
            administrator = session.get(Admin, 1)
            original_admin = administrator.username, administrator.password_hash
        preview = target.post(PREFIX + "preview", json={"backup": backup, "password": PASSWORD})
        assert preview.status_code == 200
        response = target.post(PREFIX + "restore", json={"backup": backup, "password": PASSWORD,
                                                         "revision": preview.json()["revision"]})
        assert response.status_code == 200, response.text
        _, restored, _ = target.app.state.settings.read()
        assert (restored.bot_token, restored.telegram_api_hash, restored.proxy.password, restored.download_concurrency) == (
            TOKEN, API_HASH, "portable-proxy-secret", 6)
        with target.app.state.db.sessions() as session:
            administrator = session.get(Admin, 1)
            assert (administrator.username, administrator.password_hash) == original_admin
            stored = session.get(Settings, 1).values
            assert target.app.state.settings.secrets.decrypt(stored["bot_token"]) == TOKEN
            assert TOKEN not in json.dumps(stored)
        assert (target_config.data_dir / "master.key").read_bytes() == target_key
        assert target.get("/api/v1/auth/me").json()["username"] == "new-administrator"
