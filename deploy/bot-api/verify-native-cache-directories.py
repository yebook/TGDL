"""Isolated current-Bot empty directories and genuine TDLib lazy writer acceptance."""
from __future__ import annotations

import concurrent.futures
import subprocess
import time
from pathlib import Path
from typing import Any


def verify(service: Any, bot: Path, root: Path, storage: Any, seed: Any, cleanup: Any, success: Any) -> None:
    subprocess.run(['/verification/test-cache-directories'], check=True)
    assert service.call('tgdFixtureTypeDirectories') == (200, {'ok': True, 'result': True}), \
        'Native media directory allowlist differs from pinned TDLib file types'
    alias, path, content = seed(service, bot, 700)
    sibling = path.parent / 'shared.mp4'
    sibling.write_bytes(b'shared-cache')
    success(cleanup(service, alias, path, content))
    assert path.parent.is_dir() and sibling.read_bytes() == b'shared-cache'
    sibling.unlink()  # This synthetic test owns its unrelated sentinel.
    alias, path, content = seed(service, bot, 701)
    success(cleanup(service, alias, path, content))
    assert not path.parent.exists(), 'Last-file automatic cleanup left an empty media directory'
    path.parent.mkdir()
    success(cleanup(service, alias, path, content), 'absent')
    assert path.parent.is_dir(), 'Absent replay pruned a newly recreated directory'

    hold = Path('/tmp/tgd-directory-result.hold')
    entered = Path('/tmp/tgd-directory-result.entered')
    for serial, replace_parent in ((702, True), (703, False)):
        alias, path, content = seed(service, bot, serial)
        entered.unlink(missing_ok=True)
        hold.touch()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(cleanup, service, alias, path, content)
                deadline = time.monotonic() + 5
                while not entered.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert entered.exists() and not path.exists(), 'Actual unlink/result boundary was not reached'
                if replace_parent:
                    original = bot / 'retained-original-videos'
                    path.parent.rename(original)
                    path.parent.mkdir()
                else:
                    (path.parent / 'concurrent-entry').write_bytes(b'new-cache')
                hold.unlink()
                success(pending.result(timeout=5))
        finally:
            hold.unlink(missing_ok=True)
            entered.unlink(missing_ok=True)
        assert path.parent.is_dir(), 'Late replacement/nonempty parent was pruned'
        if replace_parent:
            assert original.is_dir(), 'Detached original parent was pruned'
            original.rmdir()  # Only this empty synthetic fixture directory.
        else:
            (path.parent / 'concurrent-entry').unlink()

    def write() -> Path:
        code, value = service.call('tgdFixtureWrite')
        assert code == 200, (code, value)
        result = Path(value['result'])
        assert result.parent == bot / 'videos' and result.read_bytes() == b'native write after empty directory pruning'
        assert (bot / 'temp').is_dir(), 'Real TDLib temp write did not run'
        return result

    # Initialize genuine TDLib temp/final writer state before manual pruning.
    written = write()
    for relative in ('photos/old/nested', 'documents', 'temp/old/empty', 'videos/empty-child'):
        (bot / relative).mkdir(parents=True, exist_ok=True)
    auth = bot / 'directory-auth-sentinel'
    auth.mkdir()
    protected = [auth, root / 'other-bot-directory-test' / 'videos', root / 'final-media-directory-test']
    for candidate in protected:
        candidate.mkdir(parents=True, exist_ok=True)
    runtime = bot / 'td.binlog'
    runtime_before = runtime.stat().st_ino
    before = storage.refresh(service)
    assert before['scan_state'] == 'ready' and before['usage']['file_count'] >= 1
    cleared = storage.cleanup(service, 'd' * 32)
    assert cleared['operation']['state'] == 'succeeded', cleared
    assert not written.exists()
    for relative in ('photos', 'documents', 'temp', 'videos'):
        assert not (bot / relative).exists(), f'Empty media subtree remains: {relative}'
    assert runtime.stat().st_ino == runtime_before and all(item.is_dir() for item in protected)
    assert bot.is_dir(), 'Shared Bot/authentication root must survive cleanup'
    assert storage.refresh(service)['usage']['file_count'] == 0
    written = write()
    assert storage.refresh(service)['usage']['file_count'] == 1
    # Leave the fixture clean for existing recovery checks, through actual GC.
    assert storage.cleanup(service, 'e' * 32)['operation']['state'] == 'succeeded'
    assert not written.exists()
    (bot / 'photos' / 'only-empty').mkdir(parents=True)
    (bot / 'temp' / 'only-empty').mkdir(parents=True)
    assert storage.refresh(service)['usage'] == {'used_bytes': 0, 'file_count': 0}
    assert storage.cleanup(service, 'f' * 32)['operation']['state'] == 'succeeded'
    assert not (bot / 'photos').exists() and not (bot / 'temp').exists(), \
        'Zero-byte/zero-file cache cleanup did not prune empty-only residual directories'
    print('PASS: automatic last-file prune, shared parent, absent replay, actual unlink/result replacement/new entry, '
          'manual residual empty subtrees, '
          'Bot/auth/other-Bot/final roots preserved, initialized TDLib temp/final writer recreation and live stats')
