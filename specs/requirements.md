# Requirements (EARS format)

Status: `M1` = in the first milestone, `M2` = in the second milestone, `later` = specified but not built yet.

## Booking
- **REQ-BOOK-001** (M1): When a traveler submits a location, size and duration, the system shall create a booking in state `CREATED` and reserve one free locker of that size.
- **REQ-BOOK-002** (M1): While two requests compete for the last free locker, the system shall assign it to exactly one booking.
- **REQ-BOOK-003** (M1): If no locker of the requested size is free, the system shall reject the request with HTTP 409 `NO_LOCKER_AVAILABLE`.
- **REQ-BOOK-004** (M1): If a booking is not paid within 15 minutes, the system shall expire it and release the locker.
- **REQ-BOOK-005** (M1): The system shall expose bookings only by an unguessable `public_id`, never by sequential id.

## Payment
- **REQ-PAY-001** (M1): When Razorpay sends a verified `payment.captured` webhook, the system shall move the booking `CREATED -> PAID`.
- **REQ-PAY-002** (M1): If the webhook signature is invalid, the system shall reject it with HTTP 400 and change nothing.
- **REQ-PAY-003** (M1): If the same webhook event is delivered more than once, the system shall apply it once and return HTTP 200 each time.
- **REQ-PAY-004** (M1): All amounts shall be stored as integer paise.
- **REQ-PAY-005** (later): If a paid booking is never used within 24 hours, the system shall refund it.

## Unlock and storage
- **REQ-LOCK-001** (M1): When the owner of a `PAID`, `STORED` or `OVERSTAY` booking requests unlock, the system shall publish an unlock command to the locker and return HTTP 202.
- **REQ-LOCK-002** (M1): If the booking is in any other state or belongs to another user, the system shall refuse to unlock.
- **REQ-LOCK-003** (M1): When the locker reports `door_closed` for a `PAID` booking, the system shall move it to `STORED`.
- **REQ-LOCK-004** (M1): When the locker reports `pickup_completed`, the system shall move the booking to `RELEASED` and free the locker.
- **REQ-LOCK-005** (later): While the locker has no internet, a PIN issued at booking time shall still open it locally.
- **REQ-LOCK-006** (M1): Every unlock, lock and alert shall be written to the audit log (`locker_events`).

## Overstay
- **REQ-OVER-001** (later): When the paid end time passes while `STORED`, the system shall move the booking to `OVERSTAY` and notify the traveler.

## Security and tamper
- **REQ-SEC-001** (M1): The server alone decides whether a locker opens; the client never sends unlock commands to a locker.
- **REQ-SEC-002** (later): When the locker reports `tamper`, the system shall alert the operator within 10 seconds and save the camera clip.
- **REQ-SEC-003** (later): The camera shall record only between payment and checkout, with a visible notice and a retention limit.
- **REQ-SEC-004** (M2): The system shall limit OTP requests to five per phone number per ten minutes and unlock requests to 30 per authenticated user per minute.

## Traveler app
- **REQ-PWA-001** (M2): The traveler-facing PWA shall allow a traveler to verify their phone, select a location by scanning a location QR code or entering its ID, reserve a locker by size/duration, pay for the booking, view booking status, and request unlock.

## Operations
- **REQ-OPS-001** (M1): The system shall expose `GET /health` that checks the database.
- **REQ-OPS-002** (later): The operator dashboard shall show live locker status, alerts and revenue.

## Non-functional
- **REQ-NFR-001** (M1): All timestamps shall be stored in UTC.
- **REQ-NFR-002** (M1): The HTTP API shall match `openapi.yaml` (enforced by the contract test).
