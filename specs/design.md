# Design

## Architecture
Next.js PWA -> FastAPI -> MySQL; FastAPI <-> MQTT broker <-> locker (ESP32 or simulator); Razorpay -> FastAPI webhook.

The PWA uses the OTP endpoints for bearer-token sign-in, booking endpoints for reservation/status/unlock, and Razorpay Checkout for payment. An unauthenticated visitor sees only the sign-in screen; an authenticated traveler lands on the nearby map. The map-to-booking journey is location selection -> driving directions -> GPS arrival within 50 m or explicit manual arrival confirmation -> fetch/select an available individual locker -> create a hold -> dedicated payment screen -> verified payment -> explicit unlock request. API calls are not cached by the PWA service worker. Payment completion is confirmed by the backend webhook before the UI considers the booking paid. Actual inventory freshness remains limited to status saved in MySQL. The older QR/manual location-ID booking shortcut is not part of this traveler flow.

### Authenticated map-to-locker flow (REQ-PWA-002..004, REQ-BOOK-006..007)

- Gate the map, locker inventory, and booking controls behind a valid OTP bearer session. On a valid restored session, open the map directly; otherwise render only the phone/OTP sign-in view.
- Selecting a map location keeps the existing public OSRM **driving** guidance. The UI shows an arrival confirmation when GPS is within 50 m of the destination and retains a manual “I’m here” action for inaccurate/unavailable GPS. Manual confirmation advances the UI only; it does not bypass backend checks.
- After arrival, request that location's locker inventory and render each currently free locker with its stable public code and size. Do not select or reserve a locker merely by reaching the location.
- Give each locker a persisted unique `locker_code`, separate from the firmware/MQTT `device_id`. Derive the initial prefix from the first two letters of the first meaningful location-name word (Church Street -> `CH`), assign a human-readable sequence beginning at 101 for each location, and resolve collisions deterministically by extending that first word's prefix, then using additional location-word initials, then a numeric suffix. Codes are assigned during migration/creation, not regenerated from row ordering at runtime. Future location/locker provisioning must follow the same uniqueness rule.
- Booking creation names the chosen `locker_code`; the backend locks and reserves exactly that locker or rejects it as unavailable. It must not silently substitute another locker. Include the assigned locker code in booking details.
- After the exact locker and duration are selected, create the expiring hold and show the dedicated payment screen. Razorpay checkout is opened only after an explicit traveler action. Only a verified `PAID` booking enables the explicit unlock action; the server remains the sole authority for publishing the MQTT command.
- GPS arrival is a usability cue, not proof of physical presence or authorization. The current prototype still has no proximity proof, indoor positioning, or hardware verification; manual confirmation is therefore a deliberate fallback.

## Nearby locker map (REQ-LOC-001..007, REQ-LOC-011)

**Status: the nearby-search API, 1 km map flow, authenticated sign-in-first UI, arrival/manual-confirmation path, stable locker-code inventory, exact-code booking, and dedicated payment step are implemented. Backend tests and frontend typecheck/build pass. Browser verification of the full flow, route progression/rerouting, real-device permission behavior, locker heartbeat freshness, and production capacity remain outstanding.**

### Technology stack and traffic plan

The project owner requested continuous location updates, MQTT-backed locker status with polling initially, and planning for approximately 100-1,000 concurrent app users. These are planning inputs, not verified capacity targets.

