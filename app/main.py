
import os
import uuid
import json
import urllib.request
import urllib.error
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Header,
    Form,
)
from pydantic import BaseModel, Field, model_validator

from app.video_generator import (
    generate_scene_video,
    generate_srt,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("broll")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"
MAX_AUDIO_SIZE = 25 * 1024 * 1024

app = FastAPI(
    title="Script and Voiceover to B-roll Video Engine",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
)

jobs = {}
idempotency = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def split_script_into_scenes(script: str) -> list[dict]:
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())

    return [
        {
            "text": sentence.strip(),
            "visual_hint": sentence.strip(),
        }
        for sentence in sentences
        if sentence.strip()
    ]


def get_audio_duration(audio_path: Path):
    """Return audio duration in seconds, or None if unavailable."""
    try:
        from app.video_generator import (
            get_audio_duration as read_duration,
        )

        return read_duration(audio_path)

    except (ImportError, OSError, ValueError, RuntimeError):
        return None

    except Exception:
        log.exception("Could not read audio duration: %s", audio_path)
        return None


class Scene(BaseModel):
    text: str = Field(min_length=1)
    visual_hint: str | None = None


class Captions(BaseModel):
    burn_in: bool = True
    srt: bool = True


class JobRequest(BaseModel):
    audio_url: str | None = None
    script: str | None = None
    scenes: list[Scene] | None = None
    voice: str = "en-IN-female"
    aspect_ratios: list[Literal["16:9", "9:16", "1:1"]] = [
        "16:9"
    ]
    style: str = "bright, modern, real-world footage"
    avoid: list[str] = []
    captions: Captions = Captions()

    @model_validator(mode="after")
    def one_input(self):
        if not self.audio_url and not self.script and not self.scenes:
            raise ValueError(
                "Provide a script or custom scenes. "
                "An audio URL can be included with either."
            )

        if self.script and self.scenes:
            raise ValueError(
                "Provide either script or scenes, not both."
            )

        if self.scenes is not None and not self.scenes:
            raise ValueError("scenes must not be empty.")

        return self


@app.post("/api/v1/broll/preview-scenes")
def preview_scenes(payload: dict):
    script = payload.get("script", "")

    if not isinstance(script, str) or not script.strip():
        raise HTTPException(
            status_code=400,
            detail="Please provide a non-empty script.",
        )

    scenes = split_script_into_scenes(script)

    return {
        "scene_count": len(scenes),
        "scenes": scenes,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "timestamp": now(),
    }


@app.get("/ready")
def ready():
    return {
        "status": "ready",
        "timestamp": now(),
    }


