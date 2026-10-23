"""Runtime identity validation, without media mounts or Telegram access."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location("tgd_init_volumes", Path(__file__).with_name("init-volumes.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("uid,gid", [(0, 0), (0, 1000), (1000, 0), (1000, 1000), (2147483647, 2147483647)])
def test_initialize_accepts_zero_independently_and_keeps_private_mode(
    tmp_path: Path, monkeypatch: Any, uid: int, gid: int,
) -> None:
    monkeypatch.setattr(module, "Path", lambda _: tmp_path)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(module.os, "open", lambda *args: 91)
    monkeypatch.setattr(module.os, "fstat", lambda _: tmp_path.stat())
    monkeypatch.setattr(module.os, "fchown", lambda *args: calls.append(("chown", *args)), raising=False)
    monkeypatch.setattr(module.os, "fchmod", lambda *args: calls.append(("chmod", *args)), raising=False)
    monkeypatch.setattr(module.os, "close", lambda *args: calls.append(("close", *args)))
    monkeypatch.setattr(module.os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(module.os, "O_NOFOLLOW", 0, raising=False)
    module.initialize(uid, gid)
    assert calls == [("chown", 91, uid, gid), ("chmod", 91, 0o700), ("close", 91)]


@pytest.mark.parametrize("uid,gid", [(-1, 0), (0, -1), (2147483648, 0), (0, 2147483648),
                                    ("0", 0), (0, "0"), (True, 0), (0, 0.0)])
def test_initialize_rejects_invalid_ids_before_opening_volume(monkeypatch: Any, uid: Any, gid: Any) -> None:
    def unexpected(*args: Any) -> None:
        raise AssertionError("Invalid IDs must not access a volume")

    monkeypatch.setattr(module.os, "open", unexpected)
    with pytest.raises(ValueError, match="numeric IDs"):
        module.initialize(uid, gid)


def test_initialize_still_rejects_non_directory(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "not-a-volume"
    path.write_text("untouched")
    assert stat.S_ISREG(path.stat().st_mode)
    monkeypatch.setattr(module, "Path", lambda _: path)
    with pytest.raises(ValueError, match="private volume directory"):
        module.initialize(0, 0)
    assert path.read_text() == "untouched"
