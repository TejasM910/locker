# Tasks

## Current completion status
- **Backend foundation (M1): complete**
- **OTP auth backend (M2 development flow): complete**
- **Completed tasks:** T1 through T8, OTP development flow + rate limiting, and the core traveler PWA flow are implemented.
- **Current project status (2026-11-07):** implemented an OTP-first entry screen, authenticated map-first flow, 50 m arrival detection/manual confirmation, stable public locker codes, exact-code transactional reservation, and a dedicated payment screen. Backend tests and frontend typecheck/production build pass. Browser verification of the full new journey and live route arrival remains pending.
- **Database setup (2026-11-04):** an initial Alembic migration is checked in for new databases; existing manually created databases must be backed up and compared before applying it.
- **Payment test configuration:** Razorpay test checkout and the `payment.captured` webhook are configured and working for the demonstrated local test. Keep test credentials private and out of source/spec files.
- **Fix from 2026-11-03:** expired booking cleanup now commits its state and locker-release updates transactionally; a regression test covers the worker transaction.
- **Fix from 2026-11-03:** unlock audit logging now commits the existing SQLAlchemy transaction, avoiding the `A transaction is already begun on this Session` 500; regression-tested.
- **MQTT simulator test (2026-11-03):** local Mosquitto broker and CLI event publisher were used; user confirmed the test booking reached `RELEASED`. This verifies the inbound event path and locker release behavior for the manual simulation.
- **Still unverified:** outbound unlock command receipt by a subscriber/firmware and physical locker actuation.
- **Next session:** verify outbound command receipt with `mosquitto_sub`, then connect/test firmware or a simulator.
- **Remaining work:** production SMS and durable auth storage, device firmware/simulator, and the M3/"later" requirements.

## M1 Backend foundation
- [x] T1 Specs, state machine, contract test (REQ-NFR-002)
- [x] T2 Models + Alembic setup (REQ-NFR-001, REQ-PAY-004)
- [x] T3 Create booking with locker reservation (REQ-BOOK-001/002/003/005)
- [x] T4 Razorpay webhook with signature + idempotency (REQ-PAY-001/002/003)
- [x] T5 Unlock endpoint + MQTT publish + audit log (REQ-LOCK-001/002/006, REQ-SEC-001)
- [x] T6 Device event handler (REQ-LOCK-003/004)
- [x] T7 Health check (REQ-OPS-001)
- [x] T8 Booking expiry worker (REQ-BOOK-004)

## M2 - current status
- [x] Phone OTP development flow + OTP/unlock rate limiting (REQ-SEC-004)
- [x] Next.js PWA base flow: OTP sign-in -> map discovery -> coded-locker booking -> payment -> view booking -> request unlock (REQ-PWA-001)
- [x] Nearby map and bounded availability search: MapLibre/OpenFreeMap pilot, auto-start permission-based foreground location, manual map browsing, 1 km search, 20-second visible-map refresh, and per-size inventory snapshot (REQ-LOC-001..007)
- [ ] Verify location permission and live movement on the user's HTTPS/ngrok device; automated browser verification used simulated coordinates.
- [x] Add an idempotent local-only seeder for three Bengaluru demo points, each with two medium lockers and one large locker.
- [ ] Complete the user's browser-based flow from demo marker selection through reservation and confirm the test locker hold.
- [x] Implement in-app OSRM driving-route drawing and foreground next-turn guidance, with off-route rerouting, coordinate disclosure, and stop control (REQ-LOC-008..010); frontend typecheck and production build pass.
- [ ] Browser-verify route rendering, next-turn progression, rerouting, route failure fallback, and coordinate sharing using simulated GPS and a mocked OSRM response.
- [x] Gate the app behind OTP sign-in and make the authenticated map the primary screen (REQ-PWA-002).
- [x] Add 50 m arrival detection and manual arrival confirmation before exposing individual locker selection (REQ-PWA-003, REQ-LOC-011).
- [x] Add stable location-derived unique locker codes, inventory retrieval, and exact-code transactional reservation (REQ-BOOK-006..007, REQ-PWA-004).
- [x] Add the dedicated booking/payment step with explicit checkout and unlock only after verified payment (REQ-PWA-004).
- [ ] Browser-verify sign-in -> navigation/arrival -> locker-code selection -> payment -> paid-state unlock using simulated GPS and test payment.
- [ ] Add MQTT heartbeat freshness/offline handling before presenting inventory as verified live availability; current map uses persisted locker status only.
- [ ] Load-test map queries and availability polling for the proposed 100-1,000 concurrent-user range; current per-IP rate limiter is process-local.
- [x] Configure and verify Razorpay test checkout + captured-payment webhook end to end (local test; user-confirmed; webhook requests returned HTTP 200)
- [ ] Production SMS delivery and durable OTP/token storage
- [ ] ESP32 / simulator firmware per mqtt-contract.md
- [x] Configure local Mosquitto and simulate inbound `door_closed` / `pickup_completed` events (booking reached `RELEASED`)
- [ ] Verify outbound unlock command receipt with MQTT subscriber and a device/simulator

## M3 / later features still pending
- [ ] Overstay handling and traveler notification (REQ-OVER-001)
- [ ] 24-hour unused booking refund flow (REQ-PAY-005)
- [ ] Tamper alerts and camera clip workflow (REQ-SEC-002, REQ-SEC-003)
- [ ] Operator dashboard for live locker status, alerts and revenue (REQ-OPS-002)
- [ ] Offline local PIN unlock while locker has no internet (REQ-LOCK-005)

## Completion summary by requirement status
- **M1 completed / implemented:** REQ-BOOK-001, 002, 003, 004, 005; REQ-PAY-001, 002, 003, 004; REQ-LOCK-001, 002, 003, 004, 006; REQ-SEC-001; REQ-OPS-001; REQ-NFR-001, 002
- **M2 implemented and locally exercised:** REQ-PWA-001; Razorpay test checkout/webhook flow user-confirmed
- **Nearby map requirement status:** REQ-LOC-001..007 implemented with a 1 km maximum radius; device location requires browser permission and HTTPS outside localhost. Demo location seeding is implemented; browser verification through reservation remains pending.
- **Navigation requirement status:** REQ-LOC-008..010 implemented as best-effort OSRM driving guidance. Automated browser interaction with simulated GPS and mocked routing remains to be verified; public OSRM is not a production dependency.
- **Authenticated locker journey status:** REQ-PWA-002..004, REQ-LOC-011, and REQ-BOOK-006..007 implemented and covered by backend tests/typecheck/build. Live browser verification remains pending; codes are stable public display codes and are distinct from MQTT device IDs.
- **M2 completed:** OTP development flow and rate limiting (REQ-SEC-004)
- **M2 pending:** production auth storage/SMS; outbound MQTT command/device verification and firmware/simulator
- **Later / not yet built:** REQ-LOCK-005, REQ-OVER-001, REQ-PAY-005, REQ-SEC-002, REQ-SEC-003, REQ-OPS-002