| Concern | Proposed choice | Rationale / boundary |
|---|---|---|
| Web map | MapLibre GL JS in the existing Next.js PWA, with its worker bundled through Next.js `setWorkerUrl()` | Open-source map renderer; it does not supply map data or tiles by itself. Explicit worker bundling is required for reliable Next.js/Turbopack map initialization. |
| Map tiles for a pilot | OpenFreeMap public tiles, with required attribution | Its current site states that the public instance allows commercial use at no charge and has no request cap; it also states there is no SLA. Treat it as a pilot option, monitor availability, and do not assume guaranteed capacity for launch. Before launch, confirm current terms and either obtain a provider plan/SLA or budget to self-host/operate tiles. |
| Route calculation for demo navigation | Public OSRM demo route service using its `driving` profile | Selecting a locker requests a road route and turn steps. The public demo is best-effort, may impose usage limits, and is not suitable as a production service. Its verified demo endpoint provides driving, not walking, routes. Replace it with a supported or self-hosted routing service before launch. |
| User location | Browser Geolocation API `watchPosition()` while the map is foregrounded and the user has enabled the feature | Requires explicit permission and HTTPS. Show a clear active indicator and a stop control. This is foreground position refresh, not background tracking; a web PWA cannot promise continuous background location when suspended or closed. |
| Nearby API | Existing FastAPI service; auto-start foreground location when the map opens and permission is available, then bounded REST searches within a fixed 1 km radius | Avoid sending every GPS sample to the server. Send a movement-triggered query after at least 50 m of movement, and no more often than every 15 seconds. Coordinates are transient and are not written to database records or application logs. |
| Location query database | Existing `locations.lat`/`lng` fields with a composite B-tree index; use a bounded latitude/longitude bounding box followed by exact Haversine distance calculation | This first implementation avoids a geometry column. Review a MySQL spatial index/query if measurement shows this index is insufficient. Keep the candidate count, radius, and result count bounded. |
| Locker freshness | MQTT device status/heartbeat into the backend; persist status and `last_seen_at`, and mark stale/offline after an approved heartbeat timeout | Existing MQTT contract describes `heartbeat` and retained `/status`, but the current runtime does not yet provide a verified freshness pipeline. Do not claim hardware is live until this is implemented and tested. |
| Initial availability refresh | REST polling every 20 seconds only while the map is visible, plus refresh after a meaningful map-area change; pause polling when the tab is hidden | Simple to ship with the current API. Map counts are snapshots, not reservations. At 1,000 continuously active map viewers, a 20-second interval is roughly 50 refresh requests/second before caching; load-test this assumption. |
| API load controls | Debounce map movement (500 ms), require at least 50 m movement and 15 seconds between movement-triggered searches, cap search radius at 1 km and results at 100 locations, validate inputs, and apply an in-process 6,000-requests/minute per-IP rate limit | The limit is sized to leave headroom for shared mobile/NAT addresses at the planning target, but it is local-process state, not distributed across workers. Add a shared gateway/limiter before horizontally scaling. |
| Scale-out | Stateless FastAPI workers behind an HTTPS load balancer/reverse proxy, managed MySQL with connection-pool limits, and a production MQTT broker with TLS, per-device authentication and topic ACLs | ngrok and the current no-auth local Mosquitto setup are development-only. SSE can replace frequent polling later if measured traffic or update latency justifies it; WebSockets are not required for one-way availability updates. |

#### Map-provider cost note (checked 2026-11-05)

