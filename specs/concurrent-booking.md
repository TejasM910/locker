# Concurrent Booking of Limited Resources

This design applies to lockers, seats, rooms, time slots, and parking spaces. The application-specific implementation in this repository uses FastAPI, SQLAlchemy, and MySQL. The database is the source of truth; Redis is an optional optimization, not the authority.

## Resource lifecycle

`AVAILABLE -> HELD -> BOOKED/OCCUPIED -> RELEASED -> AVAILABLE`

| Transition | Trigger | Required guard |
|---|---|---|
| `AVAILABLE -> HELD` | Customer requests a hold | Atomically claim the available resource and set its hold deadline |
| `HELD -> BOOKED/OCCUPIED` | Verified payment or other required confirmation | The same booking still owns an unexpired hold |
| `HELD -> RELEASED` | Customer cancels, confirmation fails, or hold expires | Release only if the booking still owns the hold |
| `BOOKED/OCCUPIED -> RELEASED` | Customer completes use or an authorized operator releases it | Verify booking/resource ownership and record an audit event |
| `RELEASED -> AVAILABLE` | Release transaction completes | No other active booking owns the resource |

`HELD` is temporary and is not a confirmed booking. A hold has an owner, a unique booking identifier, and an expiry timestamp. A typical interactive payment hold lasts 5–10 minutes; configure the duration for the product and gateway rather than treating this range as a guarantee.

## Two-phase customer flow

1. **Hold:** The customer selects a resource. In a short database transaction, atomically claim it, create a `HELD` booking, and record its expiry. Return the booking and a countdown deadline. If no resource can be claimed, return `409 Conflict` (`RESOURCE_UNAVAILABLE`).
2. **Confirm:** The customer completes payment or another verification step. Only a verified confirmation for the same booking, resource, amount, and still-valid hold may change `HELD` to `BOOKED`.
3. **Release:** Cancellation, payment failure, or expiry releases the resource. If a payment succeeds after the hold has expired or been claimed by someone else, do not resurrect that booking; reconcile and refund or escalate it.

```text
Customer        API             MySQL             Payment gateway
   | hold        |                 |                      |
   |------------>| lock + create   |                      |
   |             |---------------> |                      |
   |             |<--------------- | HELD + expires_at    |
   |<------------| booking + TTL   |                      |
   | pay         |                 |                      |
   |------------------------------------------------------>|
   |             |                 |<-- signed callback --|
   |             | verify callback | lock and revalidate  |
   |             |---------------> | HELD -> BOOKED       |
   |<------------| confirmed       |                      |
```

## Concurrency control options

Use one authoritative atomic claim mechanism. Avoid treating several independent locks as interchangeable sources of truth.

### Database compare-and-set

```sql
UPDATE lockers
SET status = 'held', held_by = :user_id, expires_at = :deadline
WHERE id = :locker_id AND status = 'free';
```

One updated row means the claim won; zero rows means it lost. A transaction can create the booking and claim the resource together. This is a strong default for a relational application.

### Pessimistic row lock

```sql
SELECT id FROM lockers
WHERE id = :locker_id AND status = 'free'
FOR UPDATE SKIP LOCKED;
```

Only one transaction can claim a row at a time; a competitor skips it and reports unavailable. This matches the current MySQL implementation. Keep the transaction short: row locks add waiting/skip behavior and database load under hot-resource traffic. Never hold a row lock while calling a payment provider or other network service.

### Optimistic version column

```sql
UPDATE lockers
SET status = 'held', version = version + 1
WHERE id = :id AND status = 'free' AND version = :expected_version;
```

The winner increments the version; a zero-row update must reload and retry or report a conflict. This works well when contention is low and conflicts can be retried.

### Redis distributed lock

```text
SET seat:show123:A5 <random-owner-token> NX EX 600
```

The first request wins; the key expires after 600 seconds. Release it only with an atomic compare-owner-and-delete operation. Redis can reduce pressure or coordinate fast admission, but the database must still enforce the final booking invariant. Redis loss, expiry, or cache inconsistency must not permit two confirmed bookings.

### Unique constraints

For non-time-based, single-capacity resources, a unique active-booking key can be enforced using a database-supported generated column or equivalent constraint. For time-based bookings, prevent overlapping active ranges transactionally (or use a database range/exclusion constraint where supported); a plain unique `(resource_id, start_time)` does not prevent overlaps.

Constraints are a final safety net, not a substitute for useful conflict handling. MySQL has no partial unique index; select an enforcement strategy compatible with the deployed schema and migration path.

### Choosing

- Start with a short relational transaction and conditional update or row lock when the resource and booking data live in the same SQL database.
- Add optimistic retries for demonstrably low-conflict workloads.
- Add Redis for high-throughput coordination only when measured database contention justifies the extra failure modes; keep SQL authoritative.
- Use time-range overlap protection when customers book resources for intervals. This app's current locker flow reserves a locker for a paid duration but does not allow customers to schedule future overlapping intervals.

## Data model

Illustrative relational schema:

```text
resources(
  id PK, status, version, current_hold_id NULL,
  maintenance_state, updated_at
)

bookings(
  id PK, public_id UNIQUE, user_id, resource_id FK,
  state,
  held_at, hold_expires_at, starts_at NULL, ends_at NULL,
  created_at, updated_at
)

booking_requests(
  id PK, user_id FK, idempotency_key, request_hash,
  booking_id UNIQUE FK
)

payments(
  id PK, booking_id FK, provider_payment_id,
  provider_event_id UNIQUE, amount, currency, status, created_at
)
```

