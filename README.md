# Smart Locker Booking

A spec-driven locker booking prototype. Travelers use a mobile-friendly web app to verify a phone number, reserve a locker, pay, check booking status, and request locker access. The backend manages bookings and payments and exchanges commands/events with lockers over MQTT.

> **Prototype status:** The local booking and Razorpay test-payment flows have been exercised. Inbound MQTT events have been tested with a manual simulator. Physical hardware actuation, production authentication/SMS, and production deployment are not verified. Do not use this prototype to secure real property or process live payments.

## Project flow

1. **Sign in:** An unauthenticated visitor sees the phone/OTP sign-in screen before any map or locker controls. In development, the API returns the OTP as `debug_code`; there is no SMS delivery. A valid restored session opens the map directly.
2. **Find and navigate:** The traveler browses nearby locker markers and explicitly starts the previously disclosed OSRM driving route to a selected location. Foreground GPS updates show route progress; arrival is detected within 50 m, with a manual “I’m here” fallback.
3. **Choose a locker:** After arrival confirmation, the PWA fetches individual free lockers for the location, showing stable public codes (for example `CH101`) and sizes. The traveler selects a specific code and duration; the API reserves exactly that locker or rejects a stale choice, then returns the traveler to the map. An **Active locker** button appears in the signed-in header; selecting it opens the locker details, payment action, and a live countdown to the unpaid hold expiry (15 minutes). Its back button returns to the map.
4. **Pay:** The PWA opens Razorpay only after the traveler presses the checkout button. A valid `payment.captured` webhook moves the booking to `PAID` and starts its booked-duration countdown. The backend verifies the webhook signature and handles duplicate event delivery.
5. **Unlock:** After payment is verified, the traveler explicitly presses **Unlock locker**. When MQTT is enabled, the API publishes a command to `lockers/{device_id}/cmd`; otherwise it logs the would-be command. A device or simulator reports events on `lockers/{device_id}/evt`.
6. **Track and release:** A `door_closed` event moves a paid booking to `STORED`. A `pickup_completed` event moves it to `RELEASED` and frees the locker. Booking changes and device events are persisted for audit.

### Booking states

`CREATED -> PAID -> STORED -> RELEASED`

An unpaid `CREATED` booking can expire and free its locker. `OVERSTAY`, refunds, dispute workflows, and offline PIN access are specified future work, not completed functionality. See [the state machine](./specs/booking-state-machine.md) for all defined states and transitions.

## Architecture

| Component | Role |
|---|---|
| `frontend/` | Next.js PWA with an OTP-first entry screen, authenticated MapLibre map, foreground geolocation, arrival confirmation, coded-locker selection, checkout, status, and unlock requests |
| `backend/` | FastAPI API including nearby search, per-location coded locker inventory, exact-locker reservation, payment logic, expiry worker, and MQTT listener/publisher |
| MySQL 8 | Users, locations, lockers, bookings, payments, and locker-event audit data |
| Mosquitto | Local MQTT broker for locker commands and device events |
| Razorpay | Checkout/order creation and signed payment webhooks |
| ngrok | Optional HTTPS tunnel from Razorpay webhooks to the local API; MQTT is never exposed through this tunnel |
| `specs/` | Requirements, design, OpenAPI contract, state machine, MQTT contract, and task status |

## Requirements

For the supported local Windows workflow:

- Windows PowerShell
- Python 3.11 or newer
- Node.js 20.9 or newer and npm
- MySQL 8 server
- Mosquitto 2 installed at `C:\Program Files\Mosquitto\mosquitto.exe` (or update the path in `start-local.ps1`)
- ngrok installed and authenticated if testing Razorpay webhooks
- Razorpay test-mode account if testing checkout and webhooks

