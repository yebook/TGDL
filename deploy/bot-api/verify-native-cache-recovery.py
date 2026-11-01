"""Network-none real pinned TDLib recovery acceptance; synthetic files only."""
from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
import runpy
import stat
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location('storage', Path(__file__).with_name('verify-native-cache-storage.py'))
assert _spec and _spec.loader
storage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(storage)
GENERATION = 'synthetic-cache-recovery-one'
storage.GENERATION = GENERATION
Service = storage.Service
BINARY = '/verification/cache-recovery-fixture'
HASH_HOLD = Path('/tmp/tgd-recovery-hash.hold')
HASH_ENTERED = Path('/tmp/tgd-recovery-hash.entered')
UNLINK_FAIL = Path('/tmp/tgd-recovery-unlink.fail')
REUSED = Path('/tmp/tgd-recovery-reused-id')
UNLINK_HOLD = Path('/tmp/tgd-recovery-unlink.hold')
UNLINK_ENTERED = Path('/tmp/tgd-recovery-unlink.entered')
REGISTRATION_HOLD = Path('/tmp/tgd-recovery-registration.hold')
REGISTRATION_ENTERED = Path('/tmp/tgd-recovery-registration.entered')


def wait_for(predicate: Any, message: str) -> Any:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(message)


def init(root: Path) -> Any:
    if (root / 'tmp').exists():
        (root / 'tmp').rmdir()  # Empty synthetic HTTP scratch only, for same-root restart.
    service = Service(root, BINARY)
    service.call('tgdFixtureInit')
    wait_for(lambda: service.call('tgdGetCacheStorage', generation=GENERATION, refresh='0')[0] == 200,
             'Offline fixture Client initialization failed')
    return service


def lookup(service: Any, alias: str) -> str:
    code, value = service.call('tgdFixtureLookup', file_id=alias)
    assert code == 200, (code, value)
    return value['result']


def seed(service: Any, bot: Path, serial: int, content: bytes = b'completed-media' * 8192) -> tuple[str, Path, bytes]:
    path = bot / 'videos' / f'synthetic-{serial}.mp4'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    code, value = service.call('tgdFixtureSeed', serial=str(serial), path=str(path))
    assert code == 200, (code, value)
    alias = value['result']
    assert lookup(service, alias) == str(path), 'Genuine TDLib registration was not created'
    return alias, path, content


def fields(alias: str, path: Path, content: bytes) -> dict[str, str]:
    return {'generation': GENERATION, 'file_id': alias, 'expected_path': str(path),
            'expected_size': str(len(content)), 'expected_sha256': hashlib.sha256(content).hexdigest()}


def cleanup(service: Any, alias: str, path: Path, content: bytes, **overrides: str) -> tuple[int, dict[str, Any]]:
    request = fields(alias, path, content)
    request.update(overrides)
    return service.call('tgdDeleteFileCache', **request)


def success(result: tuple[int, dict[str, Any]], status: str = 'deleted') -> None:
    code, value = result
    assert code == 200 and value['result'] == {'generation': GENERATION, 'status': status}, result


