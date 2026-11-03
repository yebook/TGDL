"""Real pinned TDLib partial metadata, continuation and cleanup; synthetic network-none only."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location('acquisition', Path(__file__).with_name('verify-native-acquisition.py'))
assert _spec and _spec.loader
acquisition = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acquisition)
storage = acquisition.storage
GENERATION = acquisition.GENERATION
BINARY = '/verification/acquisition-fixture'
PATH = Path('/tmp/tgd-download-stop.path')
PARTS = Path('/tmp/tgd-download-stop.parts')
HOLD = Path('/tmp/tgd-download-stop.hold')
ENTERED = Path('/tmp/tgd-download-stop.entered')
DELETE_HOLD = Path('/tmp/tgd-partial-delete.hold')
DELETE_ENTERED = Path('/tmp/tgd-partial-delete.entered')
ENOSPC = Path('/tmp/tgd-download-stop.enospc')
MARKERS = (PATH, PARTS, HOLD, ENTERED, DELETE_HOLD, DELETE_ENTERED, ENOSPC,
           Path('/tmp/tgd-download-stop.complete'))


def init(root: Path) -> Any:
    if (root / 'tmp').exists():
        (root / 'tmp').rmdir()
    service = storage.Service(root, BINARY)
    service.call('tgdFixtureInit')
    acquisition.wait_for(lambda: service.call('tgdGetCacheStorage', generation=GENERATION, refresh='0')[0] == 200,
                         'Offline fixture failed initialization')
    return service


def inspect(service: Any, alias: str, proof: dict[str, Any] | None = None, **extra: str) -> dict[str, Any]:
    fields = {'generation': GENERATION, 'file_id': alias, **extra}
    if proof:
        fields.update(expected_path=proof['path'], expected_size=str(proof['size']), expected_identity=proof['identity'])
    code, value = service.call('tgdGetFileCache', **fields)
    assert code == 200, (code, value)
    result = value['result']
    assert set(result) == {'generation', 'completeness', 'path', 'size', 'allocated_bytes', 'identity',
                           'resumable', 'reason', 'settled'}, result
    return result


def delete(service: Any, alias: str, proof: dict[str, Any], **extra: str) -> tuple[int, dict[str, Any]]:
    return service.call('tgdDeletePartialFileCache', generation=GENERATION, file_id=alias,
                        expected_path=proof['path'], expected_size=str(proof['size']),
                        expected_identity=proof['identity'], **extra)


def stopped_partial(service: Any, alias: str, executor: Any) -> dict[str, Any]:
    HOLD.touch()
    ENTERED.unlink(missing_ok=True)
    previous = len(PARTS.read_text().splitlines()) if PARTS.exists() else 0
    owner = acquisition.control(service, 'tgdPrepareFileAcquisition', file_id=alias)
    started = executor.submit(acquisition.start, service, owner, alias)
    acquisition.wait_for(lambda: PARTS.exists() and len(PARTS.read_text().splitlines()) > previous,
                         'Genuine downloader did not write a partial')
    active = acquisition.wait_for(
        lambda: (value if (value := inspect(service, alias, acquisition_id=owner['acquisition_id']))['path'] else None),
        'Active writer did not expose its exact private partial locator')
    assert active['settled'] is False and active['resumable'] is False
    assert active['completeness'] == 'partial' and active['reason'] == 'cache_busy'
    assert active['path'] == PATH.read_text() and active['allocated_bytes'] > 0
    acquisition.cancel(service, owner)
    acquisition.wait_for(ENTERED.exists, 'Real writer did not reach held teardown')
    assert not acquisition.state(service, owner)['settled']
    assert inspect(service, alias)['settled'] is False
    HOLD.unlink()
    acquisition.wait_for(lambda: acquisition.state(service, owner)['settled'], 'Native stop did not settle')
    stopped = started.result(timeout=3)
    assert stopped[0] == 400 and 'TGD_CACHE_NO_SPACE' not in stopped[1]['description'], stopped
    proof = inspect(service, alias, acquisition_id=owner['acquisition_id'])
    source = Path(PATH.read_text())
    assert proof['completeness'] == 'partial' and proof['settled'] and proof['resumable'], proof
    assert proof['path'] == str(source) and proof['size'] == source.stat().st_size
    assert proof['allocated_bytes'] == source.stat().st_blocks * 512
    assert len(proof['identity']) == 64 and proof['reason'] == 'resume_available'
    return proof


def main() -> None:
    global GENERATION
    storage.transport.assert_network_none()
    assert os.getuid() != 0
    for marker in MARKERS:
        marker.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='tgd-retained-native-') as temporary:
        root = Path(temporary)
        prod = root / 'production'
        prod.mkdir()
        service = storage.Service(prod, '/usr/local/bin/telegram-bot-api')
        try:
            for method in ('tgdGetFileCache', 'tgdDeletePartialFileCache'):
                assert service.call(method, generation=GENERATION, file_id='unused')[0] == 404
            assert not list(prod.glob('123456*')), 'Private method created a production Client'
        finally:
            service.close()
        fixture = root / 'fixture'
        fixture.mkdir()
        service = init(fixture)
        try:
            alias = service.call('tgdFixtureFile')[1]['result']
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                proof = stopped_partial(service, alias, executor)
                source = Path(proof['path'])
                sentinel = source.parent / 'unrelated-partial'
                sentinel.write_bytes(b'preserve unrelated')
                bad = dict(proof, identity='0' * 64)
                assert delete(service, alias, bad)[0] == 409 and source.exists()
                assert inspect(service, alias, bad)['completeness'] == 'unknown'
                assert delete(service, alias, dict(proof, path=str(sentinel)))[0] == 409
                assert sentinel.read_bytes() == b'preserve unrelated'
                assert service.call('tgdGetFileCache', generation='stale', file_id=alias)[0] == 409
                invalid = acquisition.control(service, 'tgdPrepareFileAcquisition', file_id=alias,
                                               expected_cache_identity='0' * 64)
                rejected = acquisition.start(service, invalid, alias)
                assert rejected[0] == 400 and 'TGD_CACHE_RESUME_UNAVAILABLE' in rejected[1]['description'], rejected
                acquisition.wait_for(lambda: acquisition.state(service, invalid)['settled'], 'Invalid resume retained owner')
                assert PARTS.read_text().splitlines() == ['0'], 'Invalid continuation started network delivery'

                # Atomic continuation verifies real current bitmap and identity;
                # cancellation leaves it reusable and never fetches part zero twice.
                owner = acquisition.control(service, 'tgdPrepareFileAcquisition', file_id=alias,
                                             expected_cache_identity=proof['identity'])
                HOLD.touch()
                ENTERED.unlink()
                resumed = executor.submit(acquisition.start, service, owner, alias)
                acquisition.wait_for(lambda: len(PARTS.read_text().splitlines()) == 2, 'Verified continuation never wrote')
                assert PARTS.read_text().splitlines() == ['0', '1']
                acquisition.cancel(service, owner)
                acquisition.wait_for(ENTERED.exists, 'Resumed actor did not reach teardown')
                HOLD.unlink()
                acquisition.wait_for(lambda: acquisition.state(service, owner)['settled'], 'Resumed owner did not settle')
                assert resumed.result(timeout=3)[0] == 400
                proof = inspect(service, alias)
                assert proof['resumable'] and proof['size'] > 0

                # Actual typed POSIX ENOSPC must become a fixed storage failure,
                # retaining valid completed parts instead of deleting the source.
                ENOSPC.touch()
                full = acquisition.control(service, 'tgdPrepareFileAcquisition', file_id=alias,
                                            expected_cache_identity=proof['identity'])
                failed = acquisition.start(service, full, alias)
                assert failed[0] == 400 and 'TGD_CACHE_NO_SPACE' in failed[1]['description'], failed
                ENOSPC.unlink()
                acquisition.wait_for(lambda: acquisition.state(service, full)['settled'], 'ENOSPC owner did not settle')
                proof = inspect(service, alias)
                assert proof['completeness'] == 'partial' and source.exists()
                # A later explicit attempt has no persisted/stale error marker.
                proof = stopped_partial(service, alias, executor)

                service.close()
                GENERATION = acquisition.GENERATION = storage.GENERATION = 'synthetic-retained-restarted'
                service = init(fixture)
                # File DB is disabled. No promise of cross-child continuation:
                # the persisted native identity still locates the exact orphan.
                assert inspect(service, alias)['completeness'] == 'absent'
                orphan = inspect(service, alias, proof)
                assert orphan['completeness'] == 'partial' and orphan['settled']
                assert not orphan['resumable'] and orphan['reason'] == 'bitmap_unavailable'
                unavailable = acquisition.control(service, 'tgdPrepareFileAcquisition', file_id=alias,
                                                   expected_cache_identity=proof['identity'])
                assert acquisition.start(service, unavailable, alias)[0] == 400
                acquisition.wait_for(lambda: acquisition.state(service, unavailable)['settled'], 'Restart rejection stuck')
                # Exact persisted proof also authorizes real TDLib-owned cleanup
                # after the file DB association vanished in a new child.
                assert delete(service, alias, proof)[0] == 200
                assert not source.exists() and sentinel.exists()
                assert service.call('tgdFixtureFile')[1]['result'] == alias
                proof = stopped_partial(service, alias, executor)
                source = Path(proof['path'])
                before = source.read_bytes()
                replacement = source.with_suffix('.new')
                replacement.write_bytes(before)
                source.replace(source.with_suffix('.original'))
                replacement.replace(source)
                assert delete(service, alias, proof)[0] == 409, 'Replaced inode accepted old native proof'
                source.unlink()
                source.with_suffix('.original').replace(source)
                # Rename changes ctime; refreshing proof must not trust it. Use a
                # second genuine partial for the independent lost-reply case.
                assert inspect(service, alias, proof)['completeness'] == 'unknown'
                proof = stopped_partial(service, alias, executor)
                source = Path(proof['path'])
                DELETE_HOLD.touch()
                DELETE_ENTERED.unlink(missing_ok=True)
                fields = {'generation': GENERATION, 'file_id': alias, 'expected_path': proof['path'],
                          'expected_size': str(proof['size']), 'expected_identity': proof['identity']}
                request = urllib.request.Request(f'http://127.0.0.1:{service.port}/bot{storage.TOKEN}/tgdDeletePartialFileCache',
                                                 data=urllib.parse.urlencode(fields).encode())
                try:
                    storage.OPENER.open(request, timeout=0.15)
                    raise AssertionError('Expected lost HTTP deletion response')
                except TimeoutError:
                    pass
                acquisition.wait_for(DELETE_ENTERED.exists, 'TDLib unlink did not hold native fence')
                assert service.call('tgdGetFileCache', generation=GENERATION, file_id=alias)[0] == 409
                assert service.call('tgdPrepareFileAcquisition', generation=GENERATION, file_id=alias)[0] == 409
                assert source.exists()
                DELETE_HOLD.unlink()
                acquisition.wait_for(lambda: not source.exists(), 'Late deletion did not finish')
                assert inspect(service, alias, proof)['completeness'] == 'absent'
                assert sentinel.read_bytes() == b'preserve unrelated'
        finally:
            for marker in MARKERS:
                marker.unlink(missing_ok=True)
            service.close()
    print(json.dumps({'native_retained_cache': 'passed', 'production_existing_client_only': True,
                      'real_partial_metadata_and_allocation': True, 'same_child_bitmap_resume': True,
                      'restart_orphan_inspection': True, 'exact_partial_cleanup': True,
                      'restart_resume_rejected_without_transfer': True, 'enospc_classification': True,
                      'replacement_rejected': True, 'lost_reply_native_fence': True,
                      'unrelated_files_preserved': True, 'outbound_connections': 0}, sort_keys=True))


if __name__ == '__main__':
    main()
