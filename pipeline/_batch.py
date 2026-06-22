"""
Batch conversion support for folder-based EPUB runs.
"""
from __future__ import annotations

import copy
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pipeline._config import apply_cli_overrides, load_config, resolve_toc_mode
from pipeline._models import Settings
from pipeline._utils import clean_display_title, clean_text, slugify


BuildSingle = Callable[[Any], None]


def _discover_epubs(folder: Path, pattern: str, recursive: bool) -> list[Path]:
    iterator = folder.rglob(pattern) if recursive else folder.glob(pattern)
    return sorted((p.resolve() for p in iterator if p.is_file()), key=lambda p: str(p).lower())


def _batch_output_name(epub: Path, used_names: dict[str, int]) -> str:
    base = slugify(epub.stem, "book")
    count = used_names.get(base, 0) + 1
    used_names[base] = count
    suffix = "" if count == 1 else f"-{count}"
    return f"{base}{suffix}.pdf"


def _batch_report_path(args: Any) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(args.artifacts_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"batch_report_{stamp}.json"


def _resolve_batch_output_dir(args: Any) -> Path:
    if getattr(args, "output_dir_was_explicit", False):
        return Path(args.output_dir).expanduser().resolve()
    if getattr(args, "out_was_explicit", False):
        out_dir = Path(args.out).expanduser()
        if out_dir.suffix.lower() == ".pdf":
            raise SystemExit("--out in batch mode must be a folder path, not a .pdf filename. Use --output-dir or a folder-like --out value.")
        return out_dir.resolve()
    return Path(args.output_dir).expanduser().resolve()


def _filename_title(epub: Path) -> str:
    return clean_display_title(clean_text(epub.stem.replace("_", " ").replace("-", " "))) or "Untitled"


def _clone_args_for_epub(args: Any, epub: Path, out_name: str, output_dir: Path, title_source: str) -> Any:
    item_args = copy.copy(args)
    item_args.epub = str(epub)
    item_args.out = out_name
    item_args.output_dir = str(output_dir)
    item_args.title = None if title_source == "metadata" else _filename_title(epub)
    item_args.batch = None
    item_args.out_was_explicit = False
    item_args.output_dir_was_explicit = False
    item_args.batch_title_source = title_source
    return item_args


def run_batch(args: Any, build_single: BuildSingle) -> None:
    """Run the existing single-book pipeline for every EPUB in a folder."""
    batch_dir = Path(args.batch).expanduser().resolve()
    if not batch_dir.exists() or not batch_dir.is_dir():
        raise SystemExit(f"Batch folder not found: {batch_dir}")

    preview_settings = Settings()
    load_config(args.config, preview_settings)
    apply_cli_overrides(preview_settings, args)
    resolve_toc_mode(preview_settings, prompt_if_auto=True)
    args.toc_mode = preview_settings.toc_mode

    epubs = _discover_epubs(batch_dir, args.batch_glob, args.recursive)
    if not epubs:
        raise SystemExit(f"No EPUB files matched {args.batch_glob!r} in {batch_dir}")

    output_dir = _resolve_batch_output_dir(args)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = _batch_report_path(args)

    print(f"Batch conversion: {len(epubs)} EPUB(s)")
    print(f"Input folder: {batch_dir}")
    print(f"Output folder: {output_dir}")
    print(f"Title source: {args.batch_title_source}")
    print(f"Volume mode: {getattr(args, 'volume_mode', None) or 'auto'}")
    print(f"On error: {args.on_error}")

    used_names: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for index, epub in enumerate(epubs, start=1):
        out_name = _batch_output_name(epub, used_names)
        out_pdf = output_dir / out_name
        result: dict[str, Any] = {
            "index": index,
            "input_epub": str(epub),
            "output_pdf": str(out_pdf),
            "status": "pending",
            "title_source": args.batch_title_source,
            "volume_mode": getattr(args, "volume_mode", None) or "auto",
        }

        if args.skip_existing and out_pdf.exists():
            result["status"] = "skipped"
            result["reason"] = "output already exists"
            results.append(result)
            print(f"[{index}/{len(epubs)}] SKIP {epub.name} -> {out_name} already exists")
            continue

        print(f"[{index}/{len(epubs)}] BUILD {epub.name} -> {out_name}")
        item_args = _clone_args_for_epub(args, epub, out_name, output_dir, args.batch_title_source)
        try:
            build_single(item_args)
            result["status"] = "succeeded"
        except KeyboardInterrupt:
            result["status"] = "failed"
            result["error"] = "Interrupted by user"
            results.append(result)
            raise
        except SystemExit as exc:
            result["status"] = "failed"
            result["error"] = str(exc) or f"SystemExit({exc.code})"
            result["traceback"] = traceback.format_exc()
            print(f"[{index}/{len(epubs)}] FAIL {epub.name}: {result['error']}")
            if args.on_error == "stop":
                results.append(result)
                break
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)
            result["traceback"] = traceback.format_exc()
            print(f"[{index}/{len(epubs)}] FAIL {epub.name}: {exc}")
            if args.on_error == "stop":
                results.append(result)
                break
        results.append(result)

    summary = {
        "batch_dir": str(batch_dir),
        "output_dir": str(output_dir),
        "recursive": bool(args.recursive),
        "batch_glob": args.batch_glob,
        "on_error": args.on_error,
        "skip_existing": bool(args.skip_existing),
        "batch_title_source": args.batch_title_source,
        "volume_mode": getattr(args, "volume_mode", None) or "auto",
        "total": len(epubs),
        "succeeded": sum(1 for r in results if r["status"] == "succeeded"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "Batch complete: "
        f"{summary['succeeded']} succeeded, {summary['skipped']} skipped, {summary['failed']} failed"
    )
    print(f"Wrote batch report: {report_path}")

    if summary["failed"]:
        raise SystemExit(f"Batch finished with {summary['failed']} failed book(s).")