- [MapLibre GL JS](https://maplibre.org/maplibre-gl-js/docs/) is an open-source renderer, not a free hosted tile service.
- [OpenFreeMap](https://openfreemap.org/) states that its public tile instance is free, allows commercial use, and has no SLA. This is the closest fit if “free” is the priority, but service availability/capacity is not guaranteed; provide attribution.
- [MapTiler pricing](https://www.maptiler.com/cloud/pricing/) describes the Free plan as suitable for testing, prototypes, personal, or non-commercial use; do not assume it is valid for this commercial locker service.
- The [OpenStreetMap standard tile policy](https://operations.osmfoundation.org/policies/tiles/) says its data is free but its donated tile servers are capacity-limited, best-effort, and may block inappropriate use. Do not treat `tile.openstreetmap.org` as a production tile-hosting plan.

Provider plans and terms may change; recheck them before launch. OpenFreeMap's no-SLA public instance should be load-tested cautiously, not stress-tested without permission.

#### Traffic and privacy safeguards

- Keep the geolocation watch in browser memory only. Do not upload a location history or run it in the background. Stop watching when the map is closed/hidden or the traveler switches it off.
- Do not log full latitude/longitude, raw geolocation payloads, or precise coordinates in analytics. Scrub query parameters from request logs or log only a coarse area identifier if operationally necessary and reviewed.
- Start the browser geolocation watch when the map opens if permission is already granted; otherwise let the browser show its permission prompt. Send movement-triggered nearby queries after at least 50 m of movement, no more often than every 15 seconds; availability snapshots may also refresh every 20 seconds while visible. Debounce map panning and stop location and availability polling when the map is not visible.
- Use the composite coordinate index and bounded query before adding more database capacity. The current API disables response caching; never cache booking authorization or the actual locker reservation decision.
- Keep tile traffic on the selected tile provider/CDN rather than proxying every tile through FastAPI. Restrict any provider API key to the allowed origins and products.
- Route requests send the traveler's precise current position and the selected locker coordinates in the URL to the public OSRM demo service. Disclose this before routing; do not persist a route history. A production routing provider must be reviewed for privacy, retention, terms, and service guarantees.
- Add a load test representing 100-1,000 active map users, the 20-second refresh pattern, map panning, and simultaneous booking attempts. Measure API p95/p99 latency, database pool saturation, cache hit rate, MQTT ingestion delay, and errors before claiming capacity.

### Traveler experience

1. The traveler opens the nearby map. If location permission was previously granted, `watchPosition()` starts automatically; otherwise the browser requests permission. While the map remains visible, the user's position stays centered and the app refreshes the 1 km nearby search as the traveler moves.
2. If permission is denied or unavailable, the traveler can pan/zoom the map and explicitly search the visible area without sharing device location.
3. Each registered location within 1 km of the latest position (or manual search center) is shown as a marker. Marker styling communicates whether any lockers are free. Selecting a marker opens a detail card with the location name/address, distance from the search position, and free counts by locker size.
4. Selecting a marker opens its details and the coordinate-sharing disclosure. If current location is available, the traveler explicitly starts driving directions. The map draws the route and shows total distance, estimated drive time, and the next driving maneuver. Foreground GPS updates advance the instruction; if the traveler goes off-route, the app may request a throttled reroute. The traveler can stop guidance at any time.
5. Before requesting a route, the app discloses that precise start and destination coordinates are sent to the public OSRM demo router. This prototype provides driving directions only; it is not pedestrian turn-by-turn guidance or an emergency navigation service. If location is unavailable, routing fails, or the traveler declines the disclosure, the locker remains selectable for booking and external map directions can be used instead.
6. Selecting the location continues into the existing size/duration booking flow. Availability shown on the map is informational and may change; the existing booking API performs the authoritative reservation and can return `NO_LOCKER_AVAILABLE`.
7. Search results refresh as the traveler moves the map or uses an explicit refresh action. Locker availability refreshes every 20 seconds only while the map is visible. This is not background tracking: the current system only knows persisted locker status and received device events.

### API contract

- Public, read-only `POST /v1/locations/nearby` accepts `latitude` and `longitude` plus optional `radius_m` (default and maximum 1,000 m). Coordinates go in the JSON body rather than the URL so normal access logs do not record them.
- Return registered locations within the radius, ordered by distance. Include location ID, name, address, coordinates, distance in meters, free locker counts by size, and an API response timestamp.
- Include locations with zero free lockers so the map can show them as unavailable; count only lockers whose current status is `free`.
- The request coordinates are transient inputs: do not store them in the database, user profile, or application logs. Apply request validation, an in-process per-IP rate limit, and `Cache-Control: private, no-store`.
- Use MapLibre GL JS as the renderer and OpenFreeMap as the no-cost pilot tile source. The UI should not send map-provider credentials to the backend or treat map tiles/geocoding as part of the locker API.

### Data and consistency

The existing `locations.lat` and `locations.lng` fields are the map pin coordinates; the existing `lockers.status` field is used for availability counts. A composite `(lat, lng)` index supports bounding-box candidate filtering. Distance is calculated from the requested map center to each candidate in the API. A displayed count is a snapshot, not a reservation or guarantee. Existing transactional locker selection remains the only source of truth when booking. Device heartbeat freshness is not yet incorporated into availability.

### Acceptance examples

- Given a traveler-selected map center and locations inside and outside the chosen radius, only the in-radius locations are returned, sorted nearest first.
- Given a returned location with free and held/offline lockers, the per-size counts include only the free lockers.
- Given a location with no free lockers, it is returned with zero counts and presented as unavailable rather than selectable for an impossible reservation.
- Given a traveler who denies geolocation, map browsing/search can still request locations around the chosen map center.
- Given availability changes after a response, a booking conflict is shown clearly and the traveler can refresh or select another location.
- Given a nearby-location request, no persistent record of the requesting device's precise coordinates is created.

### Follow-ups before production

1. **Map provider and cost:** pilot uses MapLibre GL JS + OpenFreeMap public tiles. Choose a provider with support/SLA or a self-hosting plan before public launch.
2. **Search controls:** the nearby search is capped at 1 km, and the traveler can pan/zoom instead of granting location. Address/city search is not implemented.
3. **Availability freshness:** API counts persisted `free` states; device heartbeat freshness and offline timeout are not implemented. Define device heartbeat cadence and stale/offline handling before calling this "live availability."
4. **Navigation:** prototype route guidance uses the public OSRM demo's driving profile. It is not walking navigation, not an SLA-backed production service, and route/ETA quality depends on upstream data.
5. **Traffic:** 100-1,000 concurrent users is a planning target, not a tested capacity. Run load tests before launch; replace the in-process IP limiter with shared state before running multiple API workers.
6. **Map availability:** OpenFreeMap public tile service has no SLA. Confirm acceptable risk and fallback/paid hosting for production.

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
