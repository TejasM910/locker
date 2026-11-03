# Design

## Architecture
Next.js PWA -> FastAPI -> MySQL; FastAPI <-> MQTT broker <-> locker (ESP32 or simulator); Razorpay -> FastAPI webhook.

The PWA uses the OTP endpoints for bearer-token sign-in, booking endpoints for reservation/status/unlock, and Razorpay Checkout for payment. Its camera scanner accepts a numeric location ID or a URL containing a `location_id` query parameter; manual ID entry remains available. API calls are not cached by the PWA service worker. Payment completion is confirmed by the backend webhook before the UI considers the booking paid. Location discovery is not yet available.

## Current development checkpoint (2026-11-03)
- The local MySQL schema and demo inventory were created manually; confirm the API points at the same `locker` database.
- OTP sign-in, booking creation, and Razorpay test checkout were exercised locally. The user confirmed the payment flow worked; the UI showed a `PAID` booking and webhook requests reached the API with HTTP 200.
- Expiry worker fix: expiration batches now execute within a committing database transaction so expired `CREATED` bookings and released lockers persist together.
- Unlock audit logging now commits the transaction already opened by the booking ownership query. The endpoint returned HTTP 202 and wrote an audit event in the local test.
- A local Mosquitto broker and CLI event publisher were used to simulate `door_closed` and `pickup_completed`; the user confirmed the booking reached `RELEASED`. This verifies inbound MQTT event processing and locker release in the manual simulation.
- Outbound unlock command receipt by an MQTT subscriber/firmware and physical locker actuation remain unverified. Earlier disabled-MQTT runs only logged the would-be command.
- Razorpay test credentials/webhook are configured locally by the user. Do not copy secret values into this document or source control.
- Development OTPs and bearer tokens are held in process memory; restarting/reloading the API invalidates existing OTPs and tokens. Sign in again after an API restart.

## Key decisions (ADRs)
- **ADR-1 Server decides unlock** (REQ-SEC-001). Phone never talks to the locker.
- **ADR-2 Row locking**: reserve a locker with `SELECT ... FOR UPDATE SKIP LOCKED` inside one transaction (REQ-BOOK-002).
- **ADR-3 Webhook idempotency**: `payments.gateway_event_id` has a UNIQUE index; duplicate insert means already processed (REQ-PAY-003).
- **ADR-4 Money in paise** (REQ-PAY-004). **UTC everywhere** (REQ-NFR-001).
- **ADR-5 Public ids**: random 32-char hex `public_id` for URLs/QR (REQ-BOOK-005).
- **ADR-6 State changes only via `state_machine.apply`**, never by assigning `booking.state` directly.

## Recent implementation updates (as of today)
- **Booking expiry worker**: the app now runs a background loop that finds `CREATED` bookings with `expires_at <= now`, applies `expire`, and frees the locker while writing a `booking_expired` audit event.
- **Locker event ingestion**: the MQTT listener subscribes to `lockers/+/evt` and calls `handle_device_event()`, which records `door_closed` and `pickup_completed` events and frees the locker when the booking reaches a terminal state.
- **Payment flow**: webhooks validate Razorpay signatures, ignore unrelated events, and apply the payment only when the order matches and the event id is unique; duplicate deliveries are treated as idempotent no-ops.
- **Unlock path**: the booking unlock route checks ownership and state before publishing an MQTT command to the device and logging the request in `locker_events`.
- **Dev auth guardrail**: the API accepts OTP bearer tokens for user auth and keeps `X-User-Id` as a dev fallback during local testing; the OTP flow is enforced with rate limiting.

## Schema
users(id, phone UNIQUE, created_at)
locations(id, name, address, lat, lng)
lockers(id, location_id FK, size, device_id UNIQUE, status ENUM(free,held,offline))
bookings(id, public_id UNIQUE, user_id FK, locker_id FK, state, amount_paise, duration_hours, razorpay_order_id UNIQUE, created_at, expires_at, paid_at, ends_at)
payments(id, booking_id FK, gateway_payment_id, gateway_event_id UNIQUE, amount_paise, status, created_at)
locker_events(id, locker_id FK, booking_id FK null, type, payload JSON, created_at)
