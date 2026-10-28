"""No-network task-list SELECT counts against isolated temporary storage."""
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.core.config import Config
from app.main import create_app
from app.db.models import MediaRequest, MediaItem

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for name in ("data", "downloads", "cache"):
        (root / name).mkdir()
    config = Config(data_dir=root / "data", download_root=root / "downloads",
                    bot_api_root=root / "cache", static_dir=root / "static",
                    minimum_free_bytes=1024, run_background=False)
    with TestClient(create_app(config)) as client:
        setup = client.post("/api/v1/setup", json={
            "bootstrap_secret": (config.data_dir / "bootstrap.key").read_text(),
            "username": "admin", "password": "synthetic-password"})
        assert setup.status_code == 200
        login = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "synthetic-password"})
        assert login.status_code == 200
        db, service = client.app.state.db, client.app.state.tasks
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", capture)
        previous = 0
        for n in (0, 1, 20):
            with db.transaction() as session:
                for index in range(previous, n):
                    request = MediaRequest(bot_id=7, chat_id=42, user_id=42,
                                           title=f"Synthetic {index}", status="expired",
                                           collect_until=time.time() - 100)
                    session.add(request)
                    session.flush()
                    session.add(MediaItem(request_id=request.id, bot_id=7, chat_id=42,
                                          message_id=index + 1, file_id=f"fake-{index}",
                                          media_type="video", size_bytes=123))
            previous = n
            statements.clear()
            direct = service.list_tasks(None, None, 1, 20)
            assert len(direct["items"]) == n
            direct_count = len(statements)
            statements.clear()
            response = client.get("/api/v1/tasks?page=1&page_size=20")
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) == n
            http_count = len(statements)
            tables = Counter(statement.split("FROM ", 1)[1].split()[0]
                             for statement in statements if "FROM " in statement)
            print(json.dumps({"tasks": n, "direct_select": direct_count,
                              "http_select": http_count, "http_tables": dict(tables)}))
        event.remove(db.engine, "before_cursor_execute", capture)
