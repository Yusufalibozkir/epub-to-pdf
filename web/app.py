"""FastAPI application for the Deluxe EPUB → PDF web UI."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipeline._config import load_config
from pipeline._models import Settings
from web.jobs import PROJECT_ROOT, manager

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Deluxe Interior", version="1.0.0")


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_config_path(name: str) -> Path:
    raw = (name or "").strip() or "A4.yaml"
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    root = PROJECT_ROOT.resolve()
    if path != root and root not in path.parents:
        raise HTTPException(status_code=400, detail="Config must live in the project folder")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config not found: {raw}")
    return path


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/configs")
def configs() -> dict[str, Any]:
    return {"configs": manager.list_configs()}


@app.get("/api/settings")
def settings_catalog(config: str = "A4.yaml") -> dict[str, Any]:
    """Return built-in defaults plus effective settings after loading a config file."""
    defaults = dataclasses.asdict(Settings())
    cfg_path = _resolve_config_path(config)
    effective = dataclasses.asdict(load_config(str(cfg_path), Settings()))
    fields = []
    for field in dataclasses.fields(Settings):
        name = field.name
        default_value = defaults[name]
        value = effective[name]
        fields.append(
            {
                "name": name,
                "type": getattr(field.type, "__name__", str(field.type)),
                "default": default_value,
                "value": value,
                "overridden": default_value != value,
            }
        )
    return {
        "config": cfg_path.name,
        "config_path": str(cfg_path),
        "count": len(fields),
        "fields": fields,
    }


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": manager.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    payload = job.to_dict()
    payload["qa_images"] = manager.qa_images(job_id)
    report = Path(job.artifact_dir) / "qa_report.txt" if job.artifact_dir else None
    payload["has_qa_report"] = bool(report and report.exists())
    payload["has_pdf"] = bool(job.out_pdf and Path(job.out_pdf).exists())
    summary = Path(job.artifact_dir) / "build_summary.json" if job.artifact_dir else None
    payload["has_build_summary"] = bool(summary and summary.exists())
    if summary and summary.exists():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            payload["effective_settings"] = data.get("settings") or {}
        except Exception:
            payload["effective_settings"] = {}
    return payload


@app.post("/api/jobs")
async def create_job(
    epub: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    config: str = Form("A4.yaml"),
    toc_mode: str = Form("simple"),
    volume_mode: str = Form("collection"),
    sample_pages: int = Form(50),
    section: str = Form(""),
    out_name: str = Form(""),
    use_openai: str = Form("false"),
    openai_qa: str = Form("false"),
    ai_provider: str = Form("openai"),
    keep_all_images: str = Form("false"),
    remove_all_images: str = Form("false"),
    strict: str = Form("false"),
    debug_html: str = Form("true"),
) -> dict[str, Any]:
    if not epub.filename:
        raise HTTPException(status_code=400, detail="EPUB filename required")
    data = await epub.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    path = manager.save_upload(epub.filename, data)

    options = {
        "title": title.strip(),
        "author": author.strip(),
        "config": config.strip() or "A4.yaml",
        "toc_mode": toc_mode.strip() or "simple",
        "volume_mode": volume_mode.strip() or "auto",
        "sample_pages": max(0, int(sample_pages or 0)),
        "section": section.strip(),
        "out_name": out_name.strip(),
        "use_openai": _as_bool(use_openai),
        "openai_qa": _as_bool(openai_qa),
        "ai_provider": ai_provider.strip() or "openai",
        "keep_all_images": _as_bool(keep_all_images),
        "remove_all_images": _as_bool(remove_all_images),
        "strict": _as_bool(strict),
        "debug_html": _as_bool(debug_html),
    }
    job = manager.create_job(path, options)
    return job.to_dict()


@app.get("/api/jobs/{job_id}/pdf")
def download_pdf(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    pdf = Path(job.out_pdf) if job.out_pdf else None
    if pdf is None or not pdf.exists():
        raise HTTPException(status_code=404, detail="PDF not ready")
    return FileResponse(pdf, filename=pdf.name, media_type="application/pdf")


@app.get("/api/jobs/{job_id}/qa-report")
def download_qa_report(job_id: str):
    job = manager.get(job_id)
    if job is None or not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Job not found")
    report = Path(job.artifact_dir) / "qa_report.txt"
    if not report.exists():
        raise HTTPException(status_code=404, detail="QA report not found")
    return FileResponse(report, filename=f"{job.id}_qa_report.txt", media_type="text/plain")


@app.get("/api/jobs/{job_id}/qa/{filename}")
def qa_image(job_id: str, filename: str):
    job = manager.get(job_id)
    if job is None or not job.artifact_dir:
        raise HTTPException(status_code=404, detail="Job not found")
    safe = Path(filename).name
    path = Path(job.artifact_dir) / "qa" / safe
    if not path.exists() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


@app.get("/settings")
def settings_page():
    path = STATIC_DIR / "settings.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Settings page missing")
    return FileResponse(path)


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
