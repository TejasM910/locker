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