def main() -> None:
    global GENERATION
    storage.transport.assert_network_none()
    assert os.getuid() != 0
    markers = (HASH_HOLD, HASH_ENTERED, UNLINK_FAIL, REUSED, UNLINK_HOLD, UNLINK_ENTERED,
               REGISTRATION_HOLD, REGISTRATION_ENTERED)
    for marker in markers:
        marker.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='tgd-cache-recovery-') as temporary:
        production_root = Path(temporary) / 'production'
        production_root.mkdir()
        production = Service(production_root, '/usr/local/bin/telegram-bot-api')
        try:
            for request in ({}, fields('synthetic-alias', production_root / 'videos' / 'unused', b'known')):
                assert production.call('tgdDeleteFileCache', **request)[0] == 404
            assert not list(production_root.glob('123456*')), 'Private production route created a Client'
        finally:
            production.close()
        root = Path(temporary) / 'fixture'
        root.mkdir()
        service = init(root)
        try:
            bot = next(root.glob('123456*'))
            directory_checks = runpy.run_path(str(Path(__file__).with_name('verify-native-cache-directories.py')))
            directory_checks['verify'](service, bot, root, storage, seed, cleanup, success)
            alias, path, content = seed(service, bot, 1)
            success(cleanup(service, alias, path, content))
            assert not path.exists(), 'Ordinary alias cleanup did not remove actual file'
            success(cleanup(service, alias, path, content), 'absent')

            alias, path, content = seed(service, bot, 2)
            reused_ids = []
            previous_mode = stat.S_IMODE(path.parent.stat().st_mode)
            path.parent.chmod(0o500)  # Real EACCES from pinned TDLib unlink, nonroot.
            try:
                assert cleanup(service, alias, path, content)[0] == 500
                assert path.read_bytes() == content and lookup(service, alias) == '', 'Unlink failure precondition missing'
                for _ in range(3):
                    assert cleanup(service, alias, path, content)[0] == 500
                    reused_ids.append(REUSED.read_text())
                    assert path.read_bytes() == content and lookup(service, alias) == ''
            finally:
                path.parent.chmod(previous_mode)
            assert len(set(reused_ids)) == 1, 'Failed retries allocated unbounded new local registrations'
            success(cleanup(service, alias, path, content))
            assert not path.exists(), 'Recovery did not repair stale local index after failed unlink'

            # Same-child stale REMOTE alias restoration must roll back when a
            # post-registration check fails. Otherwise retry's normal fast path
            # could delete same-size changed bytes without hashing them.
            rollback_alias, rollback_path, rollback_content = seed(service, bot, 9)
            UNLINK_FAIL.touch()
            assert cleanup(service, rollback_alias, rollback_path, rollback_content)[0] == 500
            UNLINK_FAIL.unlink()
            assert lookup(service, rollback_alias) == ''
            REGISTRATION_HOLD.touch()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(cleanup, service, rollback_alias, rollback_path, rollback_content)
                wait_for(REGISTRATION_ENTERED.exists, 'Recovery registration boundary was not reached')
                changed = b'X' * len(rollback_content)
                replacement = rollback_path.with_suffix('.new')
                replacement.write_bytes(changed)
                replacement.replace(rollback_path)
                REGISTRATION_HOLD.unlink()
                assert pending.result(timeout=4)[0] == 409
            assert lookup(service, rollback_alias) == '', 'Failed recovery retained a restored remote local association'
            rejected = cleanup(service, rollback_alias, rollback_path, rollback_content)
            assert rejected[0] == 409 and rejected[1]['description'] == 'Conflict: cache recovery content mismatch', rejected
            assert rollback_path.read_bytes() == changed, 'Retry bypassed hash after failed restoration'

            old_alias, old_path, old_content = seed(service, bot, 3)
            race_alias, race_path, race_content = seed(service, bot, 4)
            lifetime_alias, lifetime_path, lifetime_content = seed(service, bot, 5)
            invalid_alias, invalid_path, invalid_content = seed(service, bot, 6)
            unlink_alias, unlink_path, unlink_content = seed(service, bot, 7)
            # Exercise the public-send registration ceiling with a sparse cache
            # file: >4000MiB logical bytes, zero data-page allocation in tmpfs.
            large_size = 4000 * 1024 * 1024 + 1
            large_path = bot / 'videos' / 'synthetic-large.mp4'
            with large_path.open('wb') as stream:
                stream.truncate(large_size)
            code, value = service.call('tgdFixtureSeed', serial='8', path=str(large_path))
            assert code == 200
            large_alias = value['result']
            large_hash = hashlib.sha256()
            zeros = bytes(1024 * 1024)
            for _ in range(4000):
                large_hash.update(zeros)
            large_hash.update(b'\0')
            sentinel = bot / 'videos' / 'unrelated.mp4'
            partial = bot / 'temp' / 'cancelled-partial'
            partial.parent.mkdir(exist_ok=True)
            sentinel.write_bytes(b'unrelated-complete')
            partial.write_bytes(b'reusable-cancelled-partial')
            service.close()
            GENERATION = storage.GENERATION = 'synthetic-cache-recovery-two'
            service = init(root)
            assert lookup(service, old_alias) == '', 'Restart retained local metadata; orphan condition not exercised'
            before = storage.refresh(service)['usage']
            blocks = old_path.stat().st_blocks * 512
            success(cleanup(service, old_alias, old_path, old_content))
            assert not old_path.exists()
            after = storage.refresh(service)['usage']
            assert after['file_count'] == before['file_count'] - 1 and after['used_bytes'] == before['used_bytes'] - blocks
            assert sentinel.read_bytes() == b'unrelated-complete' and partial.read_bytes() == b'reusable-cancelled-partial'

            # Fail-closed protocol and content/type/current-Bot directory checks.
            assert cleanup(service, invalid_alias, invalid_path, invalid_content, generation='stale')[0] == 409
            for digest in ('', 'A' * 64, 'a' * 63, 'g' * 64):
                assert cleanup(service, invalid_alias, invalid_path, invalid_content, expected_sha256=digest)[0] == 400
            assert cleanup(service, invalid_alias, invalid_path, invalid_content, expected_sha256='0' * 64)[0] == 409
            assert cleanup(service, invalid_alias, invalid_path, invalid_content, expected_size=str(len(invalid_content) + 1))[0] == 409
            for candidate in (bot / 'recovery-db-sentinel.sqlite', bot / 'temp' / 'not-media', bot / 'documents' / 'wrong-type', root / 'other-bot' / 'videos' / 'other'):
                candidate.parent.mkdir(exist_ok=True, parents=True)
                candidate.write_bytes(invalid_content)
                assert cleanup(service, invalid_alias, candidate, invalid_content)[0] in (400, 409)
                assert candidate.read_bytes() == invalid_content
            link = bot / 'videos' / 'symbolic.mp4'
            link.symlink_to(invalid_path)
            assert cleanup(service, invalid_alias, link, invalid_content)[0] == 409
            assert link.is_symlink() and invalid_path.read_bytes() == invalid_content
            link.unlink()
            videos = bot / 'videos'
            moved = bot / 'saved-videos'
            videos.rename(moved)
            videos.symlink_to(moved, target_is_directory=True)
            assert cleanup(service, invalid_alias, invalid_path, invalid_content)[0] == 409
            videos.unlink()
            moved.rename(videos)
            assert invalid_path.read_bytes() == invalid_content

            # Prepared ownership still prevents cleanup, including orphan recovery.
            code, value = service.call('tgdPrepareFileAcquisition', generation=GENERATION, file_id=invalid_alias)
            assert code == 200
            owner = value['result']['acquisition_id']
            assert cleanup(service, invalid_alias, invalid_path, invalid_content)[0] == 409
            code, value = service.call('tgdCancelFileAcquisition', generation=GENERATION, acquisition_id=owner)
            assert code == 200 and value['result']['settled']

            # Real open descriptor: same-size replacement after verification began.
            HASH_ENTERED.unlink(missing_ok=True)
            HASH_HOLD.touch()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(cleanup, service, race_alias, race_path, race_content)
                wait_for(HASH_ENTERED.exists, 'Hash worker did not open proof')
                replacement = race_path.with_suffix('.new')
                replacement.write_bytes(race_content)
                replacement.replace(race_path)
                HASH_HOLD.unlink()
                assert pending.result(timeout=4)[0] == 409
            assert race_path.read_bytes() == race_content, 'Changed pathname was deleted using old descriptor proof'

            # Repeat replacement after real FileManager registration/drop, at
            # the final asynchronous TDLib unlink boundary.
            UNLINK_HOLD.touch()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(cleanup, service, unlink_alias, unlink_path, unlink_content)
                wait_for(UNLINK_ENTERED.exists, 'Verified TDLib unlink boundary was not reached')
                replacement = unlink_path.with_suffix('.new')
                replacement.write_bytes(unlink_content)
                replacement.replace(unlink_path)
                UNLINK_HOLD.unlink()
                assert pending.result(timeout=4)[0] == 409
            assert unlink_path.read_bytes() == unlink_content, 'Final unlink accepted replaced inode'

            # HTTP timeout never releases native exclusion; the real operation
            # continues and deletes once the worker is allowed to finish.
            HASH_ENTERED.unlink(missing_ok=True)
            HASH_HOLD.touch()
            request = urllib.request.Request(
                f'http://127.0.0.1:{service.port}/bot{storage.TOKEN}/tgdDeleteFileCache',
                data=urllib.parse.urlencode(fields(lifetime_alias, lifetime_path, lifetime_content)).encode())
            try:
                storage.OPENER.open(request, timeout=0.15)
                raise AssertionError('Expected disconnected HTTP waiter')
            except TimeoutError:
                pass
            wait_for(HASH_ENTERED.exists, 'Timed-out native request did not retain its worker')
            assert lifetime_path.exists()
            assert cleanup(service, invalid_alias, invalid_path, invalid_content)[0] == 409
            assert service.call('getFile', file_id=lifetime_alias)[0] == 400
            assert service.call('sendDocument', chat_id='12345', document=str(lifetime_path))[0] == 409
            delayed_send = service.call('tgdFixtureDelayedSend')
            assert delayed_send[0] == 400 and delayed_send[1]['description'] == 'Bad Request: TGD_CACHE_CLEANUP_BUSY', delayed_send
            assert service.call('tgdPrepareFileAcquisition', generation=GENERATION, file_id=invalid_alias)[0] == 409
            assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='e' * 32)[0] == 409
            assert service.call('tgdGetCacheStorage', generation=GENERATION, refresh='1')[0] == 409
            assert service.call('tgdGetCacheStorage', generation=GENERATION, refresh='0')[0] == 200
            HASH_HOLD.unlink()
            wait_for(lambda: not lifetime_path.exists(), 'Late native cleanup never completed after HTTP timeout')
            success(cleanup(service, lifetime_alias, lifetime_path, lifetime_content), 'absent')
            success(cleanup(service, invalid_alias, invalid_path, invalid_content))
            large_request = urllib.request.Request(
                f'http://127.0.0.1:{service.port}/bot{storage.TOKEN}/tgdDeleteFileCache',
                data=urllib.parse.urlencode({'generation': GENERATION, 'file_id': large_alias,
                                             'expected_path': str(large_path), 'expected_size': str(large_size),
                                             'expected_sha256': large_hash.hexdigest()}).encode())
            with storage.OPENER.open(large_request, timeout=45) as response:
                success((response.status, json.loads(response.read(32768))))
            assert not large_path.exists(), '>4000MiB recovered cache was rejected by send-file limits'
            assert sentinel.read_bytes() == b'unrelated-complete' and partial.read_bytes() == b'reusable-cancelled-partial'
        finally:
            for marker in markers:
                marker.unlink(missing_ok=True)
            service.close()
    print(json.dumps({'native_cache_recovery': 'passed', 'restart': True, 'unlink_failure_retry': True,
                      'production_unknown_client_rejected': True,
                      'stable_registration': True, 'content_identity_scope': True,
                      'registration_failure_rollback': True,
                      'replacement_rejected': True, 'http_timeout_exclusion': True,
                      'larger_than_4000_mib': True,
                      'unrelated_media_preserved': True, 'external_connections': 0}, sort_keys=True))


if __name__ == '__main__':
    main()