def write_json(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def worker(job_id: str, data: dict):
    jobs[job_id].update(
        status="running",
        progress=10,
        updated_at=now(),
    )

    log.info("Job started: %s", job_id)

    try:
        script = (data.get("script") or "").strip()
        custom_scenes = data.get("scenes")

        # Uploaded audio can be used with a script or custom scenes.
        input_audio_path = (
            Path(data["_input_audio_path"])
            if data.get("_input_audio_path")
            else None
        )

        if not script and not custom_scenes:
            jobs[job_id].update(
                status="failed",
                progress=100,
                updated_at=now(),
                message="No valid script or custom scenes provided.",
            )
            return

        output_dir = OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        video_path = output_dir / "video.mp4"
        audio_path = output_dir / "narration.mp3"
        srt_path = output_dir / "captions.srt"
        timeline_path = output_dir / "timeline.json"
        manifest_path = output_dir / "manifest.json"

        # Download audio when a public audio URL was provided instead
        # of an uploaded file.
        if input_audio_path is None and data.get("audio_url"):
            audio_url = data["audio_url"]

            if not audio_url.startswith(("https://", "http://")):
                raise ValueError(
                    "Audio URL must use HTTP or HTTPS."
                )

            input_audio_path = output_dir / "input_audio"

            request = urllib.request.Request(
                audio_url,
                headers={"User-Agent": "BrollVideoEngine/1.0"},
            )

            try:
                response_context = urllib.request.urlopen(
                    request,
                    timeout=20,
                )
            except urllib.error.HTTPError as exc:
                raise ValueError(
                    f"Audio URL returned HTTP {exc.code}. "
                    "Check that it points directly to an audio file."
                ) from exc

            downloaded_size = 0

            with response_context as response:
                content_type = response.headers.get(
                    "Content-Type", ""
                ).lower()

                if not (
                    content_type.startswith("audio/")
                    or "octet-stream" in content_type
                ):
                    raise ValueError(
                        "URL must return an audio file."
                    )

                content_length = response.headers.get(
                    "Content-Length"
                )

                if (
                    content_length
                    and int(content_length) > MAX_AUDIO_SIZE
                ):
                    raise ValueError(
                        "Audio file must be 25 MB or smaller."
                    )

                with input_audio_path.open("wb") as audio_file:
                    while True:
                        chunk = response.read(64 * 1024)

                        if not chunk:
                            break

                        downloaded_size += len(chunk)

                        if downloaded_size > MAX_AUDIO_SIZE:
                            raise ValueError(
                                "Audio file must be 25 MB or smaller."
                            )

                        audio_file.write(chunk)

            if downloaded_size == 0:
                raise ValueError(
                    "The audio URL returned an empty file."
                )

        # Use custom scenes or create scenes from the script.
        if custom_scenes:
            scenes = [
                (
                    scene.model_dump()
                    if hasattr(scene, "model_dump")
                    else scene
                )
                for scene in custom_scenes
            ]
        else:
            scenes = split_script_into_scenes(script)

        if not scenes:
            raise ValueError("At least one scene is required.")

        # Generate the B-roll video. The generator copies uploaded
        # audio to narration.mp3 or creates narration from the script.
        generate_scene_video(
            scenes,
            str(video_path),
            input_audio_path=(
                str(input_audio_path)
                if input_audio_path
                else None
            ),
        )

        if not video_path.exists():
            raise FileNotFoundError(
                "Video generator did not create video.mp4"
            )

        # Generate subtitles if enabled.
        if data.get("captions", {}).get("srt", True):
            generate_srt(scenes, str(srt_path))

        duration = get_audio_duration(audio_path)

        # Estimate scene timings using word counts.
        word_counts = [
            max(1, len(scene["text"].split()))
            for scene in scenes
        ]

        total_words = sum(word_counts)
        estimated_total = duration

        timeline_scenes = []
        start_time = 0.0

        for index, scene in enumerate(scenes):
            scene_duration = (
                duration * word_counts[index] / total_words
                if duration is not None and total_words
                else None
            )

            end_time = (
                start_time + scene_duration
                if scene_duration is not None
                else None
            )

            timeline_scenes.append({
                "scene_number": index + 1,
                "text": scene["text"],
                "visual_hint": scene.get(
                    "visual_hint",
                    scene["text"],
                ),
                "start_time_seconds": (
                    round(start_time, 2)
                    if scene_duration is not None
                    else None
                ),
                "end_time_seconds": (
                    round(end_time, 2)
                    if end_time is not None
                    else None
                ),
                "timing_method": (
                    "estimated_by_word_count"
                    if duration is not None
                    else "unavailable_audio_duration"
                ),
                "footage_source": None,
                "licence": None,
                "attribution": None,
                "ai_generated": None,
                "warning": (
                    "Footage source and licence require verification."
                ),
            })

            if end_time is not None:
                start_time = end_time

        timeline_data = {
            "job_id": job_id,
            "total_duration_seconds": (
                round(estimated_total, 2)
                if estimated_total is not None
                else None
            ),
            "scenes": timeline_scenes,
            "warnings": [
                "Scene timings are estimates, not speech-aligned timestamps.",
                "Footage licence and attribution have not been automatically verified.",
            ],
        }

        write_json(timeline_path, timeline_data)

        # Record whether the video used uploaded audio, URL audio,
        # or generated narration.
        if data.get("_input_audio_path"):
            input_type = "uploaded_audio"
        elif data.get("audio_url"):
            input_type = "audio_url"
        else:
            input_type = "script"

        manifest_data = {
            "job_id": job_id,
            "status": "completed",
            "input_type": input_type,
            "voice_requested": data.get(
                "voice",
                "en-IN-female",
            ),
            "voice_used": (
                "uploaded_audio"
                if input_audio_path
                else "en-IN-NeerjaNeural"
            ),
            "output_files": {
                "video": "video.mp4",
                "audio": (
                    "narration.mp3"
                    if audio_path.exists()
                    else None
                ),
                "captions": (
                    "captions.srt"
                    if srt_path.exists()
                    else None
                ),
                "timeline": "timeline.json",
                "manifest": "manifest.json",
            },
            "video_settings": {
                "width": 1280,
                "height": 720,
                "frame_rate": 30,
                "video_codec": "H.264",
                "audio_codec": "AAC",
                "implemented_aspect_ratio": "16:9",
            },
            "generation": {
                "scene_count": len(scenes),
                "duration_seconds": (
                    round(duration, 2)
                    if duration is not None
                    else None
                ),
            },
            "warnings": [
                "Scene timings are estimated by word count.",
                "Footage licensing and attribution are not automatically verified.",
                "Only 16:9 output is implemented in this prototype.",
                "Model/provider version, cost and random seed are not tracked.",
            ],
        }

        write_json(manifest_path, manifest_data)

        jobs[job_id].update(
            status="completed",
            progress=100,
            updated_at=now(),
            message="Video and metadata files generated.",
            result={
                "video_path": str(video_path.resolve()),
                "audio_path": (
                    str(audio_path.resolve())
                    if audio_path.exists()
                    else None
                ),
                "captions_path": (
                    str(srt_path.resolve())
                    if srt_path.exists()
                    else None
                ),
                "timeline_path": str(timeline_path.resolve()),
                "manifest_path": str(manifest_path.resolve()),
            },
            input_summary={
                "has_uploaded_audio": bool(
                    data.get("_input_audio_path")
                ),
                "has_audio_url": bool(data.get("audio_url")),
                "has_script": bool(script),
                "scene_count": len(scenes),
                "aspect_ratios": data.get(
                    "aspect_ratios",
                    ["16:9"],
                ),
            },
        )

        log.info("Job completed: %s", job_id)

    except Exception as exc:
        log.exception(
            "Video generation failed for job %s",
            job_id,
        )

        jobs[job_id].update(
            status="failed",
            progress=100,
            updated_at=now(),
            message=f"Video generation failed: {exc}",
        )


@app.post("/api/v1/broll/jobs", status_code=202)
def create_job(
    payload: JobRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    x_correlation_id: str | None = Header(
        default=None,
        alias="X-Correlation-Id",
    ),
):
    if idempotency_key and idempotency_key in idempotency:
        job_id = idempotency[idempotency_key]

        return {
            "job_id": job_id,
            "status": jobs[job_id]["status"],
            "idempotent_replay": True,
        }

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "created_at": now(),
        "updated_at": now(),
        "correlation_id": x_correlation_id,
        "result": {},
        "warnings": [],
    }

    if idempotency_key:
        idempotency[idempotency_key] = job_id

    background_tasks.add_task(
        worker,
        job_id,
        payload.model_dump(mode="json"),
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/v1/jobs/{job_id}",
    }


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": "No job exists with this ID.",
                    "retryable": False,
                    "details": {},
                }
            },
        )

    return jobs[job_id]


