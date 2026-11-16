import base64, csv, hashlib, hmac, io, os, re, threading, time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from .models import Base, engine, Session, Contact, Campaign, Attempt, Gate
from .providers import get_provider, synthesize, AUDIO_DIR, PUBLIC_URL, WEBHOOK_SECRET

SECRET = os.getenv("SECRET", "dev-secret"); ADMIN = (os.getenv("ADMIN_USER", "admin"), os.getenv("ADMIN_PASS", "admin"))
DIAL_INTERVAL = float(os.getenv("DIAL_INTERVAL", "1"))   # seconds between calls = simple rate limit
RETRY_MIN = float(os.getenv("RETRY_MIN", "15"))          # backoff: attempt n waits n * RETRY_MIN minutes
DAILY_CAP = 3; GATE = ["pe", "hdr", "tpl", "num"]; IST = ZoneInfo("Asia/Kolkata")
provider = get_provider()
utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
in_window = lambda: 9 <= datetime.now(IST).hour < 21

# ---------- auth (signed token; swap for a users table + hashed passwords for production) ----------
def sign(body): return hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
def auth(authorization: str = Header("")) -> int:
    try:
        org, exp, sig = base64.urlsafe_b64decode(authorization.removeprefix("Bearer ").encode()).decode().split(":")
        if hmac.compare_digest(sig, sign(f"{org}:{exp}")) and int(exp) > time.time(): return int(org)
    except Exception: pass
    raise HTTPException(401, "Invalid or expired token. Log in again.")
def db():
    with Session() as s: yield s
def own(s, model, id, org):
    o = s.get(model, id)
    if not o or o.org_id != org: raise HTTPException(404, "Not found")
    return o
row = lambda o: {c.name: getattr(o, c.name) for c in o.__table__.columns}

# ---------- compliance + dialer ----------
def check(s, ct, camp):
    if not ct.consent: return "No consent"
    if camp.category == "promotional" and ct.dnd: return "DND"
    n = s.scalar(select(func.count()).select_from(Attempt).where(Attempt.contact_id == ct.id, Attempt.status.notin_(["skipped", "queued"]),
        Attempt.created_at >= utc() - timedelta(hours=24)))
    return f"Daily cap of {DAILY_CAP} reached" if n >= DAILY_CAP else None

def handle_status(call_id, status):  # called by the simulator and by the webhook; idempotent
    with Session() as s:
        a = s.scalar(select(Attempt).where(Attempt.provider_call_id == call_id))
        if not a or a.status != "dialing": return
        a.status = status; c = s.get(Campaign, a.campaign_id)
        if status != "answered" and a.attempt <= c.max_retries:
            s.add(Attempt(org_id=a.org_id, campaign_id=a.campaign_id, contact_id=a.contact_id, attempt=a.attempt + 1,
                          run_at=utc() + timedelta(minutes=RETRY_MIN * a.attempt), created_at=utc()))
        s.commit()

def worker():
    while True:
        time.sleep(DIAL_INTERVAL)
        try:
            with Session() as s:
                for a in s.scalars(select(Attempt).where(Attempt.status == "queued", Attempt.run_at <= utc()).limit(20)):
                    c, ct = s.get(Campaign, a.campaign_id), s.get(Contact, a.contact_id)
                    if c.mode == "pilot" and not in_window(): continue
                    if (why := check(s, ct, c)):  # re-check consent, DND and cap at dial time
                        a.status, a.note = "skipped", why; s.commit(); continue
                    a.status = "dialing"; s.commit()
                    try: a.provider_call_id = provider.place_call(ct.phone, f"{PUBLIC_URL}/audio/{c.audio_id}", str(a.id), handle_status)
                    except Exception as e: a.status, a.note = "failed", str(e)[:200]
                    s.commit(); break  # one call per tick
        except Exception as e: print("worker error:", e)

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine); threading.Thread(target=worker, daemon=True).start(); yield

app = FastAPI(title="IVR Outreach", lifespan=lifespan)

