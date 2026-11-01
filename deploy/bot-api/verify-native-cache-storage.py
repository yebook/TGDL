"""Network-none synthetic media acceptance using genuine pinned TDLib workers."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location('transport', Path(__file__).with_name('verify-native-transport.py'))
assert _spec and _spec.loader
transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transport)
TOKEN = '123456:' + 'A' * 35
GENERATION = 'synthetic-storage-generation'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Service:
    def __init__(self, root: Path, binary: str) -> None:
        self.root = root
        (root / 'tmp').mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        self.trace = root / 'connect.trace'
        self.process = subprocess.Popen([
            'strace', '-f', '-e', 'trace=connect', '-o', str(self.trace), binary,
            '--local', '--http-ip-address=127.0.0.1', f'--http-port={self.port}',
            f'--dir={root}', f'--temp-dir={root / "tmp"}', '--verbosity=0',
        ], env=dict(os.environ, TELEGRAM_API_ID='12345', TELEGRAM_API_HASH='a' * 32,
                    TGD_NATIVE_PROXY_MODE='direct', TGD_PROGRESS_GENERATION=GENERATION),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        transport.wait_for_listener(self.port, self.process)

    def call(self, method: str, **fields: str) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/bot{TOKEN}/{method}',
                                         data=urllib.parse.urlencode(fields).encode())
        try:
            with OPENER.open(request, timeout=3) as response:
                return response.status, json.loads(response.read(32768))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read(32768))

    def snapshot(self, refresh: str = '0') -> dict[str, Any]:
        code, value = self.call('tgdGetCacheStorage', generation=GENERATION, refresh=refresh)
        assert code == 200, (code, value)
        result = value['result']
        assert set(result) == {'generation', 'scope', 'scan_state', 'usage', 'operation'}
        assert result['generation'] == GENERATION and result['scope'] == 'current_bot'
        if result['usage'] is not None:
            assert set(result['usage']) == {'used_bytes', 'file_count'}
            assert all(isinstance(n, int) and n >= 0 for n in result['usage'].values())
        operation = result['operation']
        if operation is not None:
            assert set(operation) == {'operation_id', 'state', 'before_bytes', 'after_bytes',
                                      'released_bytes', 'error_code'}
        return result

    def wait(self, predicate: Any) -> dict[str, Any]:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            result = self.snapshot()
            if predicate(result):
                return result
            time.sleep(0.03)
        raise AssertionError('Synthetic cache operation did not finish')

    def close(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)  # type: ignore[attr-defined]
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
            self.process.wait(timeout=5)
        assert not transport.connection_destinations(self.trace), 'Native storage connected outside loopback'


def refresh(service: Service) -> dict[str, Any]:
    assert service.snapshot('1')['scan_state'] == 'running'
    return service.wait(lambda value: value['scan_state'] != 'running')


def cleanup(service: Service, operation_id: str) -> dict[str, Any]:
    code, response = service.call('tgdClearCacheStorage', generation=GENERATION, operation_id=operation_id)
    assert code == 200, (code, response)
    assert response['result']['operation']['state'] == 'running'
    return service.wait(lambda value: value['operation']['state'] != 'running')


def main() -> None:
    transport.assert_network_none()
    assert os.getuid() != 0, 'Permission-failure fixture must run as a non-owner of root'  # type: ignore[attr-defined]
    subprocess.run(['/verification/test-cache-storage'], check=True)
    subprocess.run(['/verification/test-cache-cleanup'], check=True)
    with tempfile.TemporaryDirectory(prefix='tgd-storage-') as temporary:
        root = Path(temporary)
        production = root / 'production'
        production.mkdir()
        native = Service(production, '/usr/local/bin/telegram-bot-api')
        try:
            for method, fields in (
                ('tgdGetCacheStorage', {'generation': GENERATION, 'refresh': '0'}),
                ('tgdGetCacheStorage', {'generation': GENERATION, 'refresh': '1'}),
                ('tgdGetCacheStorage', {}),
                ('tgdClearCacheStorage', {'generation': GENERATION, 'operation_id': 'a' * 32}),
                ('tgdClearCacheStorage', {'generation': 'stale', 'operation_id': 'A' * 4097}),
            ):
                assert native.call(method, **fields)[0] == 404
            assert not list(production.glob('123456*')), 'Private route created a Client'
        finally:
            native.close()
        fixture_root = root / 'fixture'
        fixture_root.mkdir()
        service = Service(fixture_root, '/verification/cache-storage-fixture')
        try:
            service.call('tgdFixtureInit')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                code, _ = service.call('tgdGetCacheStorage', generation=GENERATION, refresh='0')
                if code == 200:
                    break
                time.sleep(0.05)
            initial = service.snapshot()
            assert initial == {'generation': GENERATION, 'scope': 'current_bot', 'scan_state': 'idle',
                               'usage': None, 'operation': None}
            bot = next(fixture_root.glob('123456*'))
            videos, partials = bot / 'videos', bot / 'temp'
            videos.mkdir(exist_ok=True)
            partials.mkdir(exist_ok=True)
            media = [videos / 'orphan.mp4', partials / 'cancelled.part', videos / 'empty', partials / 'sparse']
            media[0].write_bytes(b'o' * 5000)
            media[1].write_bytes(b'p' * 3000)
            media[2].touch()
            with media[3].open('wb') as stream:
                stream.truncate(64 * 1024 * 1024)
            (videos / '.nomedia').touch()
            protected = [bot / 'auth-sentinel', fixture_root / 'final-output.mp4',
                         fixture_root / 'another-bot' / 'videos' / 'keep.mp4']
            for path in protected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'must remain')
            expected_bytes = sum(path.stat().st_blocks * 512 for path in media)
            result = refresh(service)
            assert result['usage'] == {'used_bytes': expected_bytes, 'file_count': 4}, result
            # Native download/upload activity refuses cleanup before accepting its ID.
            for kind in ('download', 'upload'):
                assert service.call('tgdFixtureActivity', kind=kind)[0] == 200
                assert refresh(service)['usage'] == {'used_bytes': expected_bytes, 'file_count': 4}
                code, failure = service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='c' * 32)
                assert code == 409 and failure['description'] == 'Conflict: TGD_CACHE_STORAGE_BUSY'
                assert service.snapshot()['operation'] is None
            service.call('tgdFixtureActivity', kind='idle')
            # A new scan cannot cancel an ongoing cleanup. All status projections
            # are reads of the same retained operation, including refresh=1.
            code, accepted = service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='a' * 32)
            assert code == 200 and accepted['result']['operation']['state'] == 'running'
            assert service.call('getFile', file_id='synthetic')[1]['description'] == 'Bad Request: TGD_CACHE_CLEANUP_BUSY'
            assert service.call('sendDocument', chat_id='1', document='synthetic')[0] == 409
            # The first fullscan is done and GC is genuinely pending now.
            service.wait(lambda value: value['scan_state'] == 'ready' and value['operation']['state'] == 'running')
            for _ in range(4):
                assert service.snapshot('1')['operation']['operation_id'] == 'a' * 32
                same = service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='a' * 32)
                assert same[0] == 200
            assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='b' * 32)[0] == 409
            result = service.wait(lambda value: value['operation']['state'] != 'running')
            assert result['operation']['state'] == 'succeeded', result
            assert result['usage'] == {'used_bytes': 0, 'file_count': 0}, result
            assert result['operation']['released_bytes'] == expected_bytes
            assert all(not path.exists() for path in media)
            assert all(path.read_bytes() == b'must remain' for path in protected)
            # Same terminal ID must not delete files created after that operation.
            retained = videos / 'later.mp4'
            retained.write_bytes(b'later')
            repeated = service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='a' * 32)
            assert repeated[1]['result']['operation'] == result['operation'] and retained.exists()
            # Scan errors are unknown, never a fabricated empty successful cache.
            videos.chmod(0)
            try:
                failed = refresh(service)
                assert failed['scan_state'] == 'failed' and failed['usage'] is None, failed
            finally:
                videos.chmod(0o700)
            assert refresh(service)['usage']['file_count'] == 1
            # A symlink in place of a media directory cannot escape this Bot scope.
            parked = bot / 'parked-videos'
            videos.rename(parked)
            videos.symlink_to(protected[2].parent, target_is_directory=True)
            try:
                failed = refresh(service)
                assert failed['scan_state'] == 'failed' and failed['usage'] is None
                assert protected[2].read_bytes() == b'must remain'
            finally:
                videos.unlink()
                parked.rename(videos)
            # Readable but unwritable media directory: TDLib logs unlink failure.
            # After-scan must preserve actual remaining bytes, never removed_stats.
            before = retained.stat().st_blocks * 512
            videos.chmod(0o500)
            try:
                result = cleanup(service, 'b' * 32)
                assert retained.exists()
                assert result['operation']['released_bytes'] == 0
                assert result['usage'] == {'used_bytes': before, 'file_count': 1}
            finally:
                videos.chmod(0o700)
            assert cleanup(service, 'd' * 32)['usage'] == {'used_bytes': 0, 'file_count': 0}
            assert all(path.read_bytes() == b'must remain' for path in protected)
        finally:
            service.close()
    print(json.dumps({'production_no_client': 'passed', 'actual_tdlib_storage_workers': 'passed',
                      'orphan_partial_empty_sparse': 'passed', 'allocated_bytes_and_count': 'passed',
                      'singleflight_dedup_and_native_activity': 'passed', 'permission_failures': 'passed',
                      'auth_output_other_bot_preserved': 'passed', 'outbound_connections': 0,
                      'real_telegram_tested': False}))


if __name__ == '__main__':
    main()