Docker Compose is retained as an optional development alternative; the primary documented workflow does not require Docker.
If you use Docker Compose, also set `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `DOCKER_DATABASE_URL`, and `DOCKER_SYNC_DATABASE_URL` in the ignored root `.env` before running Compose. Use URL-encoded credentials in the two Docker connection URLs; the Compose configuration intentionally has no default database passwords.

## Local setup (Windows, no Docker)

### 1. Configure environment files

The backend reads settings from the project-root `.env`. Create it from the safe template, then add your own local values:

```powershell
Copy-Item .env.example .env
```

Set `DATABASE_URL` and `SYNC_DATABASE_URL` to your local MySQL database. The async URL uses the `mysql+asyncmy` driver; the sync URL uses `mysql+pymysql`. URL-encode special characters in the database password (for example, `@` becomes `%40`). Keep real credentials only in `.env`.

Set `NEXT_PUBLIC_RAZORPAY_KEY_ID` in the same root `.env` to your Razorpay **public** test key ID. The local launcher passes this public key to the frontend; do not put Razorpay secrets in frontend variables. The root `.env` is excluded from Git.

### 2. Install dependencies

From the project root:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Set-Location frontend
npm ci
Set-Location ..
```

If PowerShell blocks activation scripts, the commands above use the virtual environment's Python directly and do not require activation.

### 3. Create the database schema and demo inventory

Create a MySQL database matching the database name in `.env`, then run the checked-in Alembic migrations from the backend folder:

```powershell
Set-Location backend
.\.venv\Scripts\alembic.exe upgrade head
Set-Location ..
```

The migrations add a `(lat, lng)` index used by nearby searches and assign each existing locker a stable location-derived public code (for example `CH101`), separate from its MQTT device ID. If your local database was created manually or does not have Alembic revision history, back it up and reconcile the existing schema with the checked-in migrations before running `upgrade head`; do not run the initial schema migration against tables that already exist.

For the original manually-created local schema only, after backing up and verifying that all tables match `20261104_0001_initial_schema.py`, mark that existing schema as the baseline, then apply subsequent migrations:

```powershell
Set-Location backend
python -m alembic stamp 20261104_0001
python -m alembic upgrade head
python -m alembic current
Set-Location ..
```

Do not stamp a database with missing or different tables/columns; reconcile it first. If the database is new, use `upgrade head` without stamping.

To create three clearly labeled Bengaluru demo locations, each with two medium lockers and one large locker, run this from the repository root:

```powershell
Set-Location backend
python .\seed_demo_inventory.py
Set-Location ..
```

The seeder is restricted to a local MySQL server, is safe to rerun, prints each location ID and its free inventory, and does not reset or modify existing locker statuses. The locations are fictional test points near central Bengaluru (about 0-400 m apart); the map opens there when device location is unavailable. Use this only in a local development database.

### 4. Test the map-to-reservation flow

