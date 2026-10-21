from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import Config
from app.db.models import Settings


@pytest.mark.parametrize("password", ["123456", "x" * 20])
def test_setup_secret_session_and_csrf(client: TestClient, config: Config, password: str) -> None:
    assert client.get("/api/v1/setup/status").json() == {"initialized": False}
    assert client.get("/api/v1/settings").status_code == 401
    body = {"bootstrap_secret": "wrong", "username": "admin", "password": "a-long-test-password"}
    assert client.post("/api/v1/setup", json=body).status_code == 403
    body["bootstrap_secret"] = (config.data_dir / "bootstrap.key").read_text()
    for invalid_password in ("12345", "x" * 21):
        body["password"] = invalid_password
        assert client.post("/api/v1/setup", json=body).status_code == 422
        assert client.get("/api/v1/setup/status").json() == {"initialized": False}
        assert (config.data_dir / "bootstrap.key").exists()
    body["password"] = password
    assert client.post("/api/v1/setup", json=body).status_code == 200
    assert not (config.data_dir / "bootstrap.key").exists()
    assert client.post("/api/v1/setup", json=body).status_code == 409
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": body["password"]})
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.patch("/api/v1/settings", json={"revision": 1, "download_concurrency": 2}).status_code == 403
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    assert client.patch("/api/v1/settings", json={"revision": 1, "download_concurrency": 2}, headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_atomic_setup_race(client: TestClient, config: Config) -> None:
    body = {"bootstrap_secret": (config.data_dir / "bootstrap.key").read_text(),
            "username": "admin", "password": "test-password"}
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v1/setup", json=body).status_code, range(2)))
    assert sorted(responses) == [200, 409]


def test_settings_revision_defaults_env_redaction(authenticated: TestClient, monkeypatch) -> None:
    client = authenticated
    settings = client.get("/api/v1/settings").json()
    assert [settings["values"][key] for key in ("confirmation_timeout_seconds", "download_concurrency", "max_auto_retries")] == [1800, 3, 3]
    rev = settings["revision"]
    assert client.patch("/api/v1/settings", json={"revision": rev, "confirmation_timeout_seconds": 90}).status_code == 200
    assert client.patch("/api/v1/settings", json={"revision": rev, "confirmation_timeout_seconds": 120}).status_code == 409
    monkeypatch.setenv("TGD_DOWNLOAD_CONCURRENCY", "4")
    settings = client.get("/api/v1/settings").json()
    assert settings["values"]["download_concurrency"] == 4
    assert settings["sources"]["download_concurrency"] == "environment"
    assert client.patch("/api/v1/settings", json={"revision": settings["revision"], "download_concurrency": 5}).status_code == 400
    assert client.patch("/api/v1/settings", json={"revision": settings["revision"], "download_root_id": "../../etc"}).status_code == 400
    response = client.patch("/api/v1/settings", json={"revision": settings["revision"], "proxy": {
        "enabled": False, "host": "proxy.lan", "password": "secret-proxy-password"}})
    assert response.status_code == 200
    assert "secret-proxy-password" not in response.text
    assert response.json()["configured"]["proxy_password"] is True
    response = client.patch("/api/v1/settings", json={"revision": response.json()["revision"], "proxy": {"host": "proxy2.lan"}})
    assert response.json()["configured"]["proxy_password"] is True
    response = client.patch("/api/v1/settings", json={"revision": response.json()["revision"], "clear_proxy_password": True})
    assert response.json()["configured"]["proxy_password"] is False


@pytest.mark.parametrize("password", ["123456", "x" * 20])
def test_origin_password_change_and_validation(authenticated: TestClient, password: str) -> None:
    client = authenticated
    assert client.patch("/api/v1/settings", json={"revision": 1}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    response = client.patch("/api/v1/settings", json={"revision": 1, "download_concurrency": 0})
    assert response.status_code == 422
    assert response.json()["error"]["request_id"]
    for invalid_password in ("12345", "x" * 21):
        response = client.post("/api/v1/auth/password", json={
            "current_password": "a-long-test-password", "new_password": invalid_password})
        assert response.status_code == 422
        assert client.get("/api/v1/auth/me").status_code == 200
    response = client.post("/api/v1/auth/password", json={"current_password": "a-long-test-password", "new_password": password})
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "a-long-test-password"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": password}).status_code == 200


def test_health_and_system_do_not_require_bot(authenticated: TestClient) -> None:
    assert authenticated.get("/health/live").status_code == 200
    response = authenticated.get("/api/v1/system/status")
    assert response.status_code == 200
    assert response.json()["bot"]["state"] == "unconfigured"
    assert response.json()["storage"]["roots"][0]["available"] is True


def test_login_throttle(client: TestClient) -> None:
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json={"username": "x", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "x", "password": "wrong-pass"}).status_code == 429


def test_bot_settings_save_offline_and_manual_validation_never_saves(authenticated: TestClient, monkeypatch) -> None:
    from app.bot.client import TelegramClient

    identity = AsyncMock(side_effect=OSError("upstream unavailable"))
    monkeypatch.setattr(TelegramClient, "identity", identity)
    token = "123456:synthetic-token-for-local-tests"
    client = authenticated
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.patch("/api/v1/settings", json={"revision": revision, "bot_token": token, "allowed_user_ids": [42]})
    assert response.status_code == 200
    identity.assert_not_awaited()
    assert token not in response.text
    assert response.json()["configured"]["bot_token"] is True
    with client.app.state.db.sessions() as session:
        assert token not in str(session.get(Settings, 1).values)
    saved = client.get("/api/v1/settings").json()

    failed = client.post("/api/v1/settings/validate", json={})
    assert failed.status_code == 422
    assert failed.json()["error"]["code"] == "BOT_VALIDATION_FAILED"
    assert token not in failed.text
    assert client.get("/api/v1/settings").json() == saved

    identity.side_effect = None
    identity.return_value = {"id": 123456, "username": "test_bot"}
    candidate_token = "654321:another-synthetic-token"
    checked = client.post("/api/v1/settings/validate", json={"bot_token": candidate_token, "allowed_user_ids": [43]})
    assert checked.status_code == 200
    assert checked.json()["bot"]["username"] == "test_bot"
    assert client.app.state.settings.read()[1].bot_token == token
    assert client.get("/api/v1/settings").json() == saved

    # Blank retains encrypted credentials; explicit clear removes them without a probe.
    identity.reset_mock()
    kept = client.patch("/api/v1/settings", json={"revision": saved["revision"], "bot_token": "", "allowed_user_ids": [44]})
    assert kept.status_code == 200
    assert client.app.state.settings.read()[1].bot_token == token
    cleared = client.patch("/api/v1/settings", json={"revision": kept.json()["revision"], "clear_bot_token": True})
    assert cleared.status_code == 200
    assert cleared.json()["configured"]["bot_token"] is False
    identity.assert_not_awaited()


@pytest.mark.parametrize("values", [{"bot_token": "not a token", "allowed_user_ids": [42]}, {"allowed_user_ids": [-1]}])
def test_offline_save_still_rejects_invalid_fields(authenticated: TestClient, values: dict) -> None:
    saved = authenticated.get("/api/v1/settings").json()
    response = authenticated.patch("/api/v1/settings", json={"revision": saved["revision"], **values})
    assert response.status_code == 422
    assert authenticated.get("/api/v1/settings").json() == saved
