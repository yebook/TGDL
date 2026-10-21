from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Config
from app.main import create_app


@pytest.fixture
def config(tmp_path: Path) -> Config:
    for name in ("data", "downloads", "cache"):
        (tmp_path / name).mkdir()
    return Config(data_dir=tmp_path / "data", download_root=tmp_path / "downloads",
                  bot_api_root=tmp_path / "cache", static_dir=tmp_path / "static",
                  minimum_free_bytes=1024, run_background=False)


@pytest.fixture
def client(config: Config) -> Iterator[TestClient]:
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture
def authenticated(client: TestClient, config: Config) -> TestClient:
    response = client.post("/api/v1/setup", json={"bootstrap_secret": (config.data_dir / "bootstrap.key").read_text(),
                                               "username": "admin", "password": "a-long-test-password"})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "a-long-test-password"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
