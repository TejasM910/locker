# Smart Locker Booking

A spec-driven locker booking prototype. Travelers use a mobile-friendly web app to verify a phone number, reserve a locker, pay, check booking status, and request locker access. The backend manages bookings and payments and exchanges commands/events with lockers over MQTT.

> **Prototype status:** The local booking and Razorpay test-payment flows have been exercised. Inbound MQTT events have been tested with a manual simulator. Physical hardware actuation, production authentication/SMS, and production deployment are not verified. Do not use this prototype to secure real property or process live payments.

## Project flow

1. **Sign in:** The PWA requests and verifies an OTP. In development, the API returns the OTP as `debug_code`; there is no SMS delivery.
2. **Choose a location:** The traveler scans a location QR code or enters a location ID. The QR code identifies an existing location; location discovery and inventory administration are not yet implemented.
3. **Reserve a locker:** The API checks for a free locker matching the requested size, marks it held, creates a `CREATED` booking, and creates a Razorpay order. An unpaid hold expires after 15 minutes.
4. **Pay:** The PWA opens Razorpay Checkout. A valid `payment.captured` webhook moves the booking to `PAID`. The backend verifies the webhook signature and handles duplicate event delivery.
5. **Use the locker:** The traveler can request unlock through the API. When MQTT is enabled, the API publishes a command to `lockers/{device_id}/cmd`; otherwise it logs the would-be command. A device or simulator reports events on `lockers/{device_id}/evt`.
6. **Track and release:** A `door_closed` event moves a paid booking to `STORED`. A `pickup_completed` event moves it to `RELEASED` and frees the locker. Booking changes and device events are persisted for audit.

### Booking states

`CREATED -> PAID -> STORED -> RELEASED`

An unpaid `CREATED` booking can expire and free its locker. `OVERSTAY`, refunds, dispute workflows, and offline PIN access are specified future work, not completed functionality. See [the state machine](./specs/booking-state-machine.md) for all defined states and transitions.

## Architecture

| Component | Role |
|---|---|
| `frontend/` | Next.js PWA for OTP sign-in, QR/manual location selection, booking, checkout, status, and unlock requests |
| `backend/` | FastAPI API, booking/payment logic, database access, expiry worker, and MQTT listener/publisher |
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

Create `frontend/.env.local` from its template:

```powershell
Copy-Item frontend\.env.example frontend\.env.local
```

Set `NEXT_PUBLIC_RAZORPAY_KEY_ID` to your Razorpay **public** test key ID. Never put Razorpay secrets in frontend variables. Both `.env` and `.env.local` are excluded from Git.

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

Create a MySQL database matching the database name in `.env`, then run the checked-in Alembic migration from the backend folder:

```powershell
Set-Location backend
.\.venv\Scripts\alembic.exe upgrade head
Set-Location ..
```

To create a demo location and two lockers, connect to the database in MySQL Workbench and run:

```sql
INSERT INTO locations (name, address, lat, lng)
VALUES ('Demo Location', 'Local test site', 0, 0);

SET @location_id = LAST_INSERT_ID();

INSERT INTO lockers (location_id, size, device_id, status)
VALUES
  (@location_id, 'M', 'demo-locker-001', 'free'),
  (@location_id, 'L', 'demo-locker-002', 'free');

SELECT @location_id AS demo_location_id;
```

Save the returned location ID for the app. Existing databases that were created manually should be backed up and compared with the migration before applying it; do not run the initial migration against tables that already exist.

### 4. Start the local services

Start MySQL first. Configure the root `.env` and `frontend/.env.local`, then run this once from the project root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

The launcher reuses listeners already running on the expected ports and opens a separate PowerShell window for each service it starts. Keep those windows open. It starts or checks MySQL, Mosquitto, the API, the frontend, and ngrok. It does not install dependencies or create the database. Restart the API or frontend manually after changing that service's environment file.

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
- Locations and locker inventory must be inserted into MySQL manually; there is no operator setup screen or location directory.
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
