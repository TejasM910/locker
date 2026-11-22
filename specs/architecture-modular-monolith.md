# Modular Monolith Architecture & SOLID Design

## 1. Overview & Architectural Goal

The Locker Backend transitions from an unstructured layered directory (`routers/`, flat utility scripts) into a **Modular Monolith using Clean Architecture (Hexagonal / Ports & Adapters)**.

This delivers:
1. **Full SOLID Principle Compliance**:
   - **Single Responsibility (SRP)**: Handlers, domain rules, storage repositories, and external gateways are completely separated.
   - **Open/Closed (OCP)**: New notification/SMS channels or payment providers can be introduced without altering domain logic.
   - **Liskov Substitution (LSP)**: All port implementations (e.g., `MockSmsGateway` vs `TwilioSmsGateway`) satisfy interchangeable contracts.
   - **Interface Segregation (ISP)**: Clients depend only on narrow interfaces (e.g., separate read queries from state mutation commands).
   - **Dependency Inversion (DIP)**: Core domain logic and business use cases depend exclusively on abstract interfaces (Ports), never on concrete libraries (SQLAlchemy, Razorpay SDK, Paho MQTT).
2. **Microservice-Readiness**:
   - Each module owns its domain models, contracts, and business logic.
   - When scale dictates, any module can be carved into an isolated container/service without rewriting internal logic.
3. **Preservation of ACID Transactions & Simplicity**:
   - Unlike distributed microservices, row-level locking (`SELECT ... FOR UPDATE SKIP LOCKED`) continues to run with ACID guarantees without the operational risk of two-phase commits.

---

## 2. Target Directory & Module Structure

```text
backend/app/
├── modules/
│   ├── auth/                        # Identity & Authentication Bounded Context
│   │   ├── domain/                  # Entities, value objects, token/OTP policies
│   │   │   └── models.py
│   │   ├── ports/                   # Abstract contracts (Interfaces)
│   │   │   ├── user_repository.py
│   │   │   ├── otp_service.py
│   │   │   └── sms_gateway.py
│   │   ├── adapters/                # Infrastructure implementations
│   │   │   ├── mysql_user_repository.py
│   │   │   ├── memory_otp_service.py
│   │   │   └── sms_gateway.py       # Supports Mock, Twilio, etc.
│   │   └── api/                     # FastAPI HTTP Routers & Schemas
│   │       ├── router.py
│   │       └── schemas.py
│   │
│   ├── locations/                   # Locker Discovery & Bounding Box Context
│   │   ├── domain/                  # Haversine distance, bounding box calculations
│   │   │   └── geo.py
│   │   ├── ports/                   # Location & Inventory query contracts
│   │   │   └── location_repository.py
│   │   ├── adapters/                # Database queries
│   │   │   └── mysql_location_repository.py
│   │   └── api/
│   │       ├── router.py
│   │       └── schemas.py
│   │
│   ├── bookings/                    # Reservation & Lifecycle State Machine Context
│   │   ├── domain/                  # Booking state machine, expiry calculation
│   │   │   ├── models.py
│   │   │   └── state_machine.py
│   │   ├── ports/                   # Atomic hold & booking repository contracts
│   │   │   └── booking_repository.py
│   │   ├── adapters/                # SQLAlchemy SKIP LOCKED & Expiry workers
│   │   │   ├── mysql_booking_repository.py
│   │   │   └── expiry_worker.py
│   │   └── api/
│   │       ├── router.py
│   │       └── schemas.py
│   │
│   ├── payments/                    # Payment Gateway & Webhook Verification Context
│   │   ├── domain/                  # Amount calculations (paise), webhook signatures
│   │   ├── ports/                   # Payment gateway contracts
│   │   │   └── payment_gateway.py
│   │   ├── adapters/                # Razorpay client & webhook verifier
│   │   │   └── razorpay_adapter.py
│   │   └── api/
│   │       ├── router.py
│   │       └── schemas.py
│   │
│   └── device_gateway/              # IoT & Access Control Context
│       ├── domain/                  # Device event validation, unlock audit
│       ├── ports/                   # MQTT Publisher & Event Handler contracts
│       │   └── mqtt_publisher.py
│       ├── adapters/                # Mosquitto / Paho client
│       │   └── paho_mqtt_adapter.py
│       └── api/
│           └── event_listener.py
│
├── core/                            # Shared Kernel & Infrastructure
│   ├── config.py                    # Environment settings
│   ├── db.py                        # Database session & engine
│   ├── models.py                    # Shared DB ORM mappings (declarative base)
│   └── rate_limiter.py              # Centralized token bucket / rate limiter
│
└── main.py                          # FastAPI Application Assembler (wires modules)
```

---

## 3. SOLID Mapping & Design Patterns

### A. Single Responsibility (SRP)
- **Problem Before:** `routers/bookings.py` mixed HTTP validation, SQL locking queries, Razorpay order generation, and MQTT publish calls in a single function.
- **Solution:** 
  - `BookingApiRouter` validates HTTP requests.
  - `BookingService` enforces booking business rules.
  - `BookingRepository` executes SQL row-locking.
  - `MqttPublisher` dispatches hardware actuation.

### B. Open / Closed (OCP) & Liskov Substitution (LSP)
- **Problem Before:** `auth.py` hardcoded dev OTP generation with no path to real SMS without modifying router handlers.
- **Solution:**
  - Define `SmsGatewayPort` (abstract base class).
  - Provide `MockSmsGateway` (logs to console/debug in dev) and `TwilioSmsGateway` (sends real carrier SMS).
  - Can swap or extend without touching any route or controller code.

### C. Dependency Inversion (DIP)
- High-level business use cases depend on interfaces (`typing.Protocol` or `abc.ABC`).
- FastAPI's `Depends()` injects concrete adapters at runtime.

---

## 4. Implementation & Backward Compatibility Plan

To ensure zero regressions:
1. All existing routes (`/v1/auth/*`, `/v1/locations/*`, `/v1/bookings/*`, `/v1/webhooks/*`) maintain identical HTTP contracts as defined in `specs/openapi.yaml`.
2. Existing 135 unit and integration tests must pass continuously throughout refactoring.
3. The frontend interface remains 100% compatible.
