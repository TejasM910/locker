# Spec-Driven Workflow

Specs are the source of truth. Code follows specs, never the other way round.

## The loop (for every feature)
1. **Requirement**: add or change a `REQ-*` entry in `requirements.md` (EARS style, testable).
2. **Design**: update `design.md`, `booking-state-machine.md`, `mqtt-contract.md` if behaviour changes.
3. **Contract**: update `openapi.yaml` (mark unbuilt endpoints `x-status: planned`).
4. **Tasks**: add tasks to `tasks.md`, each linking the REQ ids it satisfies.
5. **Tests first**: write tests tagged `@pytest.mark.req("REQ-...")`.
6. **Code**: implement until tests pass.
7. **Gate** (`make verify`): the contract test fails if the API and `openapi.yaml` disagree, and the traceability test fails if an M1 REQ has no test.

## Recent implementation snapshot (today)
- An initial Alembic migration now defines the six MySQL application tables for a fresh local setup.
- `CREATED` bookings now expire automatically via a background expiry worker and free their locker.
- The app listens for locker events (`door_closed`, `pickup_completed`) and updates the booking state/audit trail accordingly.
- Razorpay webhook verification and duplicate-event handling are implemented and aligned with the payment requirements.
- MQTT unlock commands and locker command publishing are live. Booking routes support OTP bearer auth, with `X-User-Id` retained as a development fallback.
- The M2 PWA now supports OTP sign-in, QR/manual location selection, booking, Razorpay checkout, status refresh, and unlock requests.
- On 2026-11-03, the user confirmed a Razorpay test payment worked; webhook requests reached the API with HTTP 200 and a booking was shown as `PAID`.
- On 2026-11-03, the user ran a local Mosquitto event simulation and confirmed the booking reached `RELEASED`.
- Outbound unlock command receipt by a subscriber/firmware and physical locker actuation are not yet confirmed.

## Current status summary
- **Completed and locally exercised:** core M1 backend, OTP development flow, PWA booking flow, Razorpay test checkout/webhook, and inbound MQTT event simulation through locker release.
- **Next:** verify the outbound unlock command with `mosquitto_sub`, then connect a device/simulator.
- **Remaining:** production SMS and durable auth storage, then device firmware/simulator and M3 operational, overstay, refund, and tamper features.
- **Visible next tasks:** hardware simulator, overstay/refund logic, tamper alerts, and operator dashboard.

## Files
| File | Purpose |
|---|---|
| requirements.md | What the system must do, with IDs |
| design.md | Architecture, schema, decisions (ADRs) |
| booking-state-machine.md | Legal booking states and transitions |
| mqtt-contract.md | Topics and payloads between cloud and locker |
| openapi.yaml | HTTP API contract |
| tasks.md | Build plan linked to REQ ids |