class Login(BaseModel): username: str; password: str
class ContactIn(BaseModel): name: str; phone: str; group_name: str = "Users"; consent: bool = False; consent_purpose: str = "transactional"; dnd: bool = False
class ConsentIn(BaseModel): given: bool; purpose: str = "transactional"
class CampaignIn(BaseModel): name: str; prompt: str; group_name: str; language: str = "English"; category: str = "transactional"; mode: str = "sandbox"; max_retries: int = 1
class GateIn(BaseModel): key: str; done: bool
class ImportIn(BaseModel): csv: str

@app.post("/auth/login")
def login(b: Login):
    if (b.username, b.password) != ADMIN: raise HTTPException(401, "Wrong username or password.")
    exp = str(int(time.time()) + 86400); return {"token": base64.urlsafe_b64encode(f"1:{exp}:{sign(f'1:{exp}')}".encode()).decode()}

def norm(p):
    p = re.sub(r"[\s-]", "", p)
    if not re.fullmatch(r"\+?\d{10,15}", p): raise HTTPException(422, f"Invalid phone number: {p}")
    return p

@app.post("/contacts")
def add_contact(b: ContactIn, org=Depends(auth), s=Depends(db)):
    c = Contact(org_id=org, name=b.name, phone=norm(b.phone), group_name=b.group_name, consent=b.consent,
                consent_at=utc() if b.consent else None, consent_purpose=b.consent_purpose, dnd=b.dnd)
    s.add(c)
    try: s.commit()
    except Exception: raise HTTPException(409, "A contact with this phone number already exists.")
    return row(c)

@app.post("/contacts/import")
def import_contacts(b: ImportIn, org=Depends(auth), s=Depends(db)):
    ok = dup = bad = 0; seen = {p for (p,) in s.execute(select(Contact.phone).where(Contact.org_id == org))}
    for r in csv.DictReader(io.StringIO(b.csv)):
        try: p = norm(r.get("phone", ""))
        except HTTPException: bad += 1; continue
        if p in seen: dup += 1; continue
        seen.add(p); yes = (r.get("consent", "").strip().lower() in ("1", "true", "yes"))
        s.add(Contact(org_id=org, name=r.get("name", p), phone=p, group_name=r.get("group", "Users"), consent=yes,
                      consent_at=utc() if yes else None, consent_purpose=r.get("purpose", "transactional") or "transactional",
                      dnd=r.get("dnd", "").strip().lower() in ("1", "true", "yes"))); ok += 1
    s.commit(); return {"added": ok, "duplicates": dup, "invalid": bad}

@app.get("/contacts")
def contacts(org=Depends(auth), s=Depends(db)): return [row(c) for c in s.scalars(select(Contact).where(Contact.org_id == org).order_by(Contact.id))]

@app.post("/contacts/{id}/consent")
def consent(id: int, b: ConsentIn, org=Depends(auth), s=Depends(db)):
    c = own(s, Contact, id, org); c.consent, c.consent_purpose = b.given, b.purpose
    c.consent_at = utc() if b.given else None; s.commit(); return row(c)

@app.delete("/contacts/{id}")
def del_contact(id: int, org=Depends(auth), s=Depends(db)): s.delete(own(s, Contact, id, org)); s.commit(); return {"deleted": id}

@app.get("/roadmap")
def roadmap(org=Depends(auth), s=Depends(db)):
    d = {g.key: g.done for g in s.scalars(select(Gate).where(Gate.org_id == org))}; return {k: d.get(k, False) for k in GATE}
@app.put("/roadmap")
def set_gate(b: GateIn, org=Depends(auth), s=Depends(db)):
    if b.key not in GATE: raise HTTPException(422, f"Key must be one of {GATE}")
    g = s.scalar(select(Gate).where(Gate.org_id == org, Gate.key == b.key)) or Gate(org_id=org, key=b.key)
    g.done = b.done; s.add(g); s.commit(); return roadmap(org, s)

@app.post("/campaigns")
def add_campaign(b: CampaignIn, org=Depends(auth), s=Depends(db)):
    if b.mode not in ("sandbox", "pilot") or b.category not in ("transactional", "promotional"): raise HTTPException(422, "Invalid mode or category.")
    c = Campaign(org_id=org, **b.model_dump()); s.add(c); s.commit(); return row(c)
@app.get("/campaigns")
def campaigns(org=Depends(auth), s=Depends(db)): return [row(c) for c in s.scalars(select(Campaign).where(Campaign.org_id == org).order_by(Campaign.id.desc()))]

@app.post("/campaigns/{id}/render")
def render(id: int, org=Depends(auth), s=Depends(db)):  # TTS runs once per campaign, never per call
    c = own(s, Campaign, id, org); c.audio_id = hashlib.sha1(f"{c.language}:{c.prompt}".encode()).hexdigest()[:10]
    c.audio_secs = max(3, round(len(c.prompt.split()) / 2.5)); synthesize(c.prompt, c.language, c.audio_id); s.commit(); return row(c)

@app.post("/campaigns/{id}/launch")
def launch(id: int, org=Depends(auth), s=Depends(db)):
    c = own(s, Campaign, id, org)
    if not c.audio_id: raise HTTPException(400, "Render the audio before launching.")
    targets = s.scalars(select(Contact).where(Contact.org_id == org, Contact.group_name == c.group_name)).all()
    if c.mode == "pilot":
        if not all(roadmap(org, s)[k] for k in GATE): raise HTTPException(403, "Pilot is locked. Complete the DLT gate items first.")
        if len(targets) > 100: raise HTTPException(400, "Pilot groups are capped at 100 contacts.")
    done = {x for (x,) in s.execute(select(Attempt.contact_id).where(Attempt.campaign_id == c.id))}; q = sk = 0
    for ct in targets:
        if ct.id in done: continue  # relaunch never double-calls
        why = check(s, ct, c); sk += bool(why); q += not why
        s.add(Attempt(org_id=org, campaign_id=c.id, contact_id=ct.id, status="skipped" if why else "queued",
                      note=why or "", run_at=utc(), created_at=utc()))
    c.status = "running"; s.commit(); return {"queued": q, "skipped": sk}

@app.post("/webhooks/status")  # body: {"call_id": "...", "status": "answered|no-answer|busy|failed"}
async def webhook(req: Request, secret: str = ""):
    if not hmac.compare_digest(secret, WEBHOOK_SECRET): raise HTTPException(403, "Bad secret")
    b = await req.json(); st = {"completed": "answered"}.get(b["status"], b["status"])
    handle_status(b["call_id"], st); return {"ok": True}

@app.get("/audio/{audio_id}")
def audio(audio_id: str):
    for p in AUDIO_DIR.glob(f"{re.sub(r'[^a-f0-9]', '', audio_id)}.*"): return FileResponse(p)
    raise HTTPException(404, "No audio")

@app.get("/stats")
def stats(org=Depends(auth), s=Depends(db)):
    by = dict(s.execute(select(Attempt.status, func.count()).where(Attempt.org_id == org).group_by(Attempt.status)).all())
    placed = sum(v for k, v in by.items() if k not in ("queued", "skipped", "dialing"))
    return {"by_status": by, "answer_rate": round(by.get("answered", 0) / placed * 100) if placed else 0, "provider": provider.name}
@app.get("/calls")
def calls(org=Depends(auth), s=Depends(db)):
    q = select(Attempt, Contact.name, Campaign.name).join(Contact, Contact.id == Attempt.contact_id).join(Campaign, Campaign.id == Attempt.campaign_id)
    return [{**row(a), "contact": c, "campaign": k} for a, c, k in s.execute(q.where(Attempt.org_id == org).order_by(Attempt.id.desc()).limit(100))]

@app.get("/")
def home(): return FileResponse(Path(__file__).parent / "static" / "index.html")
