"""
Viralix Backend — FastAPI
Full pipeline: download → transcribe → AI analyze → FFmpeg cut → burn captions
With production-grade authentication: password hashing, JWT, session management
"""
import os, uuid, json, asyncio, shutil, subprocess, sys, textwrap
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Annotated
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from sqlalchemy.orm import Session
import uvicorn

from backend.models import init_db, get_db, User
from backend.auth import (
    create_access_token, create_refresh_token, verify_token, 
    verify_refresh_token_in_db, revoke_refresh_token,
    register_user, authenticate_user, TokenResponse,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ── CONFIG — update FFMPEG path if yours is different ─────────────────────
FFMPEG     = r"D:\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
UPLOAD_DIR = Path("uploads");  UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = Path("outputs");  OUTPUT_DIR.mkdir(exist_ok=True)
DB_FILE    = Path("jobs.json")

# Resolve yt-dlp path: prefer PATH, otherwise use venv\Scripts\yt-dlp.exe if present
YTDLP = shutil.which("yt-dlp")
if not YTDLP:
    venv_ytdlp = Path(sys.executable).parent / "yt-dlp.exe"
    if venv_ytdlp.exists():
        YTDLP = str(venv_ytdlp)

load_dotenv(Path(__file__).with_name(".env"))

jobs: dict = {}


def job_dir(job_id: str) -> Path:
    path = OUTPUT_DIR / job_id
    path.mkdir(exist_ok=True)
    return path


def source_file(job_id: str) -> Path:
    return job_dir(job_id) / "source.mp4"


def audio_file(job_id: str) -> Path:
    return job_dir(job_id) / "audio.wav"


def transcript_file(job_id: str) -> Path:
    return job_dir(job_id) / "transcript.json"


def save_transcript(job_id: str, transcript: dict):
    transcript_file(job_id).write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")


def load_transcript(job_id: str) -> dict:
    path = transcript_file(job_id)
    if not path.exists():
        raise FileNotFoundError("Transcript not found for this job")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_clip_metadata(clip: dict) -> dict:
    filename = clip.get("filename", "")
    return {
        **clip,
        "download_url": f"/outputs/{filename}" if filename else clip.get("download_url", ""),
    }


def normalize_job(job: dict) -> dict:
    return {
        **job,
        "clips": [normalize_clip_metadata(c) for c in job.get("clips", [])],
    }

def save_jobs():
    try:
        with open(DB_FILE, "w") as f: json.dump(jobs, f, default=str)
    except: pass

def load_jobs():
    global jobs
    if DB_FILE.exists():
        try:
            with open(DB_FILE) as f: jobs = json.load(f)
        except: jobs = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_jobs()
    # Initialize SQL schema for auth models
    try:
        init_db()
        print("[auth] Initialized database schema")
    except Exception as e:
        print(f"[auth] DB init error: {e}")
    asyncio.create_task(cleanup_expired_clips())
    yield

app = FastAPI(title="Viralix API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")

# Security
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    token = credentials.credentials
    payload = verify_token(token, token_type="access")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired access token")
    user = db.query(User).filter(User.id == payload.sub).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def log(job_id: str, msg: str):
    print(f"[{job_id}] {msg}")
    if job_id in jobs:
        jobs[job_id]["log"].append(msg)
        save_jobs()


async def run_subprocess(cmd: list[str]):
    return await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)

def update(job_id: str, **kwargs):
    if job_id in jobs:
        jobs[job_id].update(kwargs)
        save_jobs()

def make_job(job_id, title, language, clip_duration, auto_post, captions, user_id="guest"):
    return {
        "id": job_id, "title": title, "status": "queued",
        "progress": 0, "stage": "download", "log": [],
        "clips": [], "startedAt": datetime.utcnow().isoformat(),
        "language": language, "clipDuration": clip_duration,
        "autoPost": auto_post, "captions": captions, "userId": user_id,
        "expiresAt": (datetime.utcnow() + timedelta(days=15)).isoformat(),
        "error": None,
    }