@app.post("/api/v1/broll/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    """Save an audio file without starting video generation."""
    if not file.filename or not file.filename.lower().endswith(
        (".mp3", ".wav", ".m4a")
    ):
        raise HTTPException(
            status_code=400,
            detail="Upload an MP3, WAV, or M4A file.",
        )

    output_dir = OUTPUT_DIR / "audio_uploads"
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (
        f"{uuid.uuid4().hex}_"
        f"{Path(file.filename).name}"
    )
    saved_path = output_dir / safe_name

    try:
        contents = await file.read(MAX_AUDIO_SIZE + 1)

        if len(contents) > MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Audio file must be 25 MB or smaller.",
            )

        if not contents:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty.",
            )

        saved_path.write_bytes(contents)

        return {
            "message": "Audio uploaded successfully.",
            "filename": file.filename,
            "saved_path": str(saved_path),
        }

    finally:
        await file.close()


@app.post("/api/v1/broll/jobs/upload-audio", status_code=202)
async def create_job_with_uploaded_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    script: str | None = Form(default=None),
    scenes_json: str | None = Form(default=None),
):
    """Upload audio and immediately queue a video-generation job."""
    has_script = bool(script and script.strip())
    has_scenes = bool(scenes_json and scenes_json.strip())

    if has_script == has_scenes:
        raise HTTPException(
            status_code=400,
            detail="Provide either a script or scenes_json, not both.",
        )

    if not file.filename or not file.filename.lower().endswith(
        (".mp3", ".wav", ".m4a")
    ):
        raise HTTPException(
            status_code=400,
            detail="Upload an MP3, WAV, or M4A file.",
        )

    scenes = None

    if has_scenes:
        try:
            scenes = json.loads(scenes_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail="scenes_json must contain valid JSON.",
            ) from exc

        if not isinstance(scenes, list) or not scenes:
            raise HTTPException(
                status_code=400,
                detail="scenes_json must be a non-empty JSON list.",
            )

        for scene in scenes:
            if (
                not isinstance(scene, dict)
                or not isinstance(scene.get("text"), str)
                or not scene["text"].strip()
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Every scene must be an object "
                        "with non-empty text."
                    ),
                )

    job_id = str(uuid.uuid4())
    output_dir = OUTPUT_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = (
        output_dir
        / f"input_audio{Path(file.filename).suffix.lower()}"
    )

    try:
        contents = await file.read(MAX_AUDIO_SIZE + 1)

        if len(contents) > MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Audio file must be 25 MB or smaller.",
            )

        if not contents:
            raise HTTPException(
                status_code=400,
                detail="The uploaded audio is empty.",
            )

        audio_path.write_bytes(contents)

    finally:
        await file.close()

    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "created_at": now(),
        "updated_at": now(),
        "correlation_id": None,
        "result": {},
        "warnings": [],
    }

    data = {
        "_input_audio_path": str(audio_path),
        "script": script.strip() if has_script else "",
        "scenes": scenes,
        "captions": {"srt": True},
        "voice": "uploaded_audio",
        "aspect_ratios": ["16:9"],
    }

    background_tasks.add_task(
        worker,
        job_id,
        data,
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/v1/jobs/{job_id}",
    }
