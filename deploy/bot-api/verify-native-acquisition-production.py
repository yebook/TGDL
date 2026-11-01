"""No-network production-binary boundary check; never use real credentials/cache."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('storage', Path(__file__).with_name('verify-native-cache-storage.py'))
assert spec and spec.loader
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


def main() -> None:
    storage.transport.assert_network_none()
    assert os.getuid() != 0
    requests = 0
    with tempfile.TemporaryDirectory(prefix='tgd-production-control-') as temporary:
        root = Path(temporary)
        service = storage.Service(root, '/usr/local/bin/telegram-bot-api')
        try:
            for method in ('tgdPrepareFileAcquisition', 'tgdGetFileAcquisition', 'tgdCancelFileAcquisition'):
                for fields in ({}, {'generation': storage.GENERATION},
                               {'generation': storage.GENERATION, 'file_id': 'unused'},
                               {'generation': storage.GENERATION, 'acquisition_id': 'a' * 64},
                               {'generation': 'x' * 129, 'acquisition_id': 'malformed'},
                               {'generation': storage.GENERATION, 'file_id': 'x' * 4097}):
                    assert service.call(method, **fields)[0] == 404
                    requests += 1
            assert not list(root.glob('123456*')), 'Private control created a Bot Client'
        finally:
            service.close()  # Checks strace for any non-loopback connect attempt.
    print(json.dumps({'production_requests': requests, 'missing_client_and_malformed': 'passed',
                      'created_bot_clients': 0, 'outbound_connections': 0, 'real_telegram_tested': False}))


if __name__ == '__main__':
    main()
