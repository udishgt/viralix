"""
Viralix Backend — FastAPI
Full pipeline: download → transcribe → AI analyze → FFmpeg cut → burn captions
With production-grade authentication: password hashing, JWT, session management
"""
import os, uuid, json, asyncio, shutil, subprocess, sys, textwrap, re
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

try:
    from backend.models import init_db, get_db, User
    from backend.auth import (
        create_access_token, create_refresh_token, verify_token,
        verify_refresh_token_in_db, revoke_refresh_token,
        register_user, authenticate_user, TokenResponse,
        ACCESS_TOKEN_EXPIRE_MINUTES
    )
except ModuleNotFoundError:
    from models import init_db, get_db, User
    from auth import (
        create_access_token, create_refresh_token, verify_token,
        verify_refresh_token_in_db, revoke_refresh_token,
        register_user, authenticate_user, TokenResponse,
        ACCESS_TOKEN_EXPIRE_MINUTES
    )

if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ── CONFIG — auto-detect binaries with env override support ───────────────
def resolve_ffmpeg_binary() -> str:
    env_ffmpeg = os.getenv("FFMPEG_PATH")
    if env_ffmpeg and Path(env_ffmpeg).exists():
        return env_ffmpeg
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for candidate in [
        r"D:\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]:
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"


FFMPEG = resolve_ffmpeg_binary()
DATA_DIR = Path(os.getenv("VIRALIX_DATA_DIR", ".")).expanduser()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
DB_FILE = DATA_DIR / "jobs.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Resolve yt-dlp path: prefer PATH, otherwise use venv\Scripts\yt-dlp.exe if present
YTDLP = shutil.which("yt-dlp")
if not YTDLP:
    venv_ytdlp = Path(sys.executable).parent / "yt-dlp.exe"
    if venv_ytdlp.exists():
        YTDLP = str(venv_ytdlp)

load_dotenv(Path(__file__).with_name(".env"))

jobs: dict = {}

GENRE_PROFILES = {
    "motivation": {
        "keywords": ["change", "discipline", "mindset", "win", "success", "focus", "consistency", "growth"],
        "preferred_emotions": ["inspiring", "shocking"],
    },
    "podcast": {
        "keywords": ["story", "lesson", "advice", "opinion", "guest", "experience", "truth", "insight"],
        "preferred_emotions": ["relatable", "controversial"],
    },
    "healthcare": {
        "keywords": ["health", "doctor", "patient", "symptom", "treatment", "sleep", "nutrition", "exercise"],
        "preferred_emotions": ["inspiring", "relatable"],
    },
    "gaming": {
        "keywords": ["game", "boss", "win", "clutch", "rank", "meta", "build", "strategy"],
        "preferred_emotions": ["funny", "shocking"],
    },
    "business": {
        "keywords": ["business", "sales", "client", "offer", "revenue", "profit", "market", "brand"],
        "preferred_emotions": ["controversial", "inspiring"],
    },
    "education": {
        "keywords": ["learn", "how", "explained", "example", "mistake", "method", "framework", "tips"],
        "preferred_emotions": ["relatable", "inspiring"],
    },
    "general": {
        "keywords": ["story", "why", "how", "truth", "secret", "mistake", "lesson", "result"],
        "preferred_emotions": ["shocking", "relatable"],
    },
}

HOOK_CUES = {
    "you", "your", "nobody", "never", "always", "secret", "truth", "mistake", "today", "stop", "start",
    "why", "how", "what", "biggest", "easy", "hard", "warning", "listen", "watch"
}

EMOTION_HINTS = {
    "funny": {"funny", "laugh", "hilarious", "joke"},
    "shocking": {"crazy", "shocking", "insane", "unbelievable", "wtf", "wild"},
    "inspiring": {"inspire", "hope", "discipline", "growth", "dream", "believe"},
    "relatable": {"same", "relate", "everyone", "daily", "struggle", "real"},
    "controversial": {"controversial", "disagree", "debate", "hot", "take", "wrong"},
}


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
        "poster_url": clip.get("poster_url", ""),
    }


