"""Isolated, no-network CPU attribution; never use with production storage."""
import asyncio
import cProfile
import json
import os
import pstats
import resource
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import event
from app.core.config import Config
from app.main import create_app
from app.schemas.contracts import SettingsPatch


async def main():
    mode = os.environ.get('BENCH_MODE', 'full')
    profile = os.environ.get('BENCH_PROFILE') == '1'
    duration = float(os.environ.get('BENCH_SECONDS', '30'))
    with tempfile.TemporaryDirectory(dir='/tmp') as directory:
        root = Path(directory)
        for name in ('data', 'downloads', 'cache', 'control'):
            (root/name).mkdir()
        config = Config(data_dir=root/'data', download_root=root/'downloads',
                        bot_api_root=root/'cache', local_api_control_dir=root/'control',
                        static_dir=root/'static', managed_local_api=True,
                        run_background=False, minimum_free_bytes=1024)
        app = create_app(config)
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            heartbeat_task = None
            if os.environ.get('BENCH_CONNECTED') == '1':
                settings = app.state.settings
                candidate = SettingsPatch(revision=1, bot_token='777777:synthetic-benchmark',
                                          allowed_user_ids=[42], bot_api_mode='local')
                settings.save(candidate, settings.candidate(candidate))
                class FakeClient:
                    mode = 'local'
                    async def updates(self, offset):
                        await asyncio.sleep(30)
                        return []
                    async def close(self):
                        pass
                runtime.client, runtime.bot_id = FakeClient(), 7
                runtime.client_signature = settings.client_signature(settings.read()[1])
                runtime.client_generation = 'same-generation'
                runtime.bot_status['state'] = 'online'
                runtime.local_api.update(applied_mode='local', phase='idle', applied_command_id='command',
                                         applied_credential_revision=2, command_id=None)
                applied = {'version': 1, 'state': 'running', 'generation': 'same-generation',
                           'command_id': 'command', 'credential_revision': 2,
                           'proxy_signature': settings.proxy_signature(settings.read()[1])}
                async def heartbeat():
                    while True:
                        path = config.local_api_control_dir/'applied.json'
                        path.write_text(json.dumps(applied | {'heartbeat_at': time.time()}))
                        path.chmod(0o600)
                        await asyncio.sleep(5)
                heartbeat_task = asyncio.create_task(heartbeat())
                await asyncio.sleep(0)
                (config.bot_api_root/'.tgd-api-generation').write_text('same-generation')
            await asyncio.to_thread(runtime.maintain_diagnostics)
            await runtime.local_api.reconcile(runtime)
            await asyncio.to_thread(runtime.recover_generation, True)
            counts = Counter()
            cpu = Counter()
            sql = Counter()
            lock = threading.Lock()
            original = asyncio.to_thread
            async def measured(function, /, *args, **kwargs):
                name = getattr(function, '__qualname__', repr(function))
                def wrapped():
                    started = time.thread_time()
                    try:
                        return function(*args, **kwargs)
                    finally:
                        used = time.thread_time()-started
                        with lock:
                            counts[name] += 1
                            cpu[name] += used
                return await original(wrapped)
            def statement(connection, cursor, text, parameters, context, many):
                sql[text.split()[0]] += 1
            event.listen(runtime.db.engine, 'before_cursor_execute', statement)
            asyncio.to_thread = measured
            all_loops = {'poll': runtime.poll_loop, 'tick': runtime.tick_loop,
                         'notify': runtime.notify_loop, 'diagnostic': runtime.diagnostic_loop}
            selected = [] if mode == 'none' else [name for name in all_loops if mode != 'no_'+name]
            tasks = [asyncio.create_task(all_loops[name]()) for name in selected]
            await asyncio.sleep(2)
            counts.clear(); cpu.clear(); sql.clear()
            before = resource.getrusage(resource.RUSAGE_SELF)
            wall = time.monotonic()
            await asyncio.sleep(duration)
            elapsed = time.monotonic()-wall
            after = resource.getrusage(resource.RUSAGE_SELF)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            asyncio.to_thread = original
            if heartbeat_task:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            result = {'mode': mode, 'measurement_profiled': False, 'wall': elapsed,
                      'connected': heartbeat_task is not None,
                      'user': after.ru_utime-before.ru_utime, 'system': after.ru_stime-before.ru_stime,
                      'threads_cpu': dict(cpu.most_common()), 'counts': dict(counts), 'sql': dict(sql)}
            if profile:
                profiler = cProfile.Profile()
                profiler.enable()
                for _ in range(200):
                    runtime.local_api.read()
                    app.state.settings.read()
                    runtime.tasks.freeze_due()
                profiler.disable()
                stats = pstats.Stats(profiler)
                result['sync_profile_scope'] = '200 calls each state.read/settings.read/freeze_due after timed interval; profiler wall clock, no loop concurrency'
                result['top_internal'] = [{'function': ':'.join(map(str, key)), 'calls': value[1],
                                           'internal': value[2], 'cumulative': value[3]}
                                          for key, value in sorted(stats.stats.items(), key=lambda item: item[1][2], reverse=True)[:30]]
                result['top_cumulative'] = [{'function': ':'.join(map(str, key)), 'calls': value[1],
                                             'internal': value[2], 'cumulative': value[3]}
                                            for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:30]]
            print(json.dumps(result), flush=True)


asyncio.run(main())
