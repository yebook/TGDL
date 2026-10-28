# Idle CPU measurement evidence

Recorded 2026-10-27. Scope: reduce idle CPU work, without changing memory behavior or Telegram request pacing.

## Environment and boundary

Windows Python 3.12 from the existing backend virtual environment. A temporary SQLite database, initialized FastAPI runtime, synthetic Bot identity/token, empty task/notification/cache-cleanup queues, a stable generation marker and (local mode) a fresh synthetic supervisor status file are used. No Telegram calls, real secrets, user files, Docker startup, native build or production data are involved. Only `Runtime.tick_loop` runs, for 4.2 seconds, with its existing 0.5-second cadence. SQLAlchemy `before_cursor_execute` counts SQL and a wrapper counts writes to `api-generation.json`; setup/shutdown are outside the counters. Real local Bot API CPU, long polling, browser polling, and native TDLib internals are outside this isolated sample.

Baseline methods are loaded from commit `2409af7` into the otherwise identical instance. Baseline substitutes Runtime.tick_loop/counts/cleanup_cache/recover_generation and TaskService.freeze_due. The updated run uses the working tree methods. The test harness creates no real TelegramClient.

## Results

| Mode / implementation | Ticks observed | SELECT | BEGIN IMMEDIATE | Generation writes | Process CPU seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cloud baseline | 8 | 112 | 16 | 8 | 0.1250 |
| Cloud updated | 9 | 36 | 0 | 0 | 0.0781 |
| Managed local baseline | 8 | 152 | 16 | 8 | 0.0781 |
| Managed local updated | 9 | 54 | 0 | 0 | 0.0625 |

Short wall-clock runs produce slightly different tick counts. Normalize operation counts: cloud SELECTs 14 -> 4 per tick; local SELECTs 19 -> 6 per tick. Idle write reservations 2 -> 0 and unchanged-generation writes 1 -> 0 per tick. Process CPU seconds have coarse Windows resolution and thread/scheduling noise; these are supporting observations, **not a predicted container CPU percentage or reduction**. The later aggregate query adds an indexed WHERE filtering active/pending states without increasing query count. No memory-reduction claim is made.

Supervisor deterministic-clock regressions separately verify stable heartbeat publication once every five seconds (rather than every second), immediate state/generation acknowledgement, and retry immediately after a failed write. The application retains its original 15-second stale-status rejection. Group freezing, new dispatch, confirmation expiration and active-task scheduling retain the 0.5-second cadence; no idle backoff or persistent settings cache was introduced.

## Reproduction

Save the script below as `.runtime/profile_idle.py` relative to the repository root. Run using `backend/.venv/Scripts/python.exe .runtime/profile_idle.py [--local] [--baseline]` on Windows (use the equivalent virtual-environment Python path on Linux). The baseline commit is explicit; no checkout or production configuration modification is needed. The temporary directories are automatically removed. The file below is a measurement aid, not a deployed entrypoint or general-purpose benchmark.

```python
import asyncio
import json
import sys
import tempfile
import time
import subprocess
import types
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.core.config import Config
from app.main import create_app
from app.schemas.contracts import SettingsPatch

async def measure(runtime):
    sql = Counter()
    writes = Counter()
    def statement(connection, cursor, text, parameters, context, many):
        sql[text.split()[0]] += 1
    original = Path.write_text
    def write(path, *args, **kwargs):
        if path.name == 'api-generation.json':
            writes['generation'] += 1
        return original(path, *args, **kwargs)
    event.listen(runtime.db.engine, 'before_cursor_execute', statement)
    start = time.process_time()
    with patch.object(Path, 'write_text', write):
        task = asyncio.create_task(runtime.tick_loop())
        await asyncio.sleep(4.2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    print(json.dumps({'cpu_seconds': round(time.process_time()-start, 4), 'sql': dict(sql), 'writes': dict(writes)}))

with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
    root = Path(directory)
    for name in ('data', 'downloads', 'cache'):
        (root / name).mkdir()
    local = '--local' in sys.argv
    cfg = Config(data_dir=root/'data', download_root=root/'downloads', bot_api_root=root/'cache',
                 static_dir=root/'static', run_background=False, minimum_free_bytes=1024,
                 managed_local_api=local, local_api_control_dir=root/'control')
    with TestClient(create_app(cfg)) as client:
        settings = client.app.state.settings
        p = SettingsPatch(revision=1, bot_token='777777:synthetic-benchmark', bot_api_mode='local' if local else 'cloud', allowed_user_ids=[42])
        settings.save(p, settings.candidate(p))
        runtime = client.app.state.runtime
        runtime.client = Mock(mode='local' if local else 'cloud')
        runtime.client_signature = settings.client_signature(settings.read()[1])
        runtime.bot_id = 7
        runtime.bot_status['state'] = 'online'
        if local:
            cfg.local_api_control_dir.mkdir()
            cfg.local_api_control_dir.joinpath('applied.json').write_text(json.dumps({
                'version': 1, 'heartbeat_at': time.time(), 'state': 'running', 'generation': 'same-generation',
                'command_id': 'command', 'credential_revision': 2,
                'proxy_signature': settings.proxy_signature(settings.read()[1]),
            }))
            runtime.local_api.update(applied_mode='local', phase='idle', applied_command_id='command',
                                     applied_credential_revision=2, command_id=None)
            runtime.client_generation = 'same-generation'
        (cfg.bot_api_root / '.tgd-api-generation').write_text('same-generation')
        runtime.recover_generation(initial=True)
        if '--baseline' in sys.argv:
            for path, owner, methods in [
                ('backend/app/workers/runtime.py', runtime, ['tick_loop', 'counts', 'cleanup_cache', 'recover_generation']),
                ('backend/app/services/tasks.py', runtime.tasks, ['freeze_due']),
            ]:
                source = subprocess.check_output(['git', 'show', f'2409af7:{path}'], text=True, encoding='utf-8')
                namespace = {'__name__': 'offline_profile'}
                exec(compile(source, path, 'exec'), namespace)
                cls = namespace[type(owner).__name__]
                for name in methods:
                    setattr(owner, name, types.MethodType(getattr(cls, name), owner))
        asyncio.run(measure(runtime))

```

## Additional notification-loop coverage

The table above isolates tick_loop, not notify_loop. Final review also added a read-only Runtime.notification_due probe before send-only checks. Empty/not-yet-due outboxes avoid settings/proxy/generation reads and write reservations. No Bot filter or negative cache is used; next_notification retains authoritative transaction and obsolete/request-protection logic. Targeted regression proves a future message is sent once due at the next original 0.5-second iteration, and old-Bot messages still become obsolete. Final targeted group94passed, Ruff/mypy36passed. No additional container CPU claim follows from this isolated query-count table.