Add indexes for `(resource_id, state, hold_expires_at)`, `(user_id, created_at)`, and any lookup/idempotency key. Enforce one active hold/booking per single-capacity resource using a database-compatible unique invariant or a serialized resource-row transaction. Add a unique provider event ID so a retried webhook cannot be applied twice. Time-based resources also require overlap validation in the transaction.

For this locker repository, `lockers.status`, `bookings.state`, `bookings.expires_at`, and `bookings.ends_at` provide the resource lifecycle. MySQL row locks and transactional updates remain authoritative; `booking_requests` persists request idempotency, and `payments.gateway_event_id` is unique. Availability also excludes lockers with active booking records, so stale `free` locker status cannot make an occupied booking available. Redis, future time slots, maintenance workflows, and a generalized resource table are not prerequisites for this implementation.

## API contract

```text
POST /v1/bookings
  Idempotency-Key: <client-generated key>
  -> 201 booking HELD, locker, hold_expires_at
  -> 409 RESOURCE_UNAVAILABLE
  -> same key + same request: return the original result
  -> same key + different request: 409 IDEMPOTENCY_KEY_REUSED

POST /v1/bookings/{id}/confirm
  Idempotency-Key: <payment/confirmation key>
  -> confirm only after validating provider evidence and the unexpired hold

POST /v1/bookings/{id}/cancel
GET  /v1/bookings/{id}
```

Payment-provider webhooks authenticate using the provider signature and use the provider's unique event ID for idempotent processing. A failed payment attempt may still be retried, so it should not release the hold unless the provider confirms the failure is terminal. Cancellation or expiry releases the remaining hold. Never accept a client claim of payment as proof.

## Edge cases and recovery

- **Same-instant requests:** The database serializes competing writes/row locks. Exactly one claim succeeds; losers receive a conflict.
- **Abandoned hold:** A TTL is checked during claim/confirm, and a background worker releases expired holds. Do not rely on Redis TTL alone to update SQL.
- **Expiry during payment:** Confirmation locks and rechecks booking state and expiry using the authoritative database clock. If expired, reject confirmation and reconcile/refund any captured payment.
- **Late payment:** Do not silently restore a resource already released or re-held. Refund or send to an explicit operator reconciliation flow.
- **Duplicate requests/webhooks:** Store idempotency keys and unique provider event IDs. Return the original result for the same request; reject a reused key with a different payload.
- **Double-click/multiple devices:** Same key returns the original booking. Different keys still contend on the resource's atomic database claim; at most one wins.
- **Cancellation/extension/modification:** Serialize against expiry and payment by locking the booking and resource; verify ownership, state, and new availability before changing deadlines or releasing.
- **Overlapping time ranges:** Under the resource lock, reject any interval overlapping an active reservation. Use half-open intervals `[start, end)` so adjacent bookings do not overlap.
- **Fault/maintenance:** Mark unavailable under the same transaction protocol; do not release an occupied resource as though it were empty. Notify affected customers/operators and define refund or relocation policy.
- **Crash after claim:** Commit resource and booking changes in one SQL transaction. A rollback leaves neither change; a committed hold is recoverable by expiry cleanup.
- **Clock skew:** Use database time (or a single authoritative time source) for expiry comparisons; store UTC timestamps.
- **Cache/DB divergence:** Rebuild cache from SQL and never confirm based only on a cache hit.
- **Horizontal scaling:** All API instances use the same authoritative database invariants and idempotency store; process-local locks are insufficient.
- **Traffic bursts/fairness:** Rate-limit abusive clients, cap retries, queue or apply admission control for hot resources, and measure queue fairness. Do not promise FIFO fairness unless explicitly implemented.
- **Overdue usage:** A scheduled worker can mark overdue bookings and notify/escalate. Automatic release must not make a physically occupied locker available; use a grace period and pickup/door events as appropriate.

## Background jobs and observability

- Periodically release only expired `HELD` bookings whose state and resource ownership still match.
- Reconcile pending payments against provider events; separately report captured payments for expired/cancelled holds for refund or manual review.
- Log booking ID, resource ID, state transition, idempotency key hash, and provider event ID; never log secrets or payment credentials.
- Track hold wins/conflicts, expiry lag, duplicate requests/events, lock contention, payment-confirmation delay, late payments, and reconciliation backlog. Alert on stuck holds, duplicate-active invariant violations, and webhook failures.

## Testing

- Unit-test each allowed transition and invalid/duplicate event behavior.
- Integration-test concurrent holds against the real database engine. Fire 100 parallel requests at one available locker; assert exactly one succeeds, the rest conflict, one active booking exists, and the locker is held.
- Test expiry racing confirmation, payment arriving after expiry, cancellation racing a new hold, duplicate idempotency keys, duplicate webhooks, and worker/API concurrency.
- Run concurrency tests with separate database connections/processes; an in-memory fake does not verify database locking semantics.

## Implementation checklist

- [ ] Make the database claim and booking creation atomic.
- [ ] Return a stable conflict when another request wins.
- [ ] Enforce idempotent hold creation and provider event handling.
- [ ] Revalidate state, owner, amount, and deadline during confirmation.
- [ ] Expire holds safely and reconcile late payments.
- [ ] Protect active-booking invariants with database constraints/transactions.
- [ ] Add parallel race and expiry/payment tests against the production database engine.
- [ ] Add operational metrics and reconciliation alerts before production traffic.
