"""Offline real-actor cancellation acceptance; never use a real Bot or cache."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location('storage', Path(__file__).with_name('verify-native-cache-storage.py'))
assert _spec and _spec.loader
storage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(storage)
storage.GENERATION = GENERATION = 'synthetic-acquisition-generation'
Service = storage.Service


def wait_for(predicate: Callable[[], Any], message: str) -> Any:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError(message)


def control(service: Any, method: str, **fields: str) -> dict[str, Any]:
    code, response = service.call(method, generation=GENERATION, **fields)
    assert code == 200, (code, response)
    value = response['result']
    assert set(value) == {'generation', 'acquisition_id', 'state', 'settled'}, value
    assert value['generation'] == GENERATION
    assert len(value['acquisition_id']) == 64 and set(value['acquisition_id']) <= set('0123456789abcdef')
    assert type(value['settled']) is bool
    assert value['state'] in {'prepared', 'resolving', 'downloading', 'cancelling', 'cancelled', 'completed', 'failed'}
    assert not value['settled'] or value['state'] in {'cancelled', 'completed', 'failed'}
    return value


def state(service: Any, owner: dict[str, Any]) -> dict[str, Any]:
    return control(service, 'tgdGetFileAcquisition', acquisition_id=owner['acquisition_id'])


def cancel(service: Any, owner: dict[str, Any]) -> dict[str, Any]:
    return control(service, 'tgdCancelFileAcquisition', acquisition_id=owner['acquisition_id'])


def start(service: Any, owner: dict[str, Any], alias: str) -> tuple[int, dict[str, Any]]:
    return service.call('getFile', file_id=alias, tgd_generation=GENERATION,
                        tgd_acquisition_id=owner['acquisition_id'])


def descriptor_count(path: str) -> int:
    count = 0
    for entry in Path('/proc').glob('[0-9]*/fd/*'):
        try:
            if os.readlink(entry) == path:
                count += 1
        except (OSError, PermissionError):
            pass
    return count


def main() -> None:
    storage.transport.assert_network_none()
    assert os.getuid() != 0, 'Run fixture under the isolated nonroot UID'
    latch = Path('/tmp/tgd-download-stop.hold')
    entered = Path('/tmp/tgd-download-stop.entered')
    path_marker = Path('/tmp/tgd-download-stop.path')
    parts_marker = Path('/tmp/tgd-download-stop.parts')
    resolve_hold = Path('/tmp/tgd-acquisition-resolve.hold')
    resolve_entered = Path('/tmp/tgd-acquisition-resolve.entered')
    start_hold = Path('/tmp/tgd-acquisition-start.hold')
    start_entered = Path('/tmp/tgd-acquisition-start.entered')
    complete = Path('/tmp/tgd-download-stop.complete')
    scan_hold = Path('/tmp/tgd-cache-scan.hold')
    scan_entered = Path('/tmp/tgd-cache-scan.entered')
    race_destination = Path('/tmp/tgd-cache-race.destination')
    race_entered = Path('/tmp/tgd-cache-race.entered')
    for marker in (latch, entered, path_marker, parts_marker, resolve_hold, resolve_entered, start_hold, start_entered,
                   complete, scan_hold, scan_entered, race_destination, race_entered):
        marker.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='tgd-acquisition-') as temporary:
        root = Path(temporary)
        production_root = root / 'production'
        production_root.mkdir()
        production = Service(production_root, '/usr/local/bin/telegram-bot-api')
        try:
            for method, fields in (
                ('tgdPrepareFileAcquisition', {'generation': GENERATION, 'file_id': 'unused'}),
                ('tgdCancelFileAcquisition', {'generation': GENERATION, 'acquisition_id': 'a' * 64}),
                ('tgdGetFileAcquisition', {'generation': GENERATION, 'acquisition_id': 'a' * 64}),
                ('tgdCancelFileAcquisition', {}),
            ):
                assert production.call(method, **fields)[0] == 404
            assert not list(production_root.glob('123456*')), 'Private route created a Client'
        finally:
            production.close()

        fixture_root = root / 'fixture'
        fixture_root.mkdir()
        service = Service(fixture_root, '/verification/acquisition-fixture')
        try:
            service.call('tgdFixtureInit')
            wait_for(lambda: service.call('tgdGetCacheStorage', generation=GENERATION, refresh='0')[0] == 200,
                     'Offline existing Client did not initialize')
            assert service.call('tgdPrepareFileAcquisition', generation='stale', file_id='unused')[0] == 409
            assert service.call('tgdPrepareFileAcquisition', generation=GENERATION, file_id='')[0] == 400
            assert service.call('tgdGetFileAcquisition', generation=GENERATION, acquisition_id='a' * 64)[0] == 404
            assert service.call('tgdCancelFileAcquisition', generation=GENERATION, acquisition_id='A' * 64)[0] == 400
            assert service.call('getFile', file_id='unused', tgd_generation=GENERATION, tgd_acquisition_id='')[0] == 400

            # Pinned scanner must ignore only ENOENT and keep counting siblings
            # on filesystems whose directory entries don't provide d_type.
            bot = next(fixture_root.glob('123456*'))
            temporary_media, videos = bot / 'temp', bot / 'videos'
            temporary_media.mkdir(exist_ok=True)
            videos.mkdir(exist_ok=True)
            vanished = temporary_media / 'vanish-during-scan'
            sentinel = temporary_media / 'remaining-sibling'
            destination = videos / 'renamed-during-scan.mp4'
            vanished.write_bytes(b'v' * 8192)
            sentinel.write_bytes(b's' * 4096)
            expected = vanished.stat().st_blocks * 512 + sentinel.stat().st_blocks * 512
            race_destination.write_text(str(destination))
            assert service.snapshot('1')['scan_state'] == 'running'
            moved = service.wait(lambda value: value['scan_state'] == 'ready')
            assert race_entered.exists() and destination.exists() and not vanished.exists()
            assert moved['usage'] == {'used_bytes': expected, 'file_count': 2}, moved
            destination.unlink()
            sentinel.unlink()
            race_destination.unlink()

            prepared = control(service, 'tgdPrepareFileAcquisition', file_id='unused')
            assert prepared['state'] == 'prepared' and not prepared['settled']
            assert not path_marker.exists(), 'Prepare started a downloader'
            stopped = cancel(service, prepared)
            assert stopped['state'] == 'cancelled' and stopped['settled']
            assert cancel(service, prepared) == stopped, 'Duplicate cancel changed terminal owner'
            assert start(service, prepared, 'unused')[0] == 400, 'Late getFile revived cancelled token'
            assert not path_marker.exists()

            code, response = service.call('tgdFixtureFile')
            assert code == 200, (code, response)
            alias = response['result']
            assert isinstance(alias, str) and alias
            code, response = service.call('tgdFixtureFile', variant='alias')
            assert code == 200, (code, response)
            alias_b = response['result']
            assert isinstance(alias_b, str) and alias_b != alias
            # Keep a genuine metadata callback in flight while cancellation wins.
            resolving = control(service, 'tgdPrepareFileAcquisition', file_id=alias)
            resolve_hold.touch()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                delayed = executor.submit(start, service, resolving, alias)
                wait_for(resolve_entered.exists, 'Resolver callback did not reach its test latch')
                assert state(service, resolving)['state'] == 'resolving'
                assert cancel(service, resolving)['settled']
                resolve_hold.unlink()
                assert service.call('tgdFixtureReleaseResolvers')[0] == 200
                assert delayed.result(timeout=3)[0] == 400
                assert not path_marker.exists(), 'Late resolver started a cancelled acquisition'
            first = control(service, 'tgdPrepareFileAcquisition', file_id=alias)
            second = control(service, 'tgdPrepareFileAcquisition', file_id=alias_b)
            assert first['acquisition_id'] != second['acquisition_id']
            latch.touch()
            start_hold.touch()
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                original = executor.submit(start, service, first, alias)
                wait_for(lambda: state(service, first)['state'] == 'downloading', 'First owner never started')
                wait_for(path_marker.exists, 'Real downloader never wrote a part')
                wait_for(start_entered.exists, 'Initial download callback did not reach its test latch')
                # A real open writer must remain measurable, and a scan must not
                # stop a second listener's getFile or local cancellation controls.
                assert service.snapshot('1')['scan_state'] == 'running'
                assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='c' * 32)[0] == 409
                assert service.call('tgdDeleteFileCache', generation=GENERATION, file_id=alias,
                                    expected_path=path_marker.read_text(), expected_size=str(8 * 1024 * 1024))[0] == 409
                shared = executor.submit(start, service, second, alias_b)
                wait_for(lambda: state(service, second)['state'] == 'downloading', 'Shared owner never joined')
                cancelled = cancel(service, first)
                assert cancelled['settled'] and cancelled['state'] == 'cancelled'
                assert original.result(timeout=3)[0] == 400
                assert state(service, second)['state'] == 'downloading', 'Cancel stopped a shared consumer'
                scanned = service.wait(lambda value: value['scan_state'] == 'ready')
                source = path_marker.read_text()
                assert scanned['usage'] == {'used_bytes': Path(source).stat().st_blocks * 512, 'file_count': 1}, scanned
                assert not entered.exists(), 'Shared cancel tore down surviving writer'
                # Real cache maintenance cannot cross any owned native reader.
                assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='d' * 32)[0] == 409
                cancelled = cancel(service, second)
                assert not cancelled['settled'] and cancelled['state'] == 'cancelling'
                assert not entered.exists(), 'Stop bypassed a pending initial download callback'
                start_hold.unlink()
                assert service.call('tgdFixtureReleaseStarts')[0] == 200
                wait_for(entered.exists, 'Actual FileDownloader did not enter held teardown')
                source = path_marker.read_text()
                assert descriptor_count(source) > 0, 'Fixture did not hold a genuine open writer'
                for _ in range(3):
                    assert state(service, second)['settled'] is False, 'Released slot before writer destruction'
                    time.sleep(0.03)
                late = control(service, 'tgdPrepareFileAcquisition', file_id=alias)
                assert start(service, late, alias)[0] == 400, 'New writer crossed physical-stop gate'
                assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='e' * 32)[0] == 409
                assert service.snapshot('1')['scan_state'] == 'running'
                assert not state(service, second)['settled'], 'Read scan released the physical stop gate'
                scanned = service.wait(lambda value: value['scan_state'] == 'ready')
                assert scanned['usage']['file_count'] == 1
                latch.unlink()
                terminal = wait_for(lambda: (s if (s := state(service, second))['settled'] else None),
                                    'Cancellation did not settle after actual destruction')
                assert terminal['state'] == 'cancelled'
                assert shared.result(timeout=3)[0] == 400
                assert descriptor_count(source) == 0, 'Stop ack preceded descriptor closure'
                retained = Path(source).read_bytes()
                time.sleep(0.1)
                assert Path(source).read_bytes() == retained, 'File changed after stop acknowledgment'
                assert parts_marker.read_text().splitlines() == ['0']

                # Same-child retry must use FileManager's real partial bitmap.
                entered.unlink()
                latch.touch()
                retry = control(service, 'tgdPrepareFileAcquisition', file_id=alias)
                resumed = executor.submit(start, service, retry, alias)
                wait_for(lambda: len(parts_marker.read_text().splitlines()) == 2, 'Retry did not reuse partial data')
                assert parts_marker.read_text().splitlines() == ['0', '1'], 'Retry refetched a completed part'
                assert service.snapshot('1')['scan_state'] == 'running'
                grown = service.wait(lambda value: value['scan_state'] == 'ready')
                assert grown['usage']['used_bytes'] > scanned['usage']['used_bytes'], 'Refresh omitted growing partials'
                cancel(service, retry)
                wait_for(entered.exists, 'Retry teardown missing')
                assert not state(service, retry)['settled']
                latch.unlink()
                wait_for(lambda: state(service, retry)['settled'], 'Retry cancellation did not settle')
                assert resumed.result(timeout=3)[0] == 400
                assert descriptor_count(path_marker.read_text()) == 0

                # An ordinary legacy listener must survive owned cancellation,
                # and its normal completion must retain the physical GC fence.
                entered.unlink()
                latch.touch()
                tracked = control(service, 'tgdPrepareFileAcquisition', file_id=alias)
                owned = executor.submit(start, service, tracked, alias)
                wait_for(lambda: len(parts_marker.read_text().splitlines()) >= 3, 'Third writer never started')
                legacy = executor.submit(service.call, 'getFile', file_id=alias)
                wait_for(lambda: service.call('tgdFixtureListeners')[1].get('result') == 2,
                         'Legacy listener did not join the shared controller')
                assert cancel(service, tracked)['settled']
                assert owned.result(timeout=3)[0] == 400
                assert not entered.exists(), 'Owned cancellation stopped legacy listener'
                scan_hold.touch()
                assert service.snapshot('1')['scan_state'] == 'running'
                wait_for(scan_entered.exists, 'Read-only scan did not reach the temp/final boundary')
                complete.touch()
                wait_for(entered.exists, 'Legacy download never completed through actual finish code')
                assert legacy.result(timeout=3)[0] == 200
                assert Path(path_marker.read_text()).stat().st_size == 8 * 1024 * 1024
                scan_hold.unlink()
                moved = service.wait(lambda value: value['scan_state'] == 'ready')
                assert moved['usage']['file_count'] == 1, 'Temp-to-final rename was omitted or counted twice'
                assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='f' * 32)[0] == 409
                assert service.snapshot('1')['scan_state'] == 'running'
                assert service.wait(lambda value: value['scan_state'] == 'ready')['usage']['file_count'] == 1
                assert service.call('tgdClearCacheStorage', generation=GENERATION, operation_id='f' * 32)[0] == 409
                latch.unlink()
                # The read-only route is deliberately allowed while stopping;
                # prove gate release using a genuine new owned getFile instead.
                wait_for(lambda: service.call('tgdFixtureAcquisitionBusy')[1].get('result') == 0,
                         'Legacy completion physical gate did not release')
                after = control(service, 'tgdPrepareFileAcquisition', file_id=alias)
                outcome = start(service, after, alias)
                assert outcome[0] == 200, outcome
                wait_for(lambda: state(service, after)['settled'], 'Legacy completion physical gate did not release')
                complete.unlink()
        finally:
            latch.unlink(missing_ok=True)
            resolve_hold.unlink(missing_ok=True)
            start_hold.unlink(missing_ok=True)
            complete.unlink(missing_ok=True)
            scan_hold.unlink(missing_ok=True)
            race_destination.unlink(missing_ok=True)
            service.close()
    print(json.dumps({'production_no_client': 'passed', 'prepare_and_late_start_fence': 'passed',
                      'late_resolver_fence': 'passed', 'pending_initial_callback_fence': 'passed',
                      'distinct_alias_shared_consumer': 'passed', 'legacy_listener_and_completion_gate': 'passed',
                      'actual_writer_teardown_barrier': 'passed',
                      'live_allocated_usage_and_nonblocking_refresh': 'passed',
                      'temp_to_final_rename_during_scan': 'passed',
                      'rename_enoent_keeps_siblings': 'passed',
                      'no_post_ack_writes': 'passed', 'partial_bitmap_reuse_same_child': 'passed',
                      'cache_gc_stop_gate': 'passed', 'outbound_connections': 0, 'real_telegram_tested': False}))


if __name__ == '__main__':
    main()