def normalize_job(job: dict) -> dict:
    genre = (job.get("genre") or "general").lower()
    return {
        **job,
        "genre": genre,
        "niche": job.get("niche", ""),
        "audience": job.get("audience", ""),
        "tone": job.get("tone", "balanced"),
        "relevanceMode": job.get("relevanceMode", "balanced"),
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


@app.get("/health")
async def health():
    return {"status": "ok"}

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

def make_job(job_id, title, language, clip_duration, auto_post, captions, user_id="guest", genre="general", niche="", audience="", tone="balanced", relevance_mode="balanced"):
    return {
        "id": job_id, "title": title, "status": "queued",
        "progress": 0, "stage": "download", "log": [],
        "clips": [], "startedAt": datetime.utcnow().isoformat(),
        "language": language, "clipDuration": clip_duration,
        "autoPost": auto_post, "captions": captions, "userId": user_id,
        "genre": (genre or "general").lower(), "niche": niche, "audience": audience,
        "tone": tone, "relevanceMode": relevance_mode,
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
    genre: str = Form("general"),
    niche: str = Form(""),
    audience: str = Form(""),
    tone: str = Form("balanced"),
    relevance_mode: str = Form("balanced"),
    current_user: User = Depends(get_current_user),
):
    if not youtube_url and not video_file:
        raise HTTPException(400, "Provide a YouTube URL or upload an MP4")

    genre = (genre or "general").strip().lower()
    if genre not in GENRE_PROFILES:
        genre = "general"
    tone = (tone or "balanced").strip().lower()
    relevance_mode = (relevance_mode or "balanced").strip().lower()

    job_id = str(uuid.uuid4())[:8]
    title  = "YouTube Video" if youtube_url else (video_file.filename or "Uploaded Video")
    job    = make_job(
        job_id,
        title,
        language,
        clip_duration,
        auto_post,
        captions,
        current_user.id,
        genre=genre,
        niche=(niche or "").strip(),
        audience=(audience or "").strip(),
        tone=tone,
        relevance_mode=relevance_mode,
    )
    jobs[job_id] = job
    save_jobs()

    video_path = None
    if video_file:
        video_path = str(source_file(job_id))
        with open(video_path, "wb") as f:
            f.write(await video_file.read())

    background_tasks.add_task(
        run_pipeline,
        job_id,
        youtube_url,
        video_path,
        language,
        clip_duration,
        auto_post,
        captions,
        genre,
        (niche or "").strip(),
        (audience or "").strip(),
        tone,
        relevance_mode,
    )
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
async def run_pipeline(job_id, youtube_url, video_path, language, clip_duration, auto_post, captions, genre, niche, audience, tone, relevance_mode):
    try:
        update(job_id, status="processing", stage="download", progress=5)
        log(job_id, "Starting download...")
        if youtube_url:
            video_path = await download_media(job_id, youtube_url)
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
        log(job_id, f"Analyzing viral moments for genre={genre} tone={tone} relevance={relevance_mode}...")
        moments = await find_viral_moments(job_id, transcript, clip_duration, genre, niche, audience, tone, relevance_mode)
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

# ── Download Media ────────────────────────────────────────────────────────
async def download_direct_media(job_id: str, url: str) -> str | None:
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, allow_redirects=True, timeout=20) as head_resp:
                content_type = (head_resp.headers.get("content-type") or "").lower()

            if "video" not in content_type and not any(url.lower().split("?")[0].endswith(ext) for ext in [".mp4", ".mov", ".mkv", ".webm", ".m4v"]):
                return None

            async with session.get(url, allow_redirects=True, timeout=None) as resp:
                if resp.status >= 400:
                    return None
                content_type = (resp.headers.get("content-type") or content_type).lower()
                ext = ".mp4"
                if "webm" in content_type:
                    ext = ".webm"
                elif "quicktime" in content_type or ".mov" in url.lower():
                    ext = ".mov"
                elif "x-matroska" in content_type or ".mkv" in url.lower():
                    ext = ".mkv"

                out_path = source_file(job_id).with_suffix(ext)
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                return str(out_path)
    except Exception as e:
        log(job_id, f"Direct media download failed ({e})")
        return None