1. Start the local services and open [http://localhost:3000](http://localhost:3000). If using a phone, use your HTTPS ngrok frontend URL; browser geolocation does not work on a non-local HTTP URL.
2. Grant location permission if the browser asks. If your device is not near Bengaluru or you deny permission, the map opens at the Bengaluru demo area. Pan/zoom if needed and press **Search this area**.
3. Select a **DEMO ONLY** marker and review its free medium/large count. To try prototype driving guidance, press **Start driving directions** after reading the coordinate-sharing disclosure; the route and next-turn steps appear in the map details. Then press **Choose this location** to continue. Directions require foreground device location and use the public OSRM demo service; booking remains available if routing fails.
4. Sign in with a development test phone number; the local API returns the OTP on screen. Select a size with free inventory and press **Find & reserve a locker**. The app should show a `CREATED` booking and hold one demo locker.
5. To continue through payment, configure Razorpay test credentials and complete checkout in Test Mode. A reservation alone does not require payment; its hold expires after the configured 15 minutes.

These are simulated locations and do not represent physical lockers. Do not expect real hardware unlock behavior from the demo records.

Selecting a nearby marker can also request in-app **driving** directions from the current position through the public OSRM demo service. The route panel discloses that precise start and destination coordinates are sent to that third-party service. The public demo is best-effort, driving-only, and not a production or pedestrian navigation provider. Booking remains available if routing fails or the user does not start guidance.

### 5. Start the local services

Configure the root `.env`, then double-click `run-locker.bat` in the project folder. Alternatively, start it from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

The launcher reuses listeners already running on the expected ports and opens a separate PowerShell window for each service it starts. Keep those windows open. It starts or checks MySQL, Mosquitto, the API, the frontend, and ngrok. It does not install dependencies or create the database; complete the one-time setup steps above first. Restart the API and frontend after changing `.env`.

- PWA: [http://localhost:3000](http://localhost:3000)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health check: [http://localhost:8000/health](http://localhost:8000/health)

The launcher requires non-empty Razorpay settings even if you are only testing the non-payment flow. For Razorpay Test Mode, configure the dashboard webhook URL using the HTTPS URL printed by ngrok:

```text
https://YOUR-NGROK-HOST/v1/webhooks/razorpay
```

Subscribe to `payment.captured` and use the same webhook secret in the Razorpay dashboard and the backend `.env`. ngrok should forward only API port `8000`; do not expose MySQL or MQTT through ngrok.

## Tests and checks

Run backend tests from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run frontend checks from `frontend/`:

```powershell
npm run typecheck
npm run build
```

The backend tests include API-contract, requirement-traceability, booking-state, payment, authentication, and expiry-worker checks. The contract is maintained in [specs/openapi.yaml](./specs/openapi.yaml).

## Current limitations and remaining work

- OTP is returned in the API response for development; SMS delivery is not implemented.
- OTPs, bearer tokens, and rate-limit counters are in memory and are cleared when the API restarts.
- `X-User-Id` is accepted as a development authentication fallback; production authentication hardening is required.
- Nearby map availability uses current MySQL locker status, not a verified device-heartbeat freshness signal. Counts are snapshots and can change before booking; the booking endpoint remains authoritative.
- The nearby map requests browser location permission when opened, follows the traveler only while the map is visible, and searches within 1 km. If permission is denied, manual map search remains available. A visible locker demo requires location and locker inventory records in MySQL.
- Demo directions use the public OSRM demo router with driving directions; exact origin/destination coordinates are sent to that third party when directions are requested. Replace this best-effort service and review its privacy/retention terms before production. It does not provide walking turn-by-turn guidance.
- Nearby-search rate limiting is in-memory per API process and is not shared across multiple workers. Configure a shared limiter before horizontal scaling.
- Locations and locker inventory must be inserted into MySQL manually; there is no operator setup screen.
- OpenFreeMap public tiles have no SLA. They are used as a no-cost pilot source; arrange a supported provider or self-hosting plan before production.
- Manual inbound MQTT event simulation has been exercised, but outbound command receipt by firmware and physical locker operation have not been verified.
- The local Mosquitto configuration is for development only and must not be exposed publicly.
- Overstay notifications, unused-booking refunds, tamper/camera workflows, an operator dashboard, and offline PIN access are not implemented.

See [specs/tasks.md](./specs/tasks.md) for the tracked completion status and remaining work, and [specs/README.md](./specs/README.md) for the spec-driven contribution workflow.

## GitHub publishing checklist

- Keep `.env`, `frontend/.env.local`, API keys, webhook secrets, passwords, and tokens out of commits.
- Commit `.env.example` and `frontend/.env.example` only; they contain placeholders, not real credentials.
- Do not commit `node_modules/`, `.next/`, Python virtual environments, caches, or compiled files; the root `.gitignore` excludes these.
- Review the files shown by `git status` before staging or pushing.
- Create a GitHub repository, add its remote URL, and push only after confirming the staged changes contain no credentials or personal data.

The local Git repository is initialized on the `main` branch, but no remote or commit has been created. After creating an empty GitHub repository, review and push the project with:

```powershell
git status --short
git add .
git status --short
git commit -m "Initial locker booking project"
git remote add origin https://github.com/<OWNER>/<REPOSITORY>.git
git push -u origin main
```

Replace the remote URL with your repository's URL. Confirm no local credential or personal files appear in the staged status before committing; do not share credentials or access tokens in chat.

## Spec-driven development

For each feature, update the requirement, design and relevant contract first; add a linked task and test; then implement and verify. Start with [specs/README.md](./specs/README.md).
