# API (FastAPI)

Verified against `apps/api/main.py` on the current `main` layout. The React app calls these routes through the Vite `/api` proxy.

Base URL when running locally: `http://127.0.0.1:8000`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness check (`{"ok": true}`) |
| `GET` | `/api/state` | Full UI state for an optional `as_of` date |
| `GET` | `/api/emails` | Names of mock supplier email files under `data/synthetic/mock/emails/` |
| `POST` | `/api/plans/{card_id}/approve` | Approve a plan option (role + stock re-check enforced in code) |
| `POST` | `/api/plans/{card_id}/reject` | Reject a plan with a note |
| `POST` | `/api/agent/run` | Run the signal pipeline on one mock email and persist the result |
| `POST` | `/api/day/run` | Run one simulated day (ingest, inbox, stock watch, signals, follow-up) |
| `POST` | `/api/drugs/{din}/plan` | Supply manager requests a plan for one drug (Stock page) |
| `POST` | `/api/orders/{order_id}/status` | Person records order status (placed, received, …). Nothing is sent |
| `POST` | `/api/orders/{order_id}/release` | Procurement releases a held order into the orchestrator (simulated unless configured otherwise) |
| `POST` | `/api/escalations/{object_id}` | Escalate an order or transfer; code chooses the receiving role |
| `POST` | `/api/notifications/{notification_id}/read` | Mark a role notification as read |
| `POST` | `/api/followup/run` | Run transfer follow-up and order follow-up checks |

## Notes

- Approvals and related actions may resume the LangGraph alert pipeline when graph orchestration is enabled.
- Order placement defaults to simulation when `ORDER_SUBMISSION` is unset or `simulate` (code default). No live supplier order is submitted in that mode; Health Canada and Bedrock calls are separate. The env var is optional.
- Database connectivity errors return HTTP 503 with guidance to start Postgres or run migrations.
- For agent design detail, see [services/agents/README.md](../services/agents/README.md). For setup, see the root [README.md](../README.md).
