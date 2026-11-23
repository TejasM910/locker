# Booking State Machine

States: `CREATED, PAID, STORED, OVERSTAY, RELEASED, EXPIRED, CANCELLED, DISPUTED`

| From | Event | To | Trigger | REQ |
|---|---|---|---|---|
| CREATED | payment_captured | PAID | Razorpay webhook | REQ-PAY-001 |
| CREATED | expire | EXPIRED | 15 min timer | REQ-BOOK-004 |
| CREATED | cancel | CANCELLED | user | |
| PAID | door_closed | STORED | locker event | REQ-LOCK-003 |
| PAID | expire | EXPIRED | 24 h unused (refund) | REQ-PAY-005 |
| STORED | time_exceeded | OVERSTAY | timer | REQ-OVER-001 |
| STORED | pickup_completed | RELEASED | locker event | REQ-LOCK-004 |
| OVERSTAY | pickup_completed | RELEASED | locker event | REQ-LOCK-004 |
| STORED / OVERSTAY | dispute | DISPUTED | operator | |
| DISPUTED | resolve | RELEASED | operator | |

Rules:
- Any (state, event) pair not listed is illegal and raises `InvalidTransition`.
- Terminal states: `RELEASED, EXPIRED, CANCELLED`.
- Locker is held in states `CREATED, PAID, STORED, OVERSTAY, DISPUTED` and freed on terminal states.
- Replaying an event that already produced the current state is a no-op (idempotency), handled by the caller.

## Customer active-locker view

- After a locker is reserved, show an **Active locker** button in the signed-in header beside **Sign out**.
- After a locker is reserved, return the customer to the map.
- Selecting **Active locker** opens a dedicated view with the booking's public locker code, size, booking state, duration, and remaining time. Its **Back to map** button returns to the map.
- Keep the payment action for an unpaid reservation and the unlock action for an eligible paid booking in the active-locker view.
- While the booking is `CREATED`, remaining time counts down to `expires_at`, the end of the unpaid reservation hold.
- After payment is confirmed (`PAID`, `STORED`, `OVERSTAY`, or `DISPUTED`), remaining time counts down to `ends_at`, calculated from payment capture plus the booked duration. The UI refreshes this countdown at least once per second and refreshes booking state from the API.
- At zero, show that the booked time has elapsed; do not imply that the booking has been released unless the backend reports a terminal state.
- Terminal bookings (`RELEASED`, `EXPIRED`, `CANCELLED`) are not presented as active lockers.
- API booking responses include `ends_at` so clients can render the paid booking countdown. Timestamps are UTC.

## Edge case state behaviors

- **Late Payment Capture on Expired Hold (`REQ-BOOK-009`)**:
  - When a `payment.captured` webhook arrives after the 15-minute hold timer expired, the system checks whether the locker is still free and unreserved.
  - If still free, the booking is safely resurrected directly from `EXPIRED -> PAID`, and the locker is marked held.
  - If the locker was already rebooked by another traveler, the booking remains terminal (`EXPIRED`), the payment record is tagged `late_captured_refund_required`, and a `late_payment_refund_required` audit event is dispatched for the automated refund pipeline.
- **Hardware Fault / Tamper (`REQ-LOCK-007`)**:
  - Inbound device events `tamper_detected` or `err:door_jammed` transition the physical locker's status to `offline` to prevent assigning damaged hardware.
- **Single Active Hold per Traveler (`REQ-BOOK-008`)**:
  - A traveler is restricted to at most one unpaid `CREATED` hold at a time to mitigate inventory hoarding.