async def download_media(job_id: str, url: str) -> str:
    direct = await download_direct_media(job_id, url)
    if direct and Path(direct).exists():
        return direct

    out = str(job_dir(job_id) / "source.%(ext)s")
    if not YTDLP:
        raise RuntimeError("yt-dlp not found. Install yt-dlp in the project's venv or add it to PATH.")
    cmd = [YTDLP, "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
           "--merge-output-format", "mp4", "-o", out, url]
    proc = await run_subprocess(cmd)
    if proc.returncode != 0:
        err = proc.stderr or ""
        print(f"YT-DLP ERROR: {err}")
        direct = await download_direct_media(job_id, url)
        if direct and Path(direct).exists():
            return direct
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
    # Local transcription with improved preprocessing and optional faster-whisper model.
    # Falls back to OpenAI/whisper if faster-whisper is not installed.
    try:
        from faster_whisper import WhisperModel
        fw_available = True
    except Exception:
        fw_available = False

    async def _preprocess(in_path: str) -> str:
        """Normalize audio for transcription: resample to 16k mono, apply highpass/lowpass and dynamic normalization."""
        out = Path(str(in_path)).with_suffix(".preproc.wav")
        if out.exists():
            return str(out)
        cmd = [
            FFMPEG, "-y", "-i", str(in_path),
            "-af",
            "highpass=f=200, lowpass=f=8000, aresample=16000, dynaudnorm=f=150",
            "-ac", "1", "-ar", "16000", str(out)
        ]
        proc = await run_subprocess(cmd)
        if proc.returncode != 0:
            log(job_id, f"Audio preprocess failed, using original: {proc.stderr[:200]}")
            return str(in_path)
        return str(out)

    # Preprocess audio first
    preproc = await _preprocess(audio_path)

    # Try faster-whisper medium model for better quality/speed tradeoff
    if fw_available:
        try:
            log(job_id, "Using faster-whisper (medium) for local transcription")
            model = WhisperModel("medium", device="cpu")
            segments = []
            # beam_size improves accuracy at cost of some speed; vad_filter reduces hallucinations
            for segment in model.transcribe(preproc, beam_size=5, word_timestamps=True, vad_filter=True):
                # segment has start, end, text, and words if available
                seg = {"start": float(segment.start), "end": float(segment.end), "text": segment.text}
                # attach word timestamps if present
                try:
                    seg_words = []
                    for w in getattr(segment, "words", []) or []:
                        seg_words.append({"word": w.word, "start": float(w.start), "end": float(w.end)})
                    if seg_words:
                        seg["words"] = seg_words
                except Exception:
                    pass
                segments.append(seg)
            full_text = " ".join(s["text"] for s in segments).strip()
            # Post-process: merge small/adjacent segments to reduce fragmentation
            segments = post_process_segments(segments)
            return {"text": full_text, "segments": segments, "language": "en"}
        except Exception as e:
            log(job_id, f"faster-whisper failed ({e}) — falling back to whisper")

    # Fallback: use OpenAI Whisper (pyannote/whisper) model if available
    try:
        import whisper
        log(job_id, "Loading local Whisper model (base) — may be slower/lower quality)")
        model = whisper.load_model("base")
        # prefer preprocessed audio if available
        result = model.transcribe(preproc or audio_path, word_timestamps=True)
        segs = []
        for s in result.get("segments", []):
            segs.append({"start": s["start"], "end": s["end"], "text": s["text"],
                         "words": s.get("words", [])})
        segs = post_process_segments(segs)
        return {"text": result.get("text", ""), "segments": segs, "language": result.get("language", "en")}
    except Exception as e:
        log(job_id, f"Local transcription failed: {e}")
        raise