async def cleanup_expired_clips():
    while True:
        await asyncio.sleep(3600)
        now = datetime.utcnow()
        for jid, job in list(jobs.items()):
            exp = job.get("expiresAt")
            if exp and datetime.fromisoformat(exp) < now:
                clip_dir = OUTPUT_DIR / jid
                if clip_dir.exists(): shutil.rmtree(clip_dir)
                del jobs[jid]
        save_jobs()

# ── Auth Endpoints ───────────────────────────────────────────────────────


@app.post("/auth/signup")
async def signup(payload: dict, db: Session = Depends(get_db)):
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password")
    display_name = payload.get("displayName") or None
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
    user, err = register_user(email, password, display_name, db)
    if not user:
        raise HTTPException(status_code=400, detail=err)

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email, db)
    print(f"[auth] signup user={user.email} id={user.id}")
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "user": user.to_dict()}


@app.post("/auth/login")
async def login(payload: dict, db: Session = Depends(get_db)):
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
    user, err = authenticate_user(email, password, db)
    if not user:
        raise HTTPException(status_code=401, detail=err)

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email, db)
    print(f"[auth] login user={user.email} id={user.id}")
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "user": user.to_dict()}


@app.post("/auth/logout")
async def logout(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Invalidate a refresh token. Client should send {"refresh_token": "..."}"""
    token = payload.get("refresh_token")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    ok = revoke_refresh_token(token, db)
    print(f"[auth] logout user={current_user.email} revoked={ok}")
    return {"ok": True}


@app.post("/auth/refresh")
async def refresh(payload: dict, db: Session = Depends(get_db)):
    token = payload.get("refresh_token")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    user = verify_refresh_token_in_db(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or revoked refresh token")
    access = create_access_token(user.id, user.email)
    # Optionally rotate refresh token here. We'll reuse current refresh for simplicity.
    print(f"[auth] refresh user={user.email}")
    return {"access_token": access, "token_type": "bearer", "user": user.to_dict()}


@app.get("/auth/me")
async def me(current_user: User = Depends(get_current_user)):
    return current_user.to_dict()

# ── Upload & start pipeline ───────────────────────────────────────────────
@app.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    youtube_url: Optional[str] = Form(None),
    video_file: Optional[UploadFile] = File(None),
    language: str = Form("English"),
    clip_duration: int = Form(45),
    auto_post: bool = Form(False),
    captions: bool = Form(True),
    current_user: User = Depends(get_current_user),
):
    if not youtube_url and not video_file:
        raise HTTPException(400, "Provide a YouTube URL or upload an MP4")

    job_id = str(uuid.uuid4())[:8]
    title  = "YouTube Video" if youtube_url else (video_file.filename or "Uploaded Video")
    job    = make_job(job_id, title, language, clip_duration, auto_post, captions, current_user.id)
    jobs[job_id] = job
    save_jobs()

    video_path = None
    if video_file:
        video_path = str(source_file(job_id))
        with open(video_path, "wb") as f:
            f.write(await video_file.read())

    background_tasks.add_task(run_pipeline, job_id, youtube_url, video_path, language, clip_duration, auto_post, captions)
    return job

@app.get("/status/{job_id}")
async def status(job_id: str, current_user: User = Depends(get_current_user)):
    if job_id not in jobs: raise HTTPException(404, "Job not found")
    if jobs[job_id].get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to access this job")
    jobs[job_id] = normalize_job(jobs[job_id])
    return jobs[job_id]


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, current_user: User = Depends(get_current_user)):
    if job_id not in jobs: raise HTTPException(404, "Job not found")
    if jobs[job_id].get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to access this job")
    jobs[job_id] = normalize_job(jobs[job_id])
    return jobs[job_id]

@app.get("/clips/{job_id}")
async def get_clips(job_id: str, current_user: User = Depends(get_current_user)):
    if job_id not in jobs: raise HTTPException(404, "Job not found")
    if jobs[job_id].get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to access this job")
    jobs[job_id] = normalize_job(jobs[job_id])
    return jobs[job_id].get("clips", [])

@app.get("/history")
async def history(current_user: User = Depends(get_current_user)):
    result = [j for j in jobs.values() if j.get("userId") == current_user.id]
    normalized = [normalize_job(j) for j in result]
    return sorted(normalized, key=lambda x: x.get("startedAt",""), reverse=True)

@app.get("/download/{job_id}/{filename}")
async def download(job_id: str, filename: str, current_user: User = Depends(get_current_user)):
    if job_id not in jobs: raise HTTPException(404, "Job not found")
    if jobs[job_id].get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to download this file")
    path = OUTPUT_DIR / job_id / filename
    if not path.exists(): raise HTTPException(404, "File not found")
    return FileResponse(str(path), media_type="video/mp4", filename=filename)


def get_clip_entry(job_id: str, rank: int) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    for clip in job.get("clips", []):
        if int(clip.get("rank", 0)) == int(rank):
            return clip
    raise HTTPException(404, "Clip not found")


@app.patch("/jobs/{job_id}/clips/{rank}")
async def rename_clip(job_id: str, rank: int, data: dict, current_user: User = Depends(get_current_user)):
    if job_id not in jobs: raise HTTPException(404, "Job not found")
    if jobs[job_id].get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to modify this job")
    clip = get_clip_entry(job_id, rank)
    if "title" in data and data["title"]:
        clip["title"] = str(data["title"])
    if "hook_line" in data and data["hook_line"]:
        clip["hook_line"] = str(data["hook_line"])
        clip["hook"] = str(data["hook_line"])
    update(job_id, clips=jobs[job_id]["clips"])
    return clip


@app.delete("/jobs/{job_id}/clips/{rank}")
async def delete_clip(job_id: str, rank: int, current_user: User = Depends(get_current_user)):
    if job_id not in jobs: raise HTTPException(404, "Job not found")
    if jobs[job_id].get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to modify this job")
    clip = get_clip_entry(job_id, rank)
    file_path = OUTPUT_DIR / job_id / Path(clip["filename"]).name
    if file_path.exists():
        file_path.unlink()
    srt_path = OUTPUT_DIR / job_id / f"clip_{rank}.srt"
    if srt_path.exists():
        srt_path.unlink()
    jobs[job_id]["clips"] = [c for c in jobs[job_id].get("clips", []) if int(c.get("rank", 0)) != int(rank)]
    update(job_id, clips=jobs[job_id]["clips"])
    return {"ok": True}


@app.post("/jobs/{job_id}/clips/{rank}/regenerate")
async def regenerate_clip(job_id: str, rank: int, current_user: User = Depends(get_current_user)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("userId") != current_user.id:
        raise HTTPException(403, "Not authorized to modify this job")
    clip = get_clip_entry(job_id, rank)
    transcript = load_transcript(job_id)
    source = source_file(job_id)
    if not source.exists():
        raise HTTPException(404, "Source video not available for regeneration")
    if clip.get("start") is None or clip.get("end") is None:
        raise HTTPException(400, "Stored clip boundaries are missing for regeneration")

    moment = {
        "rank": clip["rank"],
        "start": float(clip["start"]),
        "end": float(clip["end"]),
        "title": clip.get("title", ""),
        "hook_line": clip.get("hook_line", clip.get("hook", "")),
        "why_viral": clip.get("why_viral", ""),
        "score": clip.get("score", 8.0),
        "emotion": clip.get("emotion", "inspiring"),
    }
    new_clip = await render_clip(job_id, str(source), moment, transcript, job.get("language", "English"), bool(clip.get("captions", True)), int(job.get("clipDuration", 45)))
    if not new_clip:
        raise HTTPException(500, "Failed to regenerate clip")

    for i, item in enumerate(jobs[job_id].get("clips", [])):
        if int(item.get("rank", 0)) == int(rank):
            jobs[job_id]["clips"][i] = new_clip
            break
    update(job_id, clips=jobs[job_id]["clips"])
    return new_clip

# ── Pipeline ──────────────────────────────────────────────────────────────
async def run_pipeline(job_id, youtube_url, video_path, language, clip_duration, auto_post, captions):
    try:
        update(job_id, status="processing", stage="download", progress=5)
        log(job_id, "Starting download...")
        if youtube_url:
            video_path = await download_youtube(job_id, youtube_url)
        elif video_path:
            final_source = source_file(job_id)
            if str(Path(video_path)) != str(final_source):
                shutil.copyfile(video_path, final_source)
                video_path = str(final_source)
        log(job_id, f"Video ready: {Path(video_path).name}")

        update(job_id, stage="transcribe", progress=25)
        log(job_id, "Extracting audio...")
        audio_path = await extract_audio(job_id, video_path)

        log(job_id, "Transcribing audio...")
        transcript = await transcribe(job_id, audio_path)
        save_transcript(job_id, transcript)
        log(job_id, f"Transcript ready — {len(transcript.get('segments', []))} segments")

        update(job_id, stage="analyze", progress=50)
        log(job_id, "Claude analyzing viral moments...")
        moments = await find_viral_moments(job_id, transcript, clip_duration)
        log(job_id, f"Found {len(moments)} viral moments")

        update(job_id, stage="generate", progress=70)
        log(job_id, "Cutting clips with FFmpeg...")
        clips = await cut_clips(job_id, video_path, moments, transcript, language, captions, clip_duration)
        log(job_id, f"Generated {len(clips)} clips")

        update(job_id, status="done", stage="complete", progress=100, clips=clips)
        log(job_id, "All done! Clips available for 15 days.")

    except Exception as e:
        import traceback
        print(f"PIPELINE ERROR [{job_id}]:\n{traceback.format_exc()}")
        update(job_id, status="error", error=str(e))
        log(job_id, f"ERROR: {str(e)}")

# ── Download YouTube ──────────────────────────────────────────────────────
async def download_youtube(job_id: str, url: str) -> str:
    out = str(job_dir(job_id) / "source.%(ext)s")
    if not YTDLP:
        raise RuntimeError("yt-dlp not found. Install yt-dlp in the project's venv or add it to PATH.")
    cmd = [YTDLP, "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
           "--merge-output-format", "mp4", "-o", out, url]
    proc = await run_subprocess(cmd)
    if proc.returncode != 0:
        err = proc.stderr or ""
        print(f"YT-DLP ERROR: {err}")
        raise RuntimeError(f"Download failed: {err[-300:]}")
    for f in job_dir(job_id).glob("source.*"):
        return str(f)
    raise RuntimeError("Downloaded file not found")

# ── Extract Audio ─────────────────────────────────────────────────────────
async def extract_audio(job_id: str, video_path: str) -> str:
    audio_path = str(audio_file(job_id))
    cmd = [FFMPEG, "-y", "-i", video_path,
           "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path]
    proc = await run_subprocess(cmd)
    if proc.returncode != 0:
        err = proc.stderr or ""
        print(f"FFMPEG AUDIO ERROR:\n{err}")
        raise RuntimeError(f"Audio extraction failed: {err[-400:]}")
    if not Path(audio_path).exists():
        raise RuntimeError(f"Audio file not created. FFmpeg path correct? Current: {FFMPEG}")
    log(job_id, "Audio extracted successfully")
    return audio_path

# ── Transcribe ────────────────────────────────────────────────────────────
async def transcribe(job_id: str, audio_path: str) -> dict:
    if os.getenv("ASSEMBLYAI_API_KEY"):
        return await transcribe_assemblyai(job_id, audio_path)
    if os.getenv("OPENAI_API_KEY"):
        return await transcribe_openai(job_id, audio_path)
    try:
        return await transcribe_local(job_id, audio_path)
    except Exception as e:
        log(job_id, f"Warning: Whisper unavailable ({e}) — using fallback")
        return {
            "text": "Sample transcript.",
            "segments": [{"start":0,"end":30,"text":"Sample content.","words":[{"word":"Sample","start":0.0,"end":0.5}]}],
            "language": "en"
        }

async def transcribe_assemblyai(job_id: str, audio_path: str) -> dict:
    import aiohttp
    key = os.getenv("ASSEMBLYAI_API_KEY")
    headers = {"authorization": key}
    async with aiohttp.ClientSession() as session:
        with open(audio_path, "rb") as f: data = f.read()
        async with session.post("https://api.assemblyai.com/v2/upload", headers=headers, data=data) as r:
            upload_url = (await r.json())["upload_url"]
        async with session.post("https://api.assemblyai.com/v2/transcript", headers=headers,
            json={"audio_url": upload_url, "language_detection": True}) as r:
            tid = (await r.json())["id"]
        log(job_id, "Transcription submitted — waiting for AssemblyAI...")
        while True:
            async with session.get(f"https://api.assemblyai.com/v2/transcript/{tid}", headers=headers) as r:
                data = await r.json()
            if data["status"] == "completed":
                words = data.get("words") or []
                segs = [{"start": w["start"]/1000, "end": w["end"]/1000, "text": w["text"],
                          "words": [{"word":w["text"],"start":w["start"]/1000,"end":w["end"]/1000}]} for w in words]
                return {"text": data.get("text",""), "segments": segs, "language": data.get("language_code","en")}
            if data["status"] == "error":
                raise RuntimeError(f"AssemblyAI error: {data['error']}")
            await asyncio.sleep(4)

async def transcribe_openai(job_id: str, audio_path: str) -> dict:
    import openai
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    with open(audio_path, "rb") as f:
        result = await client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="verbose_json",
            timestamp_granularities=["word","segment"])
    return {
        "text": result.text,
        "segments": [{"start":s.start,"end":s.end,"text":s.text,
                      "words":[{"word":w.word,"start":w.start,"end":w.end} for w in (s.words or [])]}
                     for s in (result.segments or [])],
        "language": result.language
    }

async def transcribe_local(job_id: str, audio_path: str) -> dict:
    import whisper
    log(job_id, "Loading local Whisper model (first time is slow)...")
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, word_timestamps=True)
    return {"text": result["text"], "segments": result["segments"], "language": result["language"]}

# ── AI Viral Detection ────────────────────────────────────────────────────
async def find_viral_moments(job_id: str, transcript: dict, clip_duration: int) -> list:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        segs = transcript.get("segments", [])
        lines = [f"[{s['start']:.1f}s–{s['end']:.1f}s]: {s['text']}" for s in segs]
        timed = "\n".join(lines[:150])

        prompt = f"""You are a viral short-form video expert. Analyze this transcript and find top 5 viral moments for {clip_duration}-second clips.

TRANSCRIPT:
{timed}

Find segments with: strong hook in first 3s, high emotion, complete as standalone, length {max(15,clip_duration-10)}–{clip_duration}s.

Respond ONLY with valid JSON, no markdown:
{{"moments":[{{"rank":1,"start":12.5,"end":58.0,"title":"Short title max 6 words","hook_line":"Opening hook line","why_viral":"Why this goes viral","score":9.2,"emotion":"shocking"}}]}}

Valid emotions: funny, shocking, inspiring, relatable, controversial"""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=1500,
            messages=[{"role":"user","content":prompt}])
        raw = msg.content[0].text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)["moments"]

    except Exception as e:
        log(job_id, f"AI error: {e} — using fallback segments")
        segs = transcript.get("segments", [])
        return [{"rank":i+1,"start":s["start"],"end":min(s["start"]+clip_duration,s["end"]+30),
             "title":f"Viral Moment {i+1}","hook_line":s["text"][:80],"why_viral":"High engagement segment",
             "score":round(8.5-i*0.3,1),"emotion":["shocking","inspiring","relatable","funny","controversial"][i%5]}
            for i, s in enumerate(segs[:5])]

# ── Translation ───────────────────────────────────────────────────────────
async def translate_text(text: str, target_lang: str) -> str:
    LANG_CODES = {"Spanish":"ES","French":"FR","German":"DE","Italian":"IT","Portuguese":"PT",
                  "Dutch":"NL","Polish":"PL","Russian":"RU","Japanese":"JA","Chinese":"ZH","Korean":"KO"}
    code = LANG_CODES.get(target_lang)
    if not code or target_lang == "English": return text
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=500,
                messages=[{"role":"user","content":f"Translate to {target_lang}. Return ONLY the translation:\n\n{text}"}])
            return msg.content[0].text.strip()
        except: pass
    return text


def build_caption_force_style(cap_segs: list[dict]) -> str:
    max_chars = max((len((seg.get("text") or "").strip()) for seg in cap_segs), default=0)
    if max_chars > 80:
        font_size, margin_lr, margin_v = 22, 152, 28
    elif max_chars > 55:
        font_size, margin_lr, margin_v = 24, 144, 26
    else:
        font_size, margin_lr, margin_v = 26, 128, 24

    return (
        f"FontName=Arial,FontSize={font_size},Bold=1,"
        f"PrimaryColour=&H00FFFFFF&,OutlineColour=&H000000&,"
        f"BackColour=&H99000000&,BorderStyle=3,Outline=6,Shadow=0,"
        f"Alignment=2,MarginV={margin_v},MarginL={margin_lr},MarginR={margin_lr},WrapStyle=2"
    )

# ── Cut Clips + Burn Captions ─────────────────────────────────────────────
async def render_clip(job_id: str, video_path: str, moment: dict, transcript: dict, language: str, captions: bool, clip_duration: int) -> dict | None:
    out_dir = job_dir(job_id)
    start = max(0.0, float(moment["start"]))
    end = max(start + 1.0, min(float(moment["end"]), start + float(clip_duration)))
    dur = end - start
    emotion = moment.get("emotion", "inspiring")
    name = f"clip_{moment['rank']}_{emotion}.mp4"
    path = out_dir / name
    tmp = out_dir / f"tmp_{name}"

    log(job_id, f"Cutting clip {moment['rank']}: {start:.1f}s–{end:.1f}s")

    hook_source = moment.get("hook_line") or moment.get("hook") or ""
    hook = await translate_text(hook_source, language)
    title = await translate_text(moment.get("title", ""), language)

    cmd1 = [FFMPEG, "-y",
            "-ss", str(start), "-i", video_path, "-t", str(dur),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    proc1 = await run_subprocess(cmd1)
    if proc1.returncode != 0:
        err = proc1.stderr or ""
        print(f"FFMPEG CUT ERROR clip {moment['rank']}:\n{err[-500:]}")
        log(job_id, f"Clip {moment['rank']} cut failed — skipping")
        return None

    if not tmp.exists():
        log(job_id, f"Clip {moment['rank']} tmp not created — skipping")
        return None

    if captions:
        cap_segs = get_caption_segments(transcript, start, end)
        srt_path = out_dir / f"clip_{moment['rank']}.srt"
        write_srt(cap_segs, srt_path)
        srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
        vf_caption = f"subtitles='{srt_escaped}':force_style='{build_caption_force_style(cap_segs)}'"

        cmd2 = [FFMPEG, "-y", "-i", str(tmp), "-vf", vf_caption,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "copy", "-movflags", "+faststart", str(path)]
        proc2 = await run_subprocess(cmd2)

        if proc2.returncode != 0:
            err = proc2.stderr or ""
            print(f"FFMPEG CAPTION ERROR clip {moment['rank']}:\n{err[-300:]}")
            log(job_id, f"Caption burn failed — using clean cut for clip {moment['rank']}")
            if tmp.exists(): shutil.copy(str(tmp), str(path))
        try: srt_path.unlink()
        except: pass
    else:
        if tmp.exists(): shutil.copy(str(tmp), str(path))
        log(job_id, f"Captions disabled — exported clean clip {moment['rank']}")

    try: tmp.unlink()
    except: pass

    if not path.exists():
        return None

    size_mb = round(path.stat().st_size / 1024 / 1024, 1)
    clip = {
        "rank": moment["rank"],
        "title": title,
        "hook": hook,
        "hook_line": hook,
        "why_viral": moment.get("why_viral", ""),
        "score": moment.get("score", 8.0),
        "emotion": emotion,
        "duration": round(dur, 1),
        "start": round(start, 1),
        "end": round(end, 1),
        "filename": f"{job_id}/{name}",
        "file_size_mb": size_mb,
        "language": language,
        "download_url": f"/outputs/{job_id}/{name}",
        "captions": captions,
    }
    log(job_id, f"Clip {moment['rank']} ready ({size_mb} MB)")
    return clip


async def cut_clips(job_id: str, video_path: str, moments: list, transcript: dict, language: str, captions: bool, clip_duration: int) -> list:
    clips = []
    for m in moments:
        clip = await render_clip(job_id, video_path, m, transcript, language, captions, clip_duration)
        if clip:
            clips.append(clip)
    return clips

def get_caption_segments(transcript: dict, clip_start: float, clip_end: float) -> list:
    segs = []
    for s in transcript.get("segments", []):
        if s["end"] < clip_start or s["start"] > clip_end: continue
        segs.append({
            "start": max(0, s["start"] - clip_start),
            "end":   min(clip_end - clip_start, s["end"] - clip_start),
            "text":  s["text"].strip()
        })
    return segs

def wrap_caption_text(text: str) -> str:
    clean_text = " ".join(text.split())
    if not clean_text:
        return clean_text

    length = len(clean_text)
    if length > 80:
        width = 48
    elif length > 55:
        width = 42
    else:
        width = 38

    words = clean_text.split(" ")
    longest_word = max((len(word) for word in words), default=0)
    width = max(width, min(longest_word + 2, 54))
    # Enforce a hard maximum of two lines. If the text would wrap to
    # more than two lines, collapse remaining words into the second
    # line and append an ellipsis so rendering stays within a predictable
    # vertical footprint on mobile devices.
    max_lines = 2
    lines: list[str] = []
    idx = 0
    n = len(words)

    for line_no in range(max_lines):
        if idx >= n:
            break
        line_words: list[str] = []
        line_len = 0
        while idx < n:
            w = words[idx]
            add_len = len(w) + (1 if line_words else 0)
            if line_len + add_len <= width:
                line_words.append(w)
                line_len += add_len
                idx += 1
            else:
                break

        # If no words fitted (very long single word), force one word onto the line
        if not line_words and idx < n:
            line_words.append(words[idx])
            idx += 1

        # If this is the last allowed line but text remains, merge remaining
        # words and truncate with an ellipsis to keep height predictable.
        if line_no == max_lines - 1 and idx < n:
            remaining = " ".join(words[idx:])
            candidate = (" " .join(line_words + [remaining])).strip()
            if len(candidate) > width:
                truncated = candidate[: max(0, width - 1)].rstrip()
                truncated = truncated.rstrip(".,;:!?")
                candidate = truncated + "…"
            else:
                candidate = candidate + "…"
            lines.append(candidate)
            idx = n
            break

        lines.append(" ".join(line_words))

    return "\n".join(lines)

def write_srt(segments: list, path: Path):
    def fmt(t: float) -> str:
        h = int(t//3600); m = int((t%3600)//60); s = t%60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".",",")
    lines = []
    for i, seg in enumerate(segments, 1):
        if seg["text"].strip():
            lines.append(f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{wrap_caption_text(seg['text'])}\n")
    path.write_text("\n".join(lines), encoding="utf-8")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)