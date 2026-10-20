# IncuBrix PS3 — Script and Voiceover to B-roll Video Engine
## Status
Starter scaffold only. It implements FastAPI health/readiness, request validation, job submission/status, background-task lifecycle, and idempotency-key replay. It does NOT generate audio or video yet.
## Run locally (Windows PowerShell)
Python 3.12+ recommended.
```powershell
py -3.12 -m venv .venv
.venv\\Scripts\\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open API docs at http://127.0.0.1:8000/api/v1/docs
Run tests: `pytest -q`
Example POST `/api/v1/broll/jobs` JSON:
```json
{"script":"Saving money starts with a simple budget. Track your spending weekly.","aspect_ratios":["16:9","9:16"],"style":"bright, modern footage","avoid":["alcohol"],"captions":{"burn_in":true,"srt":true}}
```
The job will say `needs_pipeline` until the media pipeline is added.
## Remaining
Text-to-speech/audio input; scene timing; licensed footage adapters/fallback; MP4 rendering; captions.srt, timeline.json, manifest.json; robust progress/storage; full tests, batch runner, deployment and evidence.
