up:        ; cp -n .env.example .env; docker compose up --build -d
migrate:   ; docker compose exec api alembic revision --autogenerate -m "init" && docker compose exec api alembic upgrade head
verify:    ; docker compose exec -e SPECS_DIR=/specs api pytest -q   # contract + traceability + unit tests
logs:      ; docker compose logs -f api