def post_process_segments(segments: list[dict]) -> list[dict]:
    """Merge short/adjacent segments and clean punctuation to reduce fragmentation and hallucinations.

    Rules:
    - Merge segments when gap <= 0.3s
    - Merge if combined length < 120 chars to avoid excessively long segments
    - Strip leading/trailing whitespace and normalize internal spaces
    - Light punctuation cleanup: collapse repeated punctuation and ensure spacing
    """
    if not segments:
        return segments
    out = []
    cur = segments[0].copy()
    cur["text"] = " ".join(cur.get("text","").split())
    for seg in segments[1:]:
        seg_text = " ".join(seg.get("text","").split())
        gap = seg.get("start", 0) - cur.get("end", 0)
        combined = (cur.get("text","") + " " + seg_text).strip()
        if gap <= 0.3 and len(combined) <= 120:
            # merge
            cur["end"] = seg.get("end", cur["end"])
            cur["text"] = combined
            # merge words if present
            if "words" in cur or "words" in seg:
                cur_words = cur.get("words", []) + seg.get("words", [])
                cur["words"] = cur_words
        else:
            # finalize current
            cur["text"] = clean_punctuation(cur.get("text",""))
            out.append(cur)
            cur = seg.copy()
            cur["text"] = seg_text
    cur["text"] = clean_punctuation(cur.get("text",""))
    out.append(cur)
    return out


def clean_punctuation(text: str) -> str:
    """Simple punctuation normalization: collapse multiple punctuation, trim spaces, capitalize sentence starts."""
    import re
    if not text:
        return text
    # collapse repeated punctuation
    text = re.sub(r"[\?\!]{2,}", lambda m: m.group(0)[0], text)
    text = re.sub(r"\.\.+", "…", text)
    # normalize spaces around punctuation
    text = re.sub(r"\s+([\.,!?…])", r"\1", text)
    text = re.sub(r"([\.,!?…])([^\s])", r"\1 \2", text)
    text = " ".join(text.split())
    # capitalize after sentence boundaries
    parts = [p.strip().capitalize() for p in re.split(r'([\.!?…]\s+)', text)]
    # recombine keeping punctuation separators
    if len(parts) > 1:
        text = "".join(parts)
    else:
        text = parts[0]
    return text

