import asyncio

import httpx
import pytest

from scripts.test_booking_race import send_hold, validate_target


def test_race_runner_allows_local_http_without_remote_opt_in():
    validate_target("http://localhost:8000", allow_remote=False, use_phone=True)


def test_race_runner_requires_opt_in_and_https_for_remote_targets():
    with pytest.raises(RuntimeError, match="non-local host"):
        validate_target("https://test.example", allow_remote=False, use_phone=False)
    with pytest.raises(RuntimeError, match="HTTPS"):
        validate_target("http://test.example", allow_remote=True, use_phone=False)
    with pytest.raises(RuntimeError, match="localhost"):
        validate_target("https://test.example", allow_remote=True, use_phone=True)

    validate_target("https://test.example", allow_remote=True, use_phone=False)


@pytest.mark.asyncio
async def test_race_runner_sends_simultaneous_requests_and_counts_one_winner():
    request_count = 100
    claimed = False
    claim_lock = asyncio.Lock()
    idempotency_keys = set()

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal claimed
        idempotency_keys.add(request.headers["Idempotency-Key"])
        async with claim_lock:
            if not claimed:
                claimed = True
                return httpx.Response(
                    201,
                    json={"public_id": "winner", "state": "CREATED"},
                )
        return httpx.Response(409, json={"detail": "NO_LOCKER_AVAILABLE"})

    async with httpx.AsyncClient(
        base_url="http://localhost:8000",
        transport=httpx.MockTransport(respond),
    ) as client:
        barrier = asyncio.Barrier(request_count)
        results = await asyncio.gather(
            *[
                send_hold(client, barrier, index, "CH101", 1)
                for index in range(request_count)
            ]
        )

    responses = [response for _, response in results]
    assert all(isinstance(response, httpx.Response) for response in responses)
    assert sum(response.status_code == 201 for response in responses) == 1
    assert sum(response.status_code == 409 for response in responses) == request_count - 1
    assert len(idempotency_keys) == request_count
