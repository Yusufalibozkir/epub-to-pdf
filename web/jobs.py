"""Background job runner for the Deluxe EPUB web UI."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = PROJECT_ROOT / "web_uploads"
JOBS_DIR = PROJECT_ROOT / "web_jobs"


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | succeeded | failed
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    options: dict[str, Any] = field(default_factory=dict)
    epub_name: str = ""
    epub_path: str = ""
    out_name: str = ""
    out_pdf: str = ""
    artifact_dir: str = ""
    log_lines: list[str] = field(default_factory=list)
    error: str = ""
    page_count: Optional[int] = None
    title: str = ""
    progress: float = 0.0
    stage: str = "Waiting"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "options": self.options,
            "epub_name": self.epub_name,
            "out_name": self.out_name,
            "out_pdf": self.out_pdf,
            "artifact_dir": self.artifact_dir,
            "error": self.error,
            "page_count": self.page_count,
            "title": self.title,
            "progress": self.progress,
            "stage": self.stage,
            "log_tail": self.log_lines[-80:],
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_dict() for j in jobs]

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def save_upload(self, filename: str, data: bytes) -> Path:
        safe = re.sub(r"[^\w.\-]+", "_", Path(filename).name).strip("._") or "book.epub"
        if not safe.lower().endswith(".epub"):
            safe += ".epub"
        dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe}"
        dest.write_bytes(data)
        return dest

    def create_job(self, epub_path: Path, options: dict[str, Any]) -> Job:
        job_id = uuid.uuid4().hex[:12]
        stem = re.sub(r"[^\w.\-]+", "_", Path(options.get("out_name") or epub_path.stem).stem)
        stem = stem.strip("._") or "print_ready"
        out_name = f"{stem}.pdf"
        job = Job(
            id=job_id,
            epub_name=epub_path.name,
            epub_path=str(epub_path),
            out_name=out_name,
            options=options,
            title=str(options.get("title") or ""),
        )
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
        thread.start()
        return job

    def _append(self, job: Job, line: str) -> None:
        text = line.rstrip()
        if not text:
            return
        job.log_lines.append(text)
        stage_match = re.search(r"\[\s*(\d+)\s*/\s*(\d+)\]\s*(.+?)(?:\.\.\.|…|$)", text)
        if stage_match:
            cur = int(stage_match.group(1))
            total = max(1, int(stage_match.group(2)))
            job.progress = min(0.95, cur / total)
            job.stage = stage_match.group(3).strip(" .")
        elif "Wrote PDF:" in text:
            job.progress = 1.0
            job.stage = "Complete"
        elif text.startswith("Auto-fix"):
            job.stage = text[:120]

    def _run_job(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = time.time()
        job.stage = "Starting pipeline"
        opts = job.options
        out_pdf = PROJECT_ROOT / "output" / job.out_name
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "deluxe_epub_to_pdf.py"),
            job.epub_path,
            "--out",
            job.out_name,
            "--output-dir",
            str(PROJECT_ROOT / "output"),
            "--artifacts-dir",
            str(PROJECT_ROOT / "artifacts"),
        ]
        if opts.get("debug_html", True):
            cmd.append("--debug-html")
        if opts.get("title"):
            cmd.extend(["--title", str(opts["title"])])
        if opts.get("author"):
            cmd.extend(["--author", str(opts["author"])])
        if opts.get("config"):
            cfg = Path(str(opts["config"]))
            if not cfg.is_absolute():
                cfg = PROJECT_ROOT / cfg
            cmd.extend(["--config", str(cfg)])
        if opts.get("toc_mode"):
            cmd.extend(["--toc-mode", str(opts["toc_mode"])])
        if opts.get("volume_mode"):
            cmd.extend(["--volume-mode", str(opts["volume_mode"])])
        sample_pages = int(opts.get("sample_pages") or 0)
        if sample_pages > 0:
            cmd.extend(["--sample-pages", str(sample_pages)])
        else:
            cmd.append("--full-without-sample")
        if opts.get("section"):
            cmd.extend(["--section", str(opts["section"])])
        if opts.get("use_openai"):
            cmd.append("--use-openai")
        if opts.get("openai_qa"):
            cmd.append("--openai-qa")
        if opts.get("ai_provider"):
            cmd.extend(["--ai-provider", str(opts["ai_provider"])])
        if opts.get("keep_all_images"):
            cmd.append("--keep-all-images")
        if opts.get("remove_all_images"):
            cmd.append("--remove-all-images")
        if opts.get("strict"):
            cmd.append("--strict")

        job.log_lines.append("Command: " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self._append(job, line)
            code = proc.wait()
            job.out_pdf = str(out_pdf)
            artifact = PROJECT_ROOT / "artifacts" / Path(job.out_name).stem
            job.artifact_dir = str(artifact)
            summary = artifact / "build_summary.json"
            if summary.exists():
                try:
                    data = json.loads(summary.read_text(encoding="utf-8"))
                    job.page_count = data.get("page_count")
                    job.title = data.get("title") or job.title
                except Exception:
                    pass
            if code != 0:
                job.status = "failed"
                job.error = f"Pipeline exited with code {code}"
                job.stage = "Failed"
            elif not out_pdf.exists():
                job.status = "failed"
                job.error = "Pipeline finished but PDF was not found"
                job.stage = "Failed"
            else:
                job.status = "succeeded"
                job.progress = 1.0
                job.stage = "Complete"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.stage = "Failed"
            job.log_lines.append(f"ERROR: {exc}")
        finally:
            job.finished_at = time.time()

    def list_configs(self) -> list[dict[str, str]]:
        configs: list[dict[str, str]] = []
        for path in sorted(PROJECT_ROOT.glob("*.yaml")):
            if path.name.startswith("."):
                continue
            configs.append({"name": path.name, "path": path.name})
        example = PROJECT_ROOT / "deluxe_config.example.yaml"
        if example.exists() and not any(c["name"] == example.name for c in configs):
            configs.append({"name": example.name, "path": example.name})
        return configs

    def qa_images(self, job_id: str) -> list[str]:
        job = self.get(job_id)
        if not job or not job.artifact_dir:
            return []
        qa_dir = Path(job.artifact_dir) / "qa"
        if not qa_dir.exists():
            return []
        files = sorted(qa_dir.glob("page_*.png")) + sorted(qa_dir.glob("review_*.png"))
        return [f.name for f in files]


manager = JobManager()
