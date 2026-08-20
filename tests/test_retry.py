"""Tests for opt-in transient-error retry in the with_account choke point."""

import pytest

from telegram_mcp import runtime


def _make_flaky_tool(fail_times: int, exc_type: type):
    calls = {"n": 0}

    @runtime.with_account(readonly=False)
    async def flaky(chat_id, account=None):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc_type("flaky")
        return "ok"

    return flaky, calls


@pytest.mark.asyncio
async def test_retry_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_RETRY_TRANSIENT", raising=False)

    flaky, calls = _make_flaky_tool(fail_times=99, exc_type=ConnectionResetError)

    with pytest.raises(ConnectionResetError):
        await flaky(chat_id=1)

    assert calls["n"] == 1  # no retry happened


@pytest.mark.asyncio
async def test_retry_transient_then_succeeds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "2")
    monkeypatch.setattr(runtime, "_backoff_delay", lambda attempt: 0.0)

    flaky, calls = _make_flaky_tool(fail_times=1, exc_type=ConnectionResetError)

    assert await flaky(chat_id=1) == "ok"
    assert calls["n"] == 2  # initial attempt + 1 retry


@pytest.mark.asyncio
async def test_retry_gives_up_after_max(monkeypatch):
    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "2")
    monkeypatch.setattr(runtime, "_backoff_delay", lambda attempt: 0.0)

    flaky, calls = _make_flaky_tool(fail_times=99, exc_type=TimeoutError)

    with pytest.raises(TimeoutError):
        await flaky(chat_id=1)

    assert calls["n"] == 3  # initial attempt + 2 retries


@pytest.mark.asyncio
async def test_non_transient_errors_are_not_retried(monkeypatch):
    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "3")
    monkeypatch.setattr(runtime, "_backoff_delay", lambda attempt: 0.0)

    flaky, calls = _make_flaky_tool(fail_times=99, exc_type=ValueError)

    with pytest.raises(ValueError):
        await flaky(chat_id=1)

    assert calls["n"] == 1


def test_get_transient_retry_count_validation(monkeypatch):
    monkeypatch.delenv("TELEGRAM_RETRY_TRANSIENT", raising=False)
    assert runtime._get_transient_retry_count() == 0

    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "3")
    assert runtime._get_transient_retry_count() == 3

    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "99")
    assert runtime._get_transient_retry_count() == 5  # capped

    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "-2")
    assert runtime._get_transient_retry_count() == 0

    monkeypatch.setenv("TELEGRAM_RETRY_TRANSIENT", "not-a-number")
    assert runtime._get_transient_retry_count() == 0


def test_backoff_delay_exponential_with_cap():
    delays = [runtime._backoff_delay(i) for i in (1, 2, 3, 4, 5, 6)]
    assert 1.0 <= delays[0] < 1.5
    assert 2.0 <= delays[1] < 2.5
    assert 4.0 <= delays[2] < 4.5
    assert 8.0 <= delays[3] < 8.5
    # Attempts 5 and 6 would be 16s/32s — capped to 10s.
    assert 10.0 <= delays[4] < 10.5
    assert 10.0 <= delays[5] < 10.5
