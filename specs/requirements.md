# Requirements (EARS format)

Status: `M1` = in the first milestone, `M2` = in the second milestone, `later` = specified but not built yet.

## Booking
- **REQ-BOOK-001** (M1): When a traveler submits a locker selection and duration, the system shall create a booking in state `CREATED` and reserve the selected free locker.
- **REQ-BOOK-002** (M1): While two requests compete for the last free locker, the system shall assign it to exactly one booking.
- **REQ-BOOK-003** (M1): If no free locker matches the requested location and size, or the selected locker code is unavailable, the system shall reject the request with HTTP 409 `NO_LOCKER_AVAILABLE`.
- **REQ-BOOK-004** (M1): If a booking is not paid within 15 minutes, the system shall expire it and release the locker.
- **REQ-BOOK-005** (M1): The system shall expose bookings only by an unguessable `public_id`, never by sequential id.
- **REQ-BOOK-006** (M2): Each physical locker shall have a stable, human-readable `locker_code` unique across the system (for example, `CH101` for a Church Street locker). Codes shall be persisted independently of database row order after creation; location-name-derived prefixes shall be disambiguated deterministically when needed.
- **REQ-BOOK-007** (M2): When a traveler selects an available locker by `locker_code`, the booking API shall reserve that exact locker transactionally and reject stale/unavailable selections without assigning a different locker.

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
- **REQ-PWA-001** (M2): The traveler-facing PWA shall allow a traveler to verify their phone, find a locker location on the map, reserve a locker by public code and duration, pay for the booking, view booking status, and request unlock.
- **REQ-PWA-002** (M2): When no valid traveler session exists, the app shall show only its phone/OTP sign-in screen; after successful sign-in (or restoration of a valid session), it shall show the nearby-locker map as the primary screen. The map and booking controls shall not be available before authentication.
- **REQ-PWA-003** (M2): After a traveler chooses a locker location and starts directions, the app shall show the choose-a-locker flow only after foreground GPS reports arrival within 50 m of the destination; it shall also offer explicit manual arrival confirmation and explain when GPS is unavailable.
- **REQ-PWA-004** (M2): On arrival confirmation, the app shall fetch and display the individual lockers at that location, including each stable locker code, size, and current availability. The traveler shall select an available locker code and duration before the app creates a hold and opens a dedicated payment/booking screen. The app shall not automatically charge the traveler; the traveler must explicitly start checkout.

## Nearby locker discovery
- **REQ-LOC-001** (M2): When the nearby map opens and the traveler has granted browser location permission (or grants it when prompted), the app shall center on the traveler's current position and show registered locker locations no farther than 1 km from that position.
- **REQ-LOC-002** (M2): If location access is unavailable or denied, the app shall let the traveler choose a map search area without requiring device location access.
- **REQ-LOC-003** (M2): For each displayed location, the app shall show the available locker count by size and distinguish locations with no currently free lockers.
- **REQ-LOC-004** (M2): When a traveler selects a location marker, the app shall show its name, address, distance from the traveler's latest position (or manual search center when location is unavailable), and availability, and shall allow the traveler to start directions to that location.
- **REQ-LOC-005** (M2): When the app refreshes nearby locations, the API shall calculate availability from lockers currently marked `free`; the booking endpoint shall remain the authority for reservation, and the app shall handle a locker becoming unavailable between discovery and booking.
- **REQ-LOC-006** (M2): When browser location permission is granted, the app shall start foreground location updates automatically while the nearby map is open, visibly indicate that updates are active, and update the user's map position and 1 km nearby results as the traveler moves. It shall stop watching when the map is hidden or closed or the traveler disables location, shall not persist precise coordinates on the server or in application logs, and shall limit movement-triggered nearby searches to at most one every 15 seconds and only after at least 50 m of movement.
- **REQ-LOC-007** (M2): If browser location permission is denied or unavailable, the app shall explain the limitation and retain manual map browsing and area search without requiring device coordinates.
- **REQ-LOC-008** (M2): When a traveler selects a locker marker while foreground device location is available and explicitly starts driving directions after seeing the coordinate-sharing disclosure, the app shall request a route from the current position to that locker and display the route line, total distance, estimated duration, and current next-turn instruction on the map.
- **REQ-LOC-009** (M2): While route guidance is active, the app shall update the next-turn instruction from foreground location updates, reroute when the traveler moves materially off route while respecting a minimum reroute interval, and allow the traveler to stop guidance. The app shall not imply that it provides pedestrian routing or emergency guidance.
- **REQ-LOC-010** (M2): Before route calculation, the app shall disclose that precise origin and destination coordinates are sent to the public OSRM demo routing service, and it shall not persist those coordinates as route history. If routing fails or location is unavailable, the app shall explain the failure and preserve the selected locker and booking flow.
- **REQ-LOC-011** (M2): While driving directions are active, the app shall recognize arrival when foreground GPS is within 50 m of the selected location, offer an “I’m here” confirmation, and allow the traveler to proceed to individual locker selection. If GPS is unavailable or inaccurate, manual confirmation shall remain available without weakening booking authorization or locker availability checks.

## Operations
- **REQ-OPS-001** (M1): The system shall expose `GET /health` that checks the database.
- **REQ-OPS-002** (later): The operator dashboard shall show live locker status, alerts and revenue.

## Non-functional
- **REQ-NFR-001** (M1): All timestamps shall be stored in UTC.
- **REQ-NFR-002** (M1): The HTTP API shall match `openapi.yaml` (enforced by the contract test).
