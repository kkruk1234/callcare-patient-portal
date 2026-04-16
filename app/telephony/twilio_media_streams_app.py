from __future__ import annotations

import asyncio, json, base64, audioop, time, os, logging
from fastapi import FastAPI, WebSocket
from app.telephony.callcare_bridge import CallCareBridge
import requests

log = logging.getLogger("callcare")
logging.basicConfig(level=logging.INFO)

app = FastAPI()
bridge = CallCareBridge()

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("CALLCARE_TWILIO_VOICE")

SILENCE = 1.0

class Call:
    def __init__(self, sid):
        self.sid = sid
        self.buffer = b""
        self.last_audio = time.time()
        self.timer = None
        self.awaiting = False
        self.speaking = False

calls = {}

def pcm_energy(ulaw):
    pcm = audioop.ulaw2lin(ulaw,2)
    return audioop.rms(pcm,2)

async def eleven_speak(ws, stream_sid, text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY}
    data = {"text": text}

    r = requests.post(url, json=data, headers=headers, stream=True)

    for chunk in r.iter_content(1024):
        if not chunk: continue
        ulaw = audioop.lin2ulaw(chunk,2)
        await ws.send_text(json.dumps({
            "event":"media",
            "streamSid":stream_sid,
            "media":{"payload":base64.b64encode(ulaw).decode()}
        }))

async def finalize(ws, call, stream_sid):
    text = call.buffer.decode(errors="ignore").strip()
    call.buffer = b""
    call.awaiting = False

    result = bridge.handle_prompt_text(call.sid, text)

    if result.done:
        await eleven_speak(ws, stream_sid,
            "Thank you for your responses. This may take up to a minute to process. Please stay on the line."
        )

        await ws.send_text(json.dumps({
            "event":"play",
            "streamSid":stream_sid,
            "play":{"url":os.getenv("CALLCARE_TWILIO_HOLD_AUDIO_URL")}
        }))

        summary = bridge.complete_session(call.sid)

        await eleven_speak(ws, stream_sid, "Thank you for holding. " + summary)
        await eleven_speak(ws, stream_sid, "Thank you, goodbye.")

        await ws.close()
        return

    await eleven_speak(ws, stream_sid, result.say)
    call.awaiting = True

async def silence_watch(ws, call, stream_sid):
    await asyncio.sleep(SILENCE)
    if time.time() - call.last_audio >= SILENCE:
        await finalize(ws, call, stream_sid)

@app.websocket("/twilio/media-stream")
async def stream(ws: WebSocket):
    await ws.accept()
    call=None
    stream_sid=None

    while True:
        msg=json.loads(await ws.receive_text())

        if msg["event"]=="start":
            sid=msg["start"]["callSid"]
            stream_sid=msg["start"]["streamSid"]
            call=Call(sid)
            calls[sid]=call

            _, opening = bridge.start_session(sid)

            await eleven_speak(ws, stream_sid, "Hello. " + opening)
            call.awaiting=True

        if msg["event"]=="media":
            payload=base64.b64decode(msg["media"]["payload"])

            if pcm_energy(payload)>200:
                call.last_audio=time.time()
                call.buffer+=payload

                if call.timer:
                    call.timer.cancel()

                call.timer=asyncio.create_task(
                    silence_watch(ws, call, stream_sid)
                )
