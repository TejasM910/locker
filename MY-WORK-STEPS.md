# Project Owner Checklist

Use the [README](./README.md) as the single setup guide and overview for this project. It documents the end-to-end flow, local Windows setup, environment files, database migration, demo flow, tests, current limitations, and GitHub publishing checklist.

## Before a real pilot

- [ ] Replace the development OTP response with an SMS provider.
- [ ] Move OTPs, bearer tokens, and rate-limit counters to secure, durable storage.
- [ ] Remove or restrict `X-User-Id` development authentication.
- [ ] Add persistent location/inventory management and an operator setup process.
- [ ] Build/test locker firmware or a simulator against [the MQTT contract](./specs/mqtt-contract.md).
- [ ] Verify outbound unlock command receipt and physical locker operation.
- [ ] Test disconnects, duplicate device messages, and locker release behavior.
- [ ] Complete the remaining features listed in [the project tasks](./specs/tasks.md).

Keep all database passwords, Razorpay secrets, MQTT credentials, and tokens in local ignored environment files; never put them in source control or chat.
