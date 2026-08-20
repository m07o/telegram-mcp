"""Tests for transient error retry logic."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon import errors as telethon_errors
from telethon.tl import TLRequest

from telegram_mcp import runtime

# Helper to create mock requests for Telethon errors
def _mock_request():
    req = MagicMock(spec=TLRequest)
    req.__class__.__name__ = "MockRequest"
    return req


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    runtime._TOOL_TIER_MAP.clear()
    yield
    runtime._TOOL_TIER_MAP.clear()


class TestIsTransientError:
    """Tests for _is_transient_error function."""

    def test_connection_error_is_transient(self):
        assert runtime._is_transient_error(ConnectionError("connection reset")) is True

    def test_os_error_is_transient(self):
        assert runtime._is_transient_error(OSError("network unreachable")) is True

    def test_asyncio_timeout_is_transient(self):
        assert runtime._is_transient_error(asyncio.TimeoutError()) is True

    def test_server_error_is_transient(self):
        error = telethon_errors.ServerError(_mock_request(), "Internal server error")
        assert runtime._is_transient_error(error) is True

    def test_timed_out_error_is_transient(self):
        error = telethon_errors.TimedOutError(_mock_request(), "Request timed out")
        assert runtime._is_transient_error(error) is True

    def test_timeout_error_is_transient(self):
        error = telethon_errors.TimeoutError("Timeout")
        assert runtime._is_transient_error(error) is True

    def test_network_migrate_error_is_transient(self):
        error = telethon_errors.NetworkMigrateError("Migrate to DC")
        assert runtime._is_transient_error(error) is True

    def test_flood_wait_error_is_transient(self):
        error = telethon_errors.FloodWaitError(_mock_request(), capture=10)
        assert runtime._is_transient_error(error) is True

    def test_auth_key_error_not_transient(self):
        error = telethon_errors.AuthKeyError(_mock_request(), "Auth key invalid")
        assert runtime._is_transient_error(error) is False

    def test_unauthorized_error_not_transient(self):
        error = telethon_errors.UnauthorizedError(_mock_request(), "Unauthorized")
        assert runtime._is_transient_error(error) is False

    def test_session_expired_not_transient(self):
        error = telethon_errors.SessionExpiredError(_mock_request())
        assert runtime._is_transient_error(error) is False

    def test_bad_request_error_not_transient(self):
        error = telethon_errors.BadRequestError(_mock_request(), "Bad request")
        assert runtime._is_transient_error(error) is False

    def test_peer_id_invalid_not_transient(self):
        error = telethon_errors.PeerIdInvalidError(_mock_request())
        assert runtime._is_transient_error(error) is False

    def test_channel_invalid_not_transient(self):
        error = telethon_errors.ChannelInvalidError(_mock_request())
        assert runtime._is_transient_error(error) is False

    def test_user_deactivated_not_transient(self):
        error = telethon_errors.UserDeactivatedError(_mock_request())
        assert runtime._is_transient_error(error) is False

    def test_user_blocked_not_transient(self):
        error = telethon_errors.UserBlockedError(_mock_request())
        assert runtime._is_transient_error(error) is False


class TestRetryWithBackoff:
    """Tests for _retry_with_backoff function."""

    @pytest.mark.asyncio
    async def test_transient_error_retried_then_succeeds(self):
        """Test that transient error is retried and eventually succeeds."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection reset")
            return "success"

        result = await runtime._retry_with_backoff(flaky_func, max_retries=3)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_transient_error_not_retried(self):
        """Test that non-transient error is not retried."""
        call_count = 0

        async def fail_func():
            nonlocal call_count
            call_count += 1
            raise telethon_errors.BadRequestError(_mock_request(), "Bad request")

        with pytest.raises(telethon_errors.BadRequestError):
            await runtime._retry_with_backoff(fail_func, max_retries=3)

        assert call_count == 1  # Should not retry

    @pytest.mark.asyncio
    async def test_max_retries_respected(self):
        """Test that max retries limit is respected."""
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection reset")

        with pytest.raises(ConnectionError):
            await runtime._retry_with_backoff(always_fail, max_retries=2)

        assert call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_env_override_works(self):
        """Test that TELEGRAM_MAX_RETRIES env var works."""
        with patch.dict(os.environ, {"TELEGRAM_MAX_RETRIES": "5"}):
            call_count = 0

            async def always_fail():
                nonlocal call_count
                call_count += 1
                raise ConnectionError("connection reset")

            with pytest.raises(ConnectionError):
                await runtime._retry_with_backoff(always_fail)

            # Default max_retries=2, but env says 5, so should be 5+1=6 calls
            # Wait, the function reads from env each time
            assert call_count == 6  # Initial + 5 retries

    @pytest.mark.asyncio
    async def test_attempt_count_attached_to_error(self):
        """Test that attempt count is attached to error after max retries."""
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection reset")

        with pytest.raises(ConnectionError) as excinfo:
            await runtime._retry_with_backoff(always_fail, max_retries=2)

        assert excinfo.value._retry_attempts == 3


class TestWithAccountRetryIntegration:
    """Tests for retry integration in with_account wrapper."""

    @pytest.mark.asyncio
    async def test_retry_on_transient_error_in_single_mode(self):
        """Test retry works in single-account mode."""
        # Setup a fake client
        fake_client = object()
        runtime.clients = {"default": fake_client}

        call_count = 0

        @runtime.with_account(readonly=False)
        async def test_tool(account=None):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("connection reset")
            return "success"

        result = await test_tool()
        assert result == "success"
        assert call_count == 2


class TestGetMaxRetries:
    """Tests for _get_max_retries function."""

    def test_default_value(self):
        with patch.dict(os.environ, {}, clear=True):
            assert runtime._get_max_retries() == 2

    def test_env_override(self):
        with patch.dict(os.environ, {"TELEGRAM_MAX_RETRIES": "5"}):
            assert runtime._get_max_retries() == 5

    def test_invalid_env_falls_back(self):
        with patch.dict(os.environ, {"TELEGRAM_MAX_RETRIES": "invalid"}):
            assert runtime._get_max_retries() == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])