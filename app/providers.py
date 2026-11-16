import os, random, threading
from pathlib import Path
import httpx

AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "audio")); AUDIO_DIR.mkdir(exist_ok=True)
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")

def synthesize(text: str, language: str, audio_id: str) -> str:
    """TTS hook. Replace with Google/Azure/ElevenLabs and save an mp3/wav under AUDIO_DIR.
    The stub stores the text so the rest of the pipeline works end to end."""
    p = AUDIO_DIR / f"{audio_id}.txt"; p.write_text(text, encoding="utf-8"); return str(p)

class Simulator:
    """Needs no account. Reports a random outcome through the same status handler a webhook would use."""
    name = "simulator"
    def place_call(self, to, audio_url, ref, on_status):
        cid = f"sim-{ref}-{random.randint(1000, 9999)}"
        r = random.random()
        st = "answered" if r < .6 else "no-answer" if r < .75 else "busy" if r < .9 else "failed"
        threading.Timer(random.uniform(1, 2.5), on_status, args=(cid, st)).start()
        return cid

class Exotel:
    """UNTESTED skeleton. Verify endpoint and field names against Exotel's current docs and your flow setup.
    Exotel's flow must play `audio_url`; its status callback must reach POST /webhooks/status."""
    name = "exotel"
    def __init__(self):
        e = os.environ
        self.sid, self.key, self.token = e["EXOTEL_SID"], e["EXOTEL_KEY"], e["EXOTEL_TOKEN"]
        self.sub = e.get("EXOTEL_SUBDOMAIN", "api.in.exotel.com")
        self.caller, self.flow = e["EXOTEL_CALLER_ID"], e["EXOTEL_FLOW_URL"]
    def place_call(self, to, audio_url, ref, on_status):
        r = httpx.post(f"https://{self.sub}/v1/Accounts/{self.sid}/Calls/connect.json", auth=(self.key, self.token),
            data={"From": to, "CallerId": self.caller, "Url": self.flow, "CustomField": ref,
                  "StatusCallback": f"{PUBLIC_URL}/webhooks/status?secret={WEBHOOK_SECRET}"}, timeout=15)
        r.raise_for_status()
        return r.json()["Call"]["Sid"]

def get_provider():
    return Exotel() if os.getenv("PROVIDER", "simulator") == "exotel" else Simulator()
