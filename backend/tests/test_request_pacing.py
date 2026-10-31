"""Exercise admission contention without Telegram, real sleeps or real credentials."""
import asyncio
import threading
from unittest.mock import AsyncMock, Mock

import pytest

from app.bot.client import TelegramClient
from app.services.request_safety import RequestSafetyService, SafetyDeferred
from tests.test_request_safety import OTHER, TOKEN, guard


@pytest.mark.parametrize("chat,spacing", [(42, 1.1), (-42, 3.1)])
async def test_same_chat_pacing_waits_and_retains_minimum_spacing(client, monkeypatch, chat, spacing):
    safety, now = guard(client)
    safety.reserve(TOKEN, chat)

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr("app.services.request_safety.asyncio.sleep", sleep)
    await safety.before(TOKEN, chat)
    assert now[0] == pytest.approx(1000 + spacing)
    assert safety.reserve(TOKEN, chat) == pytest.approx(1000 + spacing * 2)


@pytest.mark.parametrize("mode", ["cloud", "local"])
async def test_confirmation_burst_admits_in_order_without_waiting_for_long_poll(client, monkeypatch, mode):
    safety, now = guard(client)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.mode, telegram.safety = mode, safety
    safety.diagnostics = client.app.state.runtime.diagnostics
    real_sleep = asyncio.sleep
    release, sent = asyncio.Event(), asyncio.Event()
    starts = []
    methods = ["getUpdates", "getFile", "getFile", "getFile", "sendMessage"]

    async def sleep(delay):
        now[0] += delay
        await real_sleep(0)

    async def operation(index):
        starts.append((index, now[0]))
        if len(starts) == len(methods):
            sent.set()
        if index == 0:
            await release.wait()  # A long poll must not hold admission.
        return index

    monkeypatch.setattr("app.services.request_safety.asyncio.sleep", sleep)
    tasks = [asyncio.create_task(telegram.request(lambda i=i: operation(i), method=method))
             for i, method in enumerate(methods)]
    try:
        await asyncio.wait_for(sent.wait(), 2)
        assert not tasks[0].done()
        assert starts == [(index, 1000 + index) for index in range(len(methods))]
        release.set()
        assert await asyncio.gather(*tasks) == list(range(len(methods)))
        assert not safety.admissions
        assert not any(event["event"] in {"request_deferred", "telegram_recovered"}
                       for event in safety.diagnostics.export()["events"])
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await telegram.close()


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_cancelled_waiter_does_not_send_or_strand_admission(client, monkeypatch, cancel_first):
    safety, now = guard(client)
    safety.reserve(TOKEN)
    entered, release = asyncio.Event(), asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(delay):
        entered.set()
        await release.wait()
        now[0] += delay

    monkeypatch.setattr("app.services.request_safety.asyncio.sleep", sleep)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    operations = [AsyncMock(), AsyncMock()]
    tasks = [asyncio.create_task(telegram.request(operations[0]))]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        tasks.append(asyncio.create_task(telegram.request(operations[1])))
        await real_sleep(0.01)
        cancelled = 0 if cancel_first else 1
        tasks[cancelled].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[cancelled]
        release.set()
        await asyncio.wait_for(tasks[1 - cancelled], 2)
        operations[cancelled].assert_not_awaited()
        operations[1 - cancelled].assert_awaited_once()
        assert not safety.admissions
        # Exactly one live caller got a slot; cancellation did not reserve one.
        assert safety.reserve(TOKEN) == now[0] + 1
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await telegram.close()


@pytest.mark.parametrize("reason", ["cooldown", "auth_paused", "backoff"])
async def test_protection_arriving_during_pacing_is_rechecked_before_send(client, monkeypatch, reason):
    safety, now = guard(client)
    safety.reserve(TOKEN)

    async def sleep(delay):
        safety.observe(TOKEN, None, retry_after=60 if reason == "cooldown" else None,
                       unauthorized=reason == "auth_paused", transient=reason == "backoff")
        now[0] += delay

    monkeypatch.setattr("app.services.request_safety.asyncio.sleep", sleep)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    operation = AsyncMock()
    try:
        with pytest.raises(SafetyDeferred) as error:
            await telegram.request(operation)
        assert error.value.reason == reason and not error.value.is_pacing
        operation.assert_not_awaited()
        assert not safety.admissions
    finally:
        await telegram.close()


async def test_admission_timeout_is_bounded_and_does_not_poison_later_requests(client, monkeypatch):
    safety, _ = guard(client)
    monkeypatch.setattr("app.services.request_safety.PACING_WAIT_SECONDS", 0.03)
    reserve = safety._reserve
    # Keep denying a very short slot, exercising the whole queue's wall-clock bound.
    monkeypatch.setattr(safety, "_reserve", Mock(return_value=(1000.001, False)))
    results = await asyncio.wait_for(asyncio.gather(
        *(safety.before(TOKEN) for _ in range(3)), return_exceptions=True), 2)
    assert all(isinstance(error, SafetyDeferred) and error.is_pacing for error in results)
    assert not safety.admissions
    monkeypatch.setattr(safety, "_reserve", reserve)
    await safety.before(TOKEN)


async def test_storage_timeout_is_not_misclassified_as_normal_pacing(client, monkeypatch):
    safety = RequestSafetyService(client.app.state.db)
    monkeypatch.setattr(safety, "_reserve", Mock(side_effect=TimeoutError("private database detail")))
    with pytest.raises(SafetyDeferred) as error:
        await safety.before(TOKEN)
    assert error.value.reason == "protection_unavailable" and not error.value.is_pacing
    assert not safety.admissions


async def test_slow_storage_exceeding_admission_bound_is_not_reported_as_pacing(client, monkeypatch):
    safety, _ = guard(client)
    release, finished = threading.Event(), threading.Event()
    monkeypatch.setattr("app.services.request_safety.PACING_WAIT_SECONDS", 0.03)

    def slow_reserve(*args, **kwargs):
        try:
            assert release.wait(2)
            return 1000, True
        finally:
            finished.set()

    monkeypatch.setattr(safety, "_reserve", slow_reserve)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    operation = AsyncMock()
    try:
        with pytest.raises(SafetyDeferred) as error:
            await asyncio.wait_for(telegram.request(operation), 1)
        assert error.value.reason == "protection_unavailable" and not error.value.is_pacing
        operation.assert_not_awaited()
        assert not safety.admissions
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        await telegram.close()


async def test_waiting_token_does_not_block_another_token(client, monkeypatch):
    safety, now = guard(client)
    safety.reserve(TOKEN)
    entered, release = asyncio.Event(), asyncio.Event()

    async def sleep(delay):
        entered.set()
        await release.wait()
        now[0] += delay

    monkeypatch.setattr("app.services.request_safety.asyncio.sleep", sleep)
    first = asyncio.create_task(safety.before(TOKEN))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(safety.before(OTHER), 2)
        assert not first.done()
        release.set()
        await asyncio.wait_for(first, 2)
        assert not safety.admissions
    finally:
        release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
