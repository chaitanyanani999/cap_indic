# IVR Outreach

An admin web app and API for sending automated voice (IVR) calls that read out an organisation-configured prompt to groups of contacts (users, friends, MLAs, MPs and so on). Phase 1 covers outbound prompt delivery with consent, retries and a dashboard. Phase 2 (recording replies, speech-to-text, LLM analysis and validation) is planned and not included.

> Status: working prototype. It runs end to end on a built-in call simulator. The real telephony adapter is an untested skeleton. Read [Before production](#before-production) first.

## Features

- Contacts in groups, with a consent record (time, channel, purpose), a DND flag and CSV import
- Campaigns with a prompt, language, category (transactional or promotional), mode (sandbox or pilot) and retry limit
- Text-to-speech rendered once per campaign, then reused for every call
- Background dialer with rate limiting, retry backoff and no double-calling
- Compliance rules enforced on the server: consent, DND for promotional calls, 3 calls per contact per 24 hours, and a 9am to 9pm IST window for pilot campaigns
- A pilot lock that stays closed until the DLT approval items are ticked, with a cap of 100 contacts
- Admin page with login, DLT checklist, dashboard and call log; interactive API docs at `/docs`

## How it fits together

```
Browser (admin page)  -->  FastAPI  -->  Database (SQLite, or Postgres in production)
                              |
                       Dialer thread  -->  Provider adapter  -->  Telephony provider  -->  Contact's phone
                              ^                                          |
                              +--------  POST /webhooks/status  <--------+
```

```
app/main.py        API, auth, compliance checks, dialer, webhook
app/models.py      Database tables (contacts, campaigns, attempts, gates)
app/providers.py   Simulator, Exotel skeleton, text-to-speech hook
app/static/        Admin page
Dockerfile         Container image
.github/workflows  CI checks on every push
```

## Run it locally

Requires Python 3.12 or newer.

```bash
git clone <your-repo-url> && cd <repo-folder>
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then edit the secrets
set -a && source .env && set +a                       # load the variables (Windows: set them manually)
uvicorn app.main:app --reload
```

Open http://localhost:8000 and log in with `ADMIN_USER` and `ADMIN_PASS`. The simulator needs no telecom account.

First test: add a few contacts with consent, create a sandbox campaign for their group, click **Render audio**, then **Launch**. Attempts appear in the call log within seconds.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `SECRET` | Signs login tokens. Must be long and random. | `dev-secret` (change it) |
| `ADMIN_USER`, `ADMIN_PASS` | The single admin login | `admin` / `admin` (change it) |
| `WEBHOOK_SECRET` | Shared secret on the provider callback URL | `change-me` (change it) |
| `DB_URL` | Database. Use `postgresql+psycopg://user:pass@host:5432/dbname` in production. | `sqlite:///ivr.db` |
| `PROVIDER` | `simulator` or `exotel` | `simulator` |
| `PUBLIC_URL` | Public HTTPS address of this app, used for audio and callback URLs | `http://localhost:8000` |
| `DIAL_INTERVAL` | Seconds between calls (simple rate limit) | `1` |
| `RETRY_MIN` | Retry delay in minutes, multiplied by the attempt number | `15` |
| `AUDIO_DIR` | Where rendered audio is stored | `audio` |
| `EXOTEL_*` | Exotel account details, needed only when `PROVIDER=exotel` | none |

## API overview

All routes except login, the webhook and audio need the header `Authorization: Bearer <token>`. Full details are at `/docs`.

| Method and path | What it does |
|---|---|
| `POST /auth/login` | Returns a token |
| `GET/POST /contacts`, `POST /contacts/import`, `POST /contacts/{id}/consent`, `DELETE /contacts/{id}` | Manage contacts and consent |
| `GET/POST /campaigns`, `POST /campaigns/{id}/render`, `POST /campaigns/{id}/launch` | Create, render audio for, and launch campaigns |
| `GET/PUT /roadmap` | Read and set the four DLT gate items (`pe`, `hdr`, `tpl`, `num`) |
| `GET /stats`, `GET /calls` | Dashboard numbers and call log |
| `POST /webhooks/status?secret=...` | Provider call-status callback. JSON body: `{"call_id": "...", "status": "answered"}` |
| `GET /audio/{id}` | Serves rendered audio to the provider |

## Publish the code to GitHub

GitHub stores and builds the code. It does not run this app, because GitHub Pages only serves static files. See [Hosting](#host-the-running-app) for where to run it.

1. **Install and sign in.** Install Git, create a GitHub account, and optionally install the GitHub CLI (`gh`).
2. **Check nothing secret is included.** `.gitignore` already excludes `.env`, `*.db` and `audio/`. Run `git status` after `git add .` and confirm none of them are listed.
3. **Create the repository.** Make it **private**. This app handles phone numbers and consent records, so keep the code and any sample data private until you decide otherwise.
   - With the CLI: `gh repo create ivr-outreach --private --source=. --remote=origin`
   - Or on github.com: New repository, choose Private, leave the README, .gitignore and licence options empty.
4. **Commit and push.**
   ```bash
   git init -b main
   git add .
   git commit -m "Initial commit: IVR outreach phase 1"
   git remote add origin https://github.com/<your-user>/ivr-outreach.git   # skip if gh already added it
   git push -u origin main
   ```
5. **Confirm CI passes.** Open the Actions tab. The `CI` workflow installs dependencies and checks that the app imports.
6. **Protect the main branch.** Settings, Branches, add a rule for `main` that requires a pull request and the `CI` check before merging.
7. **Store secrets in GitHub, not in code.** Settings, Secrets and variables, Actions. Add `SECRET`, `ADMIN_PASS`, `WEBHOOK_SECRET`, and later the provider keys, if a workflow or deployment needs them.
8. **Turn on security features.** Settings, Code security: enable secret scanning, push protection and Dependabot alerts.
9. **If a secret is ever committed,** rotate it immediately. Deleting the file in a later commit does not remove it from history.

## Host the running app

Pick one. All of them need a public HTTPS address, because the telephony provider must fetch audio from you and send status callbacks to you.

**Important:** run exactly **one** instance of the app. The dialer runs inside the web process, so two instances could pick up the same queued call and dial it twice. Do not use `--workers` greater than 1 and do not scale to multiple replicas until the dialer is split into its own service with row locking.

### Option A: Container platform (Render, Railway, Fly.io or similar)

1. Push the repo to GitHub (above).
2. Create a new web service from the repo, using the included `Dockerfile`.
3. Add a managed **PostgreSQL** database and set `DB_URL` to its connection string, changed to start with `postgresql+psycopg://`.
4. Set the environment variables from the table above. Set `PUBLIC_URL` to the service's HTTPS address.
5. Attach a persistent disk for `AUDIO_DIR`, or the audio is lost on every redeploy. (A better long-term fix is object storage such as S3, which needs a code change.)
6. Enable auto-deploy from `main` so each merged pull request redeploys.
7. Open the service URL, log in, and run a sandbox campaign to confirm everything works.

Menu names differ between platforms, so follow each platform's own documentation for the exact clicks.

### Option B: Your own server (VPS)

```bash
git clone https://github.com/<your-user>/ivr-outreach.git && cd ivr-outreach
cp .env.example .env && nano .env
docker build -t ivr-outreach .
docker run -d --name ivr --restart unless-stopped --env-file .env -p 127.0.0.1:8000:8000 \
  -v ivr_audio:/srv/audio ivr-outreach
```

Put a reverse proxy with automatic HTTPS in front of port 8000 (Caddy or Nginx with Let's Encrypt), and point your domain at the server. To update: `git pull`, rebuild the image, and restart the container.

### Option C: GitHub Codespaces (development only)

Open the repo in a Codespace, follow the local steps, and make port 8000 public to test provider callbacks. Do not use this for real campaigns.

## Connect a real telephony provider

1. Sandbox first. Open a trial account with your shortlisted provider and use only your own test numbers.
2. Set `PROVIDER=exotel` and the `EXOTEL_*` variables. The adapter in `app/providers.py` is an **untested skeleton**: check its endpoint and field names against the provider's current documentation, and configure the provider's call flow to play the URL `PUBLIC_URL/audio/<id>`.
3. Set the provider's status callback to `PUBLIC_URL/webhooks/status?secret=<WEBHOOK_SECRET>`, sending JSON with `call_id` and `status`. Adjust the webhook handler if your provider posts form data or uses different status words.
4. Replace the stub `synthesize()` with a real text-to-speech service that writes an audio file the provider can play.

## From sandbox to pilot

1. Register on the DLT platform, get your voice header and call script templates approved, and obtain your 140 or 160 series number. Tick the four gate items in the admin page as each is approved.
2. Import only contacts with recorded voice consent.
3. Create a campaign in **pilot** mode for a group of 100 contacts or fewer, and launch it between 9am and 9pm IST.
4. Review the call log and answer rate before any larger group.

## Before production

- Replace the single admin login with a users table, hashed passwords and roles.
- Test the provider adapter on a sandbox account.
- Add real DND (NCPR) scrubbing. Today DND is just a flag you set.
- Move audio to object storage and split the dialer into its own worker.
- Get the call classification (transactional or promotional) and your use of voice for political contacts reviewed by a telecom or legal adviser.
- Announce recording at the start of the call before you turn on Phase 2.

## Troubleshooting

| Problem | Likely cause |
|---|---|
| "Pilot is locked" | One or more DLT gate items are not ticked |
| Launch returns "Render the audio" | Click **Render audio** on the campaign first |
| Calls stay queued | Pilot campaigns only dial between 9am and 9pm IST |
| Contacts skipped | No consent, DND on a promotional campaign, or the 3-calls-in-24-hours cap |
| Provider cannot fetch audio or send callbacks | `PUBLIC_URL` is not a public HTTPS address, or the webhook secret does not match |
| Data disappears after a redeploy | SQLite or the audio folder is on a temporary disk. Use Postgres and a persistent disk. |
| Login fails after a restart | `SECRET` changed. Tokens signed with the old value are rejected, so log in again. |

## Roadmap

- Phase 2: DTMF and voice-reply capture, speech-to-text (Indic languages), LLM extraction and validation against the organisation's description
- WhatsApp as a fallback channel, with explicit user consent
- Multi-user admin, audit trail, CSV export, scheduled campaigns