# ── AI Viral Detection ────────────────────────────────────────────────────
def tokenize_text(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z']+", (text or "").lower()) if t]


def infer_emotion(text: str, preferred: list[str]) -> str:
    tokens = set(tokenize_text(text))
    scored = []
    for emotion, hints in EMOTION_HINTS.items():
        score = len(tokens.intersection(hints))
        if emotion in preferred:
            score += 1
        scored.append((score, emotion))
    scored.sort(reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else (preferred[0] if preferred else "relatable")


def build_fallback_moments(transcript: dict, clip_duration: int, genre: str, niche: str, audience: str, relevance_mode: str) -> list[dict]:
    segs = transcript.get("segments", [])
    if not segs:
        return [{
            "rank": 1,
            "start": 0.0,
            "end": float(max(8, min(clip_duration, 30))),
            "title": f"{genre.title()} Highlight",
            "hook_line": f"Quick {genre} highlight tailored for short-form viewers.",
            "why_viral": "Fallback highlight generated because transcript segments were empty.",
            "score": 7.4,
            "emotion": GENRE_PROFILES.get(genre, GENRE_PROFILES["general"])["preferred_emotions"][0],
        }]

    profile = GENRE_PROFILES.get(genre, GENRE_PROFILES["general"])
    extra_keywords = tokenize_text(f"{niche} {audience}")
    keywords = set(profile["keywords"] + extra_keywords)
    strictness = {"discovery": 0.8, "balanced": 1.0, "precision": 1.3}.get(relevance_mode, 1.0)

    candidates = []
    for i, seg in enumerate(segs):
        start = float(seg.get("start", 0.0))
        end = max(start + 1.0, start + float(clip_duration))

        chunk_parts = []
        j = i
        while j < len(segs) and float(segs[j].get("start", 0.0)) < end:
            chunk_parts.append((segs[j].get("text") or "").strip())
            end = max(end, float(segs[j].get("end", end)))
            if end - start >= clip_duration:
                break
            j += 1

        chunk_text = " ".join(p for p in chunk_parts if p).strip()
        if len(chunk_text) < 30:
            continue

        tokens = tokenize_text(chunk_text)
        token_set = set(tokens)
        keyword_hits = len(token_set.intersection(keywords))
        hook_hits = len(token_set.intersection(HOOK_CUES))
        short_sentence_bonus = 1 if len(tokens) <= 90 else 0
        relevance_score = (keyword_hits * 1.25 + hook_hits * 0.95 + short_sentence_bonus) * strictness

        candidates.append({
            "start": start,
            "end": min(end, start + clip_duration + 8),
            "text": chunk_text,
            "score": relevance_score,
            "keyword_hits": keyword_hits,
            "hook_hits": hook_hits,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top = []
    for cand in candidates:
        overlaps = False
        for kept in top:
            if not (cand["end"] <= kept["start"] or cand["start"] >= kept["end"]):
                overlaps = True
                break
        if overlaps:
            continue
        top.append(cand)
        if len(top) >= 5:
            break

    moments = []
    preferred = profile["preferred_emotions"]
    for idx, cand in enumerate(top, 1):
        text = cand["text"]
        title_words = text.split()[:7]
        title = " ".join(title_words).strip(".,!?;:-") or f"{genre.title()} Viral Moment {idx}"
        hook_line = text.split(".")[0][:120].strip()
        why = f"High {genre} relevance with {cand['keyword_hits']} keyword hits and a strong hook opening."
        score = max(6.8, min(9.8, 6.9 + cand["score"] * 0.35 - (idx - 1) * 0.2))
        moments.append({
            "rank": idx,
            "start": round(cand["start"], 2),
            "end": round(cand["end"], 2),
            "title": title,
            "hook_line": hook_line,
            "why_viral": why,
            "score": round(score, 1),
            "emotion": infer_emotion(text, preferred),
        })

    if moments:
        return moments

    # Safety fallback for very sparse transcripts.
    first = segs[:5]
    return [
        {
            "rank": i + 1,
            "start": float(s.get("start", 0.0)),
            "end": min(float(s.get("start", 0.0)) + clip_duration, float(s.get("end", 0.0)) + 12),
            "title": f"{genre.title()} Moment {i + 1}",
            "hook_line": (s.get("text") or "")[:110],
            "why_viral": f"Potentially strong {genre} clip based on timing and context.",
            "score": round(8.2 - i * 0.25, 1),
            "emotion": profile["preferred_emotions"][i % len(profile["preferred_emotions"])],
        }
        for i, s in enumerate(first)
    ]


def sanitize_moments(moments: list[dict], clip_duration: int, profile: dict, genre: str) -> list[dict]:
    cleaned = []
    for i, m in enumerate(moments[:5], 1):
        start = max(0.0, float(m.get("start", 0.0)))
        end = max(start + 1.0, float(m.get("end", start + clip_duration)))
        end = min(end, start + clip_duration + 10)
        emotion = str(m.get("emotion") or "").lower()
        if emotion not in {"funny", "shocking", "inspiring", "relatable", "controversial"}:
            emotion = profile["preferred_emotions"][0]
        cleaned.append({
            "rank": i,
            "start": round(start, 2),
            "end": round(end, 2),
            "title": str(m.get("title") or f"{genre.title()} Viral Moment {i}")[:90],
            "hook_line": str(m.get("hook_line") or m.get("hook") or "")[:180],
            "why_viral": str(m.get("why_viral") or f"Strong {genre} relevance and retention potential.")[:260],
            "score": round(max(6.0, min(10.0, float(m.get("score", 8.0)))), 1),
            "emotion": emotion,
        })
    return cleaned


async def find_viral_moments(job_id: str, transcript: dict, clip_duration: int, genre: str, niche: str, audience: str, tone: str, relevance_mode: str) -> list:
    profile = GENRE_PROFILES.get(genre, GENRE_PROFILES["general"])
    segs = transcript.get("segments", [])
    lines = [f"[{s['start']:.1f}s-{s['end']:.1f}s]: {s.get('text', '')}" for s in segs]
    timed = "\n".join(lines[:200])

    try:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY missing")

        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        prompt = f"""You are a short-form growth strategist for {genre} content.

Inputs:
- Genre: {genre}
- Niche: {niche or 'general'}
- Audience: {audience or 'broad'}
- Tone: {tone}
- Relevance mode: {relevance_mode}
- Target clip length: {clip_duration} seconds
- Genre keywords to prioritize: {', '.join(profile['keywords'])}

TRANSCRIPT:
{timed}

Task:
- Return exactly 5 clips.
- Prioritize semantic relevance to the genre and niche.
- Each clip should feel standalone and start with a strong first 1-3 seconds.
- Prefer clips in the {max(15, clip_duration - 10)}-{clip_duration + 6}s range.
- Keep title <= 7 words.
- hook_line should be the most attention-grabbing first sentence.

Respond with ONLY valid JSON:
{{"moments":[{{"rank":1,"start":12.5,"end":58.0,"title":"...","hook_line":"...","why_viral":"...","score":9.1,"emotion":"shocking"}}]}}

Valid emotions: funny, shocking, inspiring, relatable, controversial"""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        ai_moments = json.loads(raw).get("moments", [])
        cleaned = sanitize_moments(ai_moments, clip_duration, profile, genre)
        if cleaned:
            return cleaned
        raise RuntimeError("AI returned no valid moments")

    except Exception as e:
        log(job_id, f"AI error: {e} — using relevance fallback")
        return build_fallback_moments(transcript, clip_duration, genre, niche, audience, relevance_mode)

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
        if cap_segs:
            srt_path = out_dir / f"clip_{moment['rank']}.srt"
            write_srt(cap_segs, srt_path)
            srt_abs = srt_path.resolve()
            srt_escaped = str(srt_abs).replace("\\", "/").replace(":", "\\:")
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
            try:
                srt_path.unlink()
            except:
                pass
        else:
            if tmp.exists():
                shutil.copy(str(tmp), str(path))
            log(job_id, f"No transcript segments for captions — exported clean clip {moment['rank']}")
    else:
        if tmp.exists(): shutil.copy(str(tmp), str(path))
        log(job_id, f"Captions disabled — exported clean clip {moment['rank']}")

    try: tmp.unlink()
    except: pass

    if not path.exists():
        return None

    poster_name = f"poster_{moment['rank']}.jpg"
    poster_path = out_dir / poster_name
    try:
        poster_cmd = [
            FFMPEG, "-y",
            "-ss", "0.8",
            "-i", str(path),
            "-vframes", "1",
            "-vf", "scale=540:960:force_original_aspect_ratio=increase,crop=540:960",
            "-q:v", "2",
            str(poster_path),
        ]
        poster_proc = await run_subprocess(poster_cmd)
        if poster_proc.returncode != 0 or not poster_path.exists():
            poster_path = None
    except Exception:
        poster_path = None

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
        "poster_url": f"/outputs/{job_id}/{poster_name}" if poster_path and poster_path.exists() else "",
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