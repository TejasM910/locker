# MQTT Contract (cloud <-> locker)

Broker auth: one username per device, TLS on. Topics are scoped by `device_id`; a device may only access its own.

## Local development status
A local Mosquitto broker and CLI publisher were used to simulate locker events. The user confirmed the booking reached `RELEASED`, verifying inbound event processing and locker release for the manual simulation. Outbound unlock command receipt by a subscriber/firmware and physical locker actuation remain unverified. If `MQTT_HOST` is empty, the API only logs the would-be command and does not publish it.

## Cloud -> locker: `lockers/{device_id}/cmd` (QoS 1)
```json
{ "cmd_id": "uuid", "cmd": "unlock", "booking": "public_id", "issued_at": "2026-01-01T10:00:00Z", "ttl_s": 30 }
```
Locker ignores commands older than `ttl_s` and de-duplicates by `cmd_id`.

## Locker -> cloud: `lockers/{device_id}/evt` (QoS 1)
```json
{ "evt": "door_closed", "ts": "2026-01-01T10:00:20Z", "cmd_id": "uuid|null" }
```
`evt` values: `door_opened, door_closed, pickup_completed, tamper, heartbeat`
Publish valid JSON with double-quoted property names. The cloud listener ignores malformed JSON or non-object payloads and continues listening.

## Status: `lockers/{device_id}/status` (retained, LWT = offline)
```json
{ "online": true, "fw": "0.1.0" }
```
