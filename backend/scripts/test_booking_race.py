"""Send simultaneous hold requests for one locker and verify only one wins."""

import argparse
import asyncio
import ipaddress
import os
import sys
import time
import uuid
from urllib.parse import urlparse

import httpx


def is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_target(base_url: str, allow_remote: bool, use_phone: bool) -> None:
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise RuntimeError("The API URL must be an absolute http:// or https:// URL.")
    loopback = is_loopback_url(base_url)
    if not loopback and not allow_remote:
        raise RuntimeError(
            "Refusing to send booking requests to a non-local host. "
            "Use --allow-remote only for a test environment you control."
        )
    if not loopback and parsed_url.scheme != "https":
        raise RuntimeError("Remote API targets must use HTTPS to protect bearer tokens.")
    if use_phone and not loopback:
        raise RuntimeError("--phone is allowed only with a localhost API.")


async def local_dev_token(client: httpx.AsyncClient, base_url: str, phone: str) -> str:
    otp_response = await client.post(
        f"{base_url}/v1/auth/otp/request",
        json={"phone": phone},
    )
    otp_response.raise_for_status()
    otp_body = otp_response.json()
    debug_code = otp_body.get("debug_code") if isinstance(otp_body, dict) else None
    if not debug_code:
        raise RuntimeError(
            "The API did not return a development OTP. Use LOCKER_TEST_TOKEN instead."
        )

    verify_response = await client.post(
        f"{base_url}/v1/auth/otp/verify",
        json={"phone": phone, "otp": debug_code},
    )
    verify_response.raise_for_status()
    verify_body = verify_response.json()
    token = verify_body.get("token") if isinstance(verify_body, dict) else None
    if not token:
        raise RuntimeError("The OTP verification response did not contain a token.")
    return token


async def send_hold(
    client: httpx.AsyncClient,
    barrier: asyncio.Barrier,
    index: int,
    locker_code: str,
    duration_hours: int,
) -> tuple[int, httpx.Response | Exception]:
    try:
        await barrier.wait()
        response = await client.post(
            "/v1/bookings",
            headers={"Idempotency-Key": f"race-{uuid.uuid4().hex}-{index}"},
            json={"locker_code": locker_code, "duration_hours": duration_hours},
        )
        return index, response
    except (httpx.HTTPError, asyncio.TimeoutError) as error:
        return index, error


async def cancel_winners(client: httpx.AsyncClient, bookings: list[dict]) -> list[str]:
    failures = []
    for booking in bookings:
        public_id = booking.get("public_id")
        if not public_id:
            failures.append("A successful booking response had no public_id.")
            continue
        if booking.get("state") != "CREATED":
            failures.append(
                f"Booking {public_id} is {booking.get('state')}; it was not cancelled."
            )
            continue
        try:
            response = await client.post(f"/v1/bookings/{public_id}/cancel")
        except httpx.HTTPError as error:
            failures.append(f"Could not cancel booking {public_id}: {error}")
            continue
        if response.status_code != 200:
            failures.append(
                f"Could not cancel booking {public_id}: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )
    return failures


async def run(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    validate_target(base_url, args.allow_remote, bool(args.phone))

    timeout = httpx.Timeout(30.0)
    limits = httpx.Limits(
        max_connections=args.requests + 4,
        max_keepalive_connections=args.requests,
    )
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        limits=limits,
    ) as client:
        token = os.environ.get("LOCKER_TEST_TOKEN")
        if args.phone:
            token = await local_dev_token(client, base_url, args.phone)
        if not token:
            raise RuntimeError(
                "Set LOCKER_TEST_TOKEN to a valid bearer token, or use --phone "
                "with the local development API."
            )
        client.headers["Authorization"] = f"Bearer {token}"

        for round_number in range(1, args.rounds + 1):
            barrier = asyncio.Barrier(args.requests)
            started_at = time.perf_counter()
            results = await asyncio.gather(
                *[
                    send_hold(
                        client,
                        barrier,
                        index,
                        args.locker_code,
                        args.duration_hours,
                    )
                    for index in range(args.requests)
                ]
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)

            winners: list[dict] = []
            conflicts = 0
            unexpected = []
            for index, result in results:
                if isinstance(result, Exception):
                    unexpected.append(f"request {index}: {result}")
                    continue
                if result.status_code == 201:
                    try:
                        booking = result.json()
                    except ValueError:
                        unexpected.append(f"request {index}: invalid JSON in HTTP 201.")
                    else:
                        if isinstance(booking, dict):
                            winners.append(booking)
                        else:
                            unexpected.append(f"request {index}: HTTP 201 body was not an object.")
                elif result.status_code == 409:
                    try:
                        detail = result.json().get("detail")
                    except ValueError:
                        detail = None
                    if detail == "NO_LOCKER_AVAILABLE":
                        conflicts += 1
                    else:
                        unexpected.append(
                            f"request {index}: unexpected HTTP 409 detail {detail!r}."
                        )
                else:
                    unexpected.append(
                        f"request {index}: unexpected HTTP {result.status_code} "
                        f"{result.text[:200]}"
                    )

            cleanup_failures = await cancel_winners(client, winners)
            passed = (
                len(winners) == 1
                and conflicts == args.requests - 1
                and not unexpected
                and not cleanup_failures
            )
            print(
                f"Round {round_number}/{args.rounds}: "
                f"requests={args.requests}, won={len(winners)}, "
                f"conflicts={conflicts}, elapsed={elapsed_ms} ms, "
                f"result={'PASS' if passed else 'FAIL'}"
            )
            for booking in winners:
                print(
                    f"  booking={booking.get('public_id', 'unknown')} "
                    f"state={booking.get('state', 'unknown')}"
                )
            for message in unexpected + cleanup_failures:
                print(f"  ERROR: {message}", file=sys.stderr)

            if not passed:
                return 1

        print(
            f"PASS: {args.requests} simultaneous requests per round; "
            f"{args.rounds} round(s). Winning unpaid holds were cancelled."
        )
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Race concurrent hold requests against one locker. By default, "
            "targets only the local API and cancels successful unpaid holds."
        )
    )
    parser.add_argument("--locker-code", required=True, help="Available locker code to race for.")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=100,
        help="Simultaneous requests per round, from 2 to 500 (default: 100).",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Sequential race rounds; the winning unpaid hold is cancelled between rounds.",
    )
    parser.add_argument(
        "--duration-hours",
        type=int,
        default=1,
        help="Requested booking duration (default: 1 hour).",
    )
    parser.add_argument(
        "--phone",
        help="Sign in using the local development OTP flow instead of LOCKER_TEST_TOKEN.",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permit a non-local API URL; use only with a test environment you control.",
    )
    args = parser.parse_args()
    parsed_url = urlparse(args.base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        parser.error("--base-url must be an absolute http:// or https:// URL.")
    if not 2 <= args.requests <= 500:
        parser.error("--requests must be between 2 and 500.")
    if not 1 <= args.rounds <= 100:
        parser.error("--rounds must be between 1 and 100.")
    if not 1 <= args.duration_hours <= 72:
        parser.error("--duration-hours must be between 1 and 72.")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except (httpx.HTTPError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error
