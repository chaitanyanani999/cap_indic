# IVR Outreach (Phase 1)

FastAPI backend + background dialer + small admin UI. Runs with a built-in simulator, so it needs no telecom account.

    pip install -r requirements.txt
    export SECRET=$(openssl rand -hex 16) ADMIN_PASS=choose-a-password WEBHOOK_SECRET=choose-another
    uvicorn app.main:app --reload      # open http://localhost:8000  (API docs at /docs)

Env: `DB_URL` (use Postgres in production), `PROVIDER=simulator|exotel`, `PUBLIC_URL` (HTTPS URL your provider can reach),
`DIAL_INTERVAL` (seconds between calls), `RETRY_MIN` (retry backoff, minutes), `EXOTEL_*` (see providers.py).

## Roadmap mapping
- Week 0: tick the DLT gate items in the UI as approvals arrive; compare providers offline.
- Weeks 0-1: run with the simulator, then set `PROVIDER=exotel` against your sandbox with your own test numbers.
- Weeks 1-3: replace `synthesize()` in providers.py with a real TTS call; tune retries and caps.
- After approvals: tick all four gate items, create a `pilot` campaign for a group of up to 100 consented contacts.

## Before real use
Replace the single admin login with a users table and hashed passwords, move to Postgres, finish and test the provider adapter,
enforce real DND (NCPR) scrubbing for promotional calls, and get the call classification reviewed. Phase 2 (recordings, STT, LLM analysis) is not included.
# cap_indic
