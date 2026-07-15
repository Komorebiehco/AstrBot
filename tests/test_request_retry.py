import httpx
import pytest

import astrbot.core.provider.sources.request_retry as request_retry
from astrbot.core.provider.sources.request_retry import retry_provider_request


@pytest.mark.asyncio
async def test_retry_provider_request_uses_configured_max_retries(monkeypatch):
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("temporary connection failure")

    with pytest.raises(httpx.ConnectError):
        await retry_provider_request(
            "Test",
            request,
            max_attempts=2,
        )

    assert calls == 2


@pytest.mark.asyncio
async def test_retry_provider_request_retries_bare_nginx_403(monkeypatch):
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)

    calls = 0

    class PermissionDeniedError(Exception):
        status_code = 403

    async def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionDeniedError(
                "<html><head><title>403 Forbidden</title></head>"
                "<body><center><h1>403 Forbidden</h1></center>"
                "<hr><center>nginx</center></body></html>"
            )
        return "ok"

    result = await retry_provider_request(
        "Test",
        request,
        max_attempts=2,
    )

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_provider_request_does_not_retry_api_permission_403(monkeypatch):
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)

    calls = 0

    class PermissionDeniedError(Exception):
        status_code = 403

    async def request():
        nonlocal calls
        calls += 1
        raise PermissionDeniedError("API key does not have access to this model")

    with pytest.raises(PermissionDeniedError):
        await retry_provider_request(
            "Test",
            request,
            max_attempts=3,
        )

    assert calls == 1
