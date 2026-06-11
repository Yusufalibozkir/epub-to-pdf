"""
Build orchestrator: pipeline entry point, auto-fix engine, and high-level build logic.

This module ties together all the other pipeline modules. build_once() executes a
single build pass; build_pipeline() manages configuration, rule packs, auto-fix
iterations, and final output. auto_fix_settings() reads QA verdict flags and
applies safe CSS/config adjustments.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

import re

from pipeline._ai import (
    add_text_qa_flag,
    add_visual_flag,
    ai_text_issue_lines,
    ai_text_qa,
    apply_openai_book_plan,
    has_visual_feedback,
    openai_visual_issue_lines,
    openai_visual_qa,
    require_ai_client,
    require_openai_client,
)
from pipeline._classify import infer_title_with_source, read_epub, scan_spine_items
from pipeline._cleaners import clean_document, fragment_is_title_only, sample_word_budget
from pipeline._config import (
    apply_cli_overrides,
    load_config,
    resolve_artifact_dir,
    resolve_output_pdf,
    write_default_config,
)
from pipeline._css import compose_html
from pipeline._fonts import prepare_embedded_fonts
from pipeline._models import BuildLog, QAVerdict, Settings, SpineDoc, TocEntry
from pipeline._render import (
    draw_vector_runner_rules,
    optimize_pdf,
    preflight_pdf,
    render_pdf,
    resolve_toc_page_numbers,
    subset_pdf,
    write_build,
)
from pipeline._rule_packs import apply_rule_packs
from pipeline._utils import clean_display_title, clean_text, copy_assets, visible_word_count

# Import new modules
from pipeline._cache import PipelineCache
from pipeline._plugins import (
    apply_plugin_regex_patterns,
    discover_plugins,
    run_plugin_cleaners,
    run_plugin_classifiers,
    run_plugin_post_processors,
    run_plugin_qa_checks,
)
from pipeline._dag import PipelineContext, PipelineDAG, Stage


# ======================================================================================
# Single build pass (DAG-based with caching and plugin support)
# ======================================================================================


def build_once(
    epub_path: Path,
    out_pdf: Path,
    artifact_dir: Path,
    settings: Settings,
    args: Any,
    log: BuildLog,
) -> tuple[QAVerdict, Path, Path, Path]:
    """Execute one complete build pass: scan → clean → render → QA.

    Uses the DAG executor with content-addressed caching for the expensive
    document-cleaning phase. Plugin hooks are called at each stage.

    Returns (verdict, qa_json_path, qa_txt_path, build_dir).
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    build_dir = artifact_dir / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    # Set up cache (unless disabled)
    cache: Optional[PipelineCache] = None
    if not getattr(args, "no_cache", False):
        cache = PipelineCache(artifact_dir)

    # Set up DAG context
    ctx = PipelineContext(
        settings=settings,
        log=log,
        args=args,
        cache=cache,
        epub_path=epub_path,
        out_pdf=out_pdf,
        artifact_dir=artifact_dir,
        build_dir=build_dir,
    )

    # Create and execute the DAG
    dag = _create_pipeline_dag()
    try:
        outputs = dag.run(ctx)
    except Exception:
        if not args.debug_html and build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        raise

    verdict: QAVerdict = outputs.get("verdict")
    qa_json: Path = outputs.get("qa_json")
    qa_txt: Path = outputs.get("qa_txt")
    return verdict, qa_json, qa_txt, build_dir


# ======================================================================================
# DAG stage definitions
# ======================================================================================


def _create_pipeline_dag() -> PipelineDAG:
    """Build the processing DAG with all stages wired together."""
    dag = PipelineDAG()

    dag.add_stage(Stage("resolve_title", runner=_run_resolve_title, cache_key=_ck_resolve_title, description="Infer book title from EPUB metadata"))
    dag.add_stage(Stage("read_assets", depends_on=["resolve_title"], runner=_run_read_assets, cache_key=_ck_read_assets, description="Read EPUB and extract images"))
    dag.add_stage(Stage("scan_classify", depends_on=["read_assets"], runner=_run_scan_classify, cache_key=_ck_scan_classify, description="Scan and classify spine documents"))
    dag.add_stage(Stage("ai_plan", depends_on=["scan_classify"], runner=_run_ai_plan, cache_key=_ck_ai_plan, description="AI structure planning (optional)"))
    dag.add_stage(Stage("prepare_fonts", depends_on=["read_assets"], runner=_run_prepare_fonts, cache_key=_ck_prepare_fonts, description="Copy fonts for embedding"))
    dag.add_stage(Stage("clean_documents", depends_on=["ai_plan", "prepare_fonts"], runner=_run_clean_documents, cache_key=_ck_clean_documents, description="Clean and normalize all documents"))
    dag.add_stage(Stage("compose_render", depends_on=["clean_documents"], runner=_run_compose_render, cache_key=_ck_compose_render, description="Assemble HTML + CSS and render PDF"))
    dag.add_stage(Stage("resolve_toc", depends_on=["compose_render"], runner=_run_resolve_toc, cache_key=None, description="Resolve TOC page numbers"))
    dag.add_stage(Stage("post_process", depends_on=["resolve_toc"], runner=_run_post_process, cache_key=None, description="Vector rules, subset, optimize"))
    dag.add_stage(Stage("run_qa", depends_on=["post_process"], runner=_run_qa, cache_key=_ck_qa, description="PDF preflight QA"))
    dag.add_stage(Stage("ai_text_qa", depends_on=["run_qa"], runner=_run_ai_text_qa, cache_key=None, description="AI text QA (optional)"))
    dag.add_stage(Stage("ai_visual_qa", depends_on=["run_qa"], runner=_run_ai_visual_qa, cache_key=None, description="AI visual QA (optional)"))
    return dag


# --------------------------------------------------------------------------------------
# Cache key generators
# --------------------------------------------------------------------------------------


def _ck_resolve_title(ctx: PipelineContext) -> Optional[str]:
    epub_path: Path = ctx.epub_path
    if not epub_path.exists():
        return None
    return PipelineCache.hash_combined(
        PipelineCache.hash_file(epub_path),
        PipelineCache.hash_text(str(ctx.args.title or "")),
        PipelineCache.hash_text(str(ctx.settings.title)),
    )


def _ck_read_assets(ctx: PipelineContext) -> Optional[str]:
    epub_path: Path = ctx.epub_path
    if not epub_path.exists():
        return None
    return PipelineCache.hash_combined(
        PipelineCache.hash_file(epub_path),
        PipelineCache.hash_object({"title": ctx.data.get("book_title", "")}),
    )


def _ck_scan_classify(ctx: PipelineContext) -> Optional[str]:
    epub_path: Path = ctx.epub_path
    if not epub_path.exists():
        return None
    # Scanning depends only on EPUB content + rule packs (which are settings-driven)
    return PipelineCache.hash_combined(
        PipelineCache.hash_file(epub_path),
        PipelineCache.hash_text(str(ctx.settings.rule_packs)),
    )


def _ck_ai_plan(ctx: PipelineContext) -> Optional[str]:
    if not ctx.args.use_openai:
        return None
    return PipelineCache.hash_combined(
        ctx.data.get("_scan_hash", ""),
        PipelineCache.hash_text(str(ctx.args.ai_provider)),
        PipelineCache.hash_text(str(ctx.args.openai_model)),
        PipelineCache.hash_text(str(ctx.args.deepseek_model)),
        PipelineCache.hash_text(str(ctx.data.get("book_title", ""))),
    )


def _ck_prepare_fonts(ctx: PipelineContext) -> Optional[str]:
    return PipelineCache.hash_combined(
        PipelineCache.hash_text(str(ctx.settings.embed_font_files)),
        PipelineCache.hash_text(str(ctx.settings.font_dir)),
        PipelineCache.hash_text(str(ctx.settings.embedded_font_regular)),
        PipelineCache.hash_text(str(ctx.settings.embedded_font_italic)),
    )


def _ck_clean_documents(ctx: PipelineContext) -> Optional[str]:
    """Cache per-document cleaning. The key includes settings that affect cleaning."""
    settings_hash = PipelineCache.hash_object({
        "image_policy": ctx.settings.image_policy,
        "smart_punctuation": ctx.settings.smart_punctuation,
        "footnote_handling": ctx.settings.footnote_handling,
        "rule_packs": ctx.settings.rule_packs,
        "title": ctx.settings.title,
        "major_opener_blank_before": ctx.settings.major_opener_blank_before,
        "major_opener_blank_after": ctx.settings.major_opener_blank_after,
    })
    docs_hash = ctx.data.get("_docs_hash", "")
    return PipelineCache.hash_combined(docs_hash, settings_hash)


def _ck_compose_render(ctx: PipelineContext) -> Optional[str]:
    """Cache the rendered PDF if nothing has changed since the clean phase."""
    clean_hash = ctx.data.get("_clean_hash", "")
    css_hash = PipelineCache.hash_object(dataclasses.asdict(ctx.settings))
    return PipelineCache.hash_combined(clean_hash, css_hash)


def _ck_qa(ctx: PipelineContext) -> Optional[str]:
    pdf_hash = ctx.data.get("_pdf_hash", "")
    return PipelineCache.hash_combined(
        pdf_hash,
        PipelineCache.hash_text(str(ctx.settings.runner_font_pt)),
        PipelineCache.hash_text(str(ctx.settings.runner_body_clearance_mm)),
    )


# --------------------------------------------------------------------------------------
# Stage runners
# --------------------------------------------------------------------------------------


def _run_resolve_title(ctx: PipelineContext) -> dict:
    """Infer book title from EPUB metadata, config, or CLI."""
    settings: Settings = ctx.settings
    args = ctx.args
    log = ctx.log
    epub_path: Path = ctx.epub_path
    book, items = read_epub(epub_path)

    if args.title is not None and clean_text(args.title):
        settings.title = clean_display_title(args.title)
        log.title_source = "command line --title"
    elif clean_text(settings.title):
        settings.title = clean_display_title(settings.title)
        if not log.title_source:
            log.title_source = "config title"
    else:
        settings.title, log.title_source = infer_title_with_source(book, epub_path.stem)

    return {"book": book, "items": items, "book_title": settings.title}


def _run_read_assets(ctx: PipelineContext) -> dict:
    """Extract images and prepare font assets."""
    build_dir: Path = ctx.build_dir
    book = ctx.data["book"]
    log = ctx.log
    src_map = copy_assets(book, build_dir, log)
    return {"src_map": src_map}


def _run_scan_classify(ctx: PipelineContext) -> dict:
    """Scan spine items and run heuristic (and plugin) classification."""
    items = ctx.data["items"]
    log = ctx.log
    docs = scan_spine_items(items)

    # Plugin classifiers
    for doc in docs:
        run_plugin_classifiers(doc)

    # Compute a hash for cache chaining
    doc_hashes = [PipelineCache.hash_bytes(d.raw[:4096]) for d in docs]
    docs_hash = PipelineCache.hash_combined(*doc_hashes)
    return {"docs": docs, "_docs_hash": docs_hash}


def _run_ai_plan(ctx: PipelineContext) -> dict:
    """Optional AI structure planning."""
    args = ctx.args
    log = ctx.log
    docs = ctx.data["docs"]
    settings = ctx.settings

    ai_provider = args.ai_provider
    ai_model = args.deepseek_model if ai_provider == "deepseek" else args.openai_model
    needs_primary_ai = ai_provider != "none" and (args.use_openai or args.openai_image_check)
    ai_client = require_ai_client(ai_provider) if needs_primary_ai else None

    if args.use_openai and ai_client is not None:
        apply_openai_book_plan(ai_client, ai_model, docs, settings.title, log, provider=ai_provider)

    # Store AI config for later stages
    return {"docs": docs, "_ai_client": ai_client, "_ai_model": ai_model, "_ai_provider": ai_provider}


def _run_prepare_fonts(ctx: PipelineContext) -> dict:
    """Prepare embedded font CSS."""
    build_dir: Path = ctx.build_dir
    settings = ctx.settings
    log = ctx.log
    font_face_css = prepare_embedded_fonts(build_dir, settings, log)
    return {"font_face_css": font_face_css}


def _run_clean_documents(ctx: PipelineContext) -> dict:
    """Clean and normalize all documents. Plugin cleaners are called per-doc."""
    from bs4 import BeautifulSoup as _BS

    docs = ctx.data["docs"]
    src_map = ctx.data["src_map"]
    settings = ctx.settings
    args = ctx.args
    log = ctx.log
    build_dir: Path = ctx.build_dir

    ai_client = ctx.data.get("_ai_client")
    ai_model = ctx.data.get("_ai_model", "gpt-5.4-mini")
    ai_provider = ctx.data.get("_ai_provider", "openai")

    toc: list[TocEntry] = []
    used_ids: set[str] = set()
    fragments: list[str] = []
    current_work: Optional[str] = None
    current_division: Optional[str] = None
    sample_budget = sample_word_budget(args.sample_pages)
    sample_words = 0

    cache: Optional[PipelineCache] = ctx.cache
    clean_hash_parts: list[str] = []
    settings_hash = PipelineCache.hash_object({
        "image_policy": settings.image_policy,
        "smart_punctuation": settings.smart_punctuation,
        "title": settings.title,
        "major_opener_blank_before": settings.major_opener_blank_before,
        "major_opener_blank_after": settings.major_opener_blank_after,
    })

    total_docs = len(docs)
    doc_print_interval = max(1, total_docs // 100)  # print ~100 updates max
    for doc_idx, doc in enumerate(docs, start=1):
        if sample_budget and sample_words >= sample_budget:
            log.warn(f"Sample mode stopped body collection after about {sample_words} words before PDF layout.")
            break

        # Show per-document progress (throttled to ~100 prints)
        if doc_idx % doc_print_interval == 0 or doc_idx == 1:
            print(f"  doc {doc_idx}/{total_docs}... ", file=sys.stderr, end="", flush=True)

        if doc.remove:
            log.removed_documents.append(f"{doc.index} {doc.href} kind={doc.kind} {doc.notes}")
            continue

        # --- Per-document cache check ---
        doc_cache_key: Optional[str] = None
        cached_frag_data: Optional[tuple] = None
        if cache is not None:
            doc_key = PipelineCache.hash_combined(
                PipelineCache.hash_bytes(doc.raw[:8192]),
                settings_hash,
                str(doc.index),
            )
            doc_cache_key = doc_key
            cached = cache.load("doc_clean", doc_key)
            if cached is not None and isinstance(cached, (list, tuple)) and len(cached) >= 4:
                cached_frag_data = tuple(cached)  # type: ignore

        if cached_frag_data is not None:
            # Restore from cache
            frag, cw, cd = cached_frag_data[0], cached_frag_data[1], cached_frag_data[2]
            current_work = cw if cw else current_work
            current_division = cd if cd else current_division
            # Rebuild TOC entries from cached fragment
            # (TOC is re-derived from headings in the fragment, so we need to re-parse)
        else:
            # Run the full cleanup
            frag, current_work, current_division = clean_document(
                doc, src_map, settings, toc, used_ids,
                current_work, current_division, log,
                ai_client=(ai_client if args.openai_image_check else None),
                ai_model=ai_model, ai_provider=ai_provider,
            )

            # Plugin cleaners
            soup = _BS(frag, "lxml")
            run_plugin_cleaners(soup, settings, log)
            frag = str(soup)

            # Cache cleaned fragment
            if cache is not None and doc_cache_key is not None:
                cache_data = (frag, current_work, current_division, doc.index)
                try:
                    cache.store("doc_clean", doc_cache_key, cache_data)
                except Exception:
                    pass

        if fragment_is_title_only(frag, settings):
            log.removed_documents.append(f"{doc.index} {doc.href} skipped as duplicate/empty title-only fragment")
            continue

        if clean_text(_BS(frag, "lxml").get_text(" ")) or "<img" in frag or "<table" in frag:
            fragments.append(frag)
            sample_words += visible_word_count(_BS(frag, "lxml").get_text(" "))
            clean_hash_parts.append(doc_cache_key or str(doc.index))

    # Clear per-doc progress line
    print(f"  doc {total_docs}/{total_docs} done", file=sys.stderr)

    if not fragments:
        raise SystemExit("No usable body content remained after cleanup. Retry with --keep-all-images or without --use-openai.")

    clean_hash = PipelineCache.hash_combined(*clean_hash_parts) if clean_hash_parts else ""
    return {
        "fragments": fragments,
        "toc": toc,
        "_current_work": current_work,
        "_current_division": current_division,
        "_clean_hash": clean_hash,
    }


def _run_compose_render(ctx: PipelineContext) -> dict:
    """Compose HTML + CSS and render the PDF."""
    settings = ctx.settings
    fragments = ctx.data["fragments"]
    toc = ctx.data["toc"]
    font_face_css = ctx.data.get("font_face_css", "")
    build_dir: Path = ctx.build_dir
    out_pdf: Path = ctx.out_pdf
    log = ctx.log

    html_doc, css = compose_html(settings, fragments, toc, font_face_css=font_face_css)
    write_build(build_dir, html_doc, css)
    render_pdf(build_dir, out_pdf)

    return {"html_doc": html_doc, "css": css, "_pdf_first_pass": True}


def _run_resolve_toc(ctx: PipelineContext) -> dict:
    """Resolve TOC page numbers and re-render if successful."""
    settings = ctx.settings
    fragments = ctx.data["fragments"]
    toc = ctx.data["toc"]
    font_face_css = ctx.data.get("font_face_css", "")
    build_dir: Path = ctx.build_dir
    out_pdf: Path = ctx.out_pdf
    log = ctx.log
    html_doc = ctx.data.get("html_doc", "")
    css = ctx.data.get("css", "")

    toc_page_numbers = resolve_toc_page_numbers(out_pdf, toc, log)
    if toc_page_numbers:
        html_doc, css = compose_html(settings, fragments, toc, toc_page_numbers=toc_page_numbers, font_face_css=font_face_css)
        write_build(build_dir, html_doc, css)
        render_pdf(build_dir, out_pdf)
    elif toc:
        log.warn("Could not resolve explicit TOC page numbers; leaving renderer-generated TOC counters in place.")

    # Compute PDF hash for downstream caching
    pdf_hash = PipelineCache.hash_file(out_pdf) if out_pdf.exists() else ""
    return {
        "html_doc": html_doc,
        "css": css,
        "_toc_page_numbers": toc_page_numbers,
        "_pdf_hash": pdf_hash,
    }


def _run_post_process(ctx: PipelineContext) -> dict:
    """Post-process: subset, vector rules, optimize, plugin post-processors."""
    settings = ctx.settings
    args = ctx.args
    log = ctx.log
    out_pdf: Path = ctx.out_pdf

    if args.sample_pages and args.sample_pages > 0:
        subset_pdf(out_pdf, args.sample_pages)
    draw_vector_runner_rules(out_pdf, settings)
    if not args.no_optimize:
        optimize_pdf(out_pdf)

    # Plugin post-processors
    run_plugin_post_processors(out_pdf, settings, log)

    return {}


def _run_qa(ctx: PipelineContext) -> dict:
    """Run QA preflight checks."""
    out_pdf: Path = ctx.out_pdf
    artifact_dir: Path = ctx.artifact_dir
    log = ctx.log
    settings = ctx.settings
    args = ctx.args

    verdict, qa_json, qa_txt = preflight_pdf(
        out_pdf, artifact_dir, log, settings=settings, render_pngs=not args.no_qa_render
    )
    return {"verdict": verdict, "qa_json": qa_json, "qa_txt": qa_txt}


def _run_ai_text_qa(ctx: PipelineContext) -> dict:
    """Run optional AI text QA (DeepSeek)."""
    args = ctx.args
    log = ctx.log
    out_pdf: Path = ctx.out_pdf
    artifact_dir: Path = ctx.artifact_dir
    settings = ctx.settings
    verdict: QAVerdict = ctx.data.get("verdict")
    qa_json: Path = ctx.data.get("qa_json")
    qa_txt: Path = ctx.data.get("qa_txt")

    ai_provider = args.ai_provider
    ai_model = args.deepseek_model if ai_provider == "deepseek" else args.openai_model
    needs_text_qa = ai_provider == "deepseek" and not args.no_text_qa
    ai_client = require_ai_client(ai_provider) if needs_text_qa else None

    if needs_text_qa and ai_client is not None:
        try:
            # Compute work-start pages from TOC for smarter sampling
            extra_pages: list[int] = []
            toc = ctx.data.get("toc", [])
            toc_page_numbers = ctx.data.get("_toc_page_numbers", {})
            if toc_page_numbers:
                import fitz
                from pipeline._render import find_first_body_page_index as _find_body
                try:
                    _doc = fitz.open(out_pdf)
                    _fb = _find_body(_doc)
                    if _fb is not None:
                        for _entry in toc:
                            if _entry.kind in ("work", "division", "backmatter"):
                                _folio = toc_page_numbers.get(_entry.target_id)
                                if _folio is not None and _folio > 0:
                                    _idx = _fb + _folio - 1
                                    if _idx >= args.ai_qa_pages:
                                        extra_pages.append(_idx)
                    _doc.close()
                except Exception:
                    pass

            text_report, suggestions_path = ai_text_qa(
                ai_client, ai_model, ai_provider, out_pdf, qa_json, qa_txt, artifact_dir, settings, log,
                max_pages=args.ai_qa_pages, extra_pages=extra_pages,
            )
            text = text_report.read_text(encoding="utf-8", errors="ignore")
            issue_lines = ai_text_issue_lines(text)
            verdict.text_qa_issue_lines = issue_lines[:80]
            if re.search(r"FINAL\s*:\s*FAIL\b|\bFAIL\b|\bISSUE\b", text, re.I):
                add_text_qa_flag(verdict, f"{ai_provider.title()} text QA flagged issues; inspect {text_report.name}.")
            if suggestions_path:
                verdict.ai_rule_suggestion_file = str(suggestions_path)
                add_text_qa_flag(verdict, f"{ai_provider.title()} suggested regex rules for review: {suggestions_path.name}.")
            qa_json.write_text(json.dumps(dataclasses.asdict(verdict), ensure_ascii=False, indent=2), encoding="utf-8")
            if verdict.text_qa_flags:
                with qa_txt.open("a", encoding="utf-8") as f:
                    f.write(f"\n\n{ai_provider.title()} text QA flags after local QA:\n")
                    for flag in verdict.text_qa_flags:
                        f.write(f"- {flag}\n")
                    if verdict.text_qa_issue_lines:
                        f.write(f"\n{ai_provider.title()} text QA issue lines used for auto-fix decisions:\n")
                        for line in verdict.text_qa_issue_lines[:40]:
                            f.write(f"- {line}\n")
        except Exception as exc:
            log.warn(f"{ai_provider.title()} text QA failed: {exc}")

    return {"verdict": verdict, "qa_json": qa_json, "qa_txt": qa_txt}


def _run_ai_visual_qa(ctx: PipelineContext) -> dict:
    """Run optional AI visual QA (OpenAI vision)."""
    args = ctx.args
    log = ctx.log
    out_pdf: Path = ctx.out_pdf
    artifact_dir: Path = ctx.artifact_dir
    verdict: QAVerdict = ctx.data.get("verdict")
    qa_json: Path = ctx.data.get("qa_json")
    qa_txt: Path = ctx.data.get("qa_txt")

    visual_client = require_openai_client() if args.openai_qa else None

    if args.openai_qa and visual_client is not None:
        visual_report = openai_visual_qa(
            visual_client, args.openai_model, out_pdf, qa_json, artifact_dir / "qa", args.openai_qa_pages
        )
        try:
            visual_text = visual_report.read_text(encoding="utf-8", errors="ignore")
            issue_lines = openai_visual_issue_lines(visual_text)
            verdict.openai_visual_issue_lines = issue_lines[:80]
            issue_text = "\n".join(issue_lines)
            if re.search(r"FINAL\s*:\s*FAIL\b|\bFAIL\b", visual_text, re.I):
                add_visual_flag(verdict, "OpenAI visual QA returned FAIL; inspect openai_visual_qa.txt.")
            _categorize_visual_findings(verdict, issue_text)
            qa_json.write_text(json.dumps(dataclasses.asdict(verdict), ensure_ascii=False, indent=2), encoding="utf-8")
            if verdict.openai_visual_flags:
                with qa_txt.open("a", encoding="utf-8") as f:
                    f.write("\n\nOpenAI visual QA flags after render:\n")
                    for flag in verdict.openai_visual_flags:
                        f.write(f"- {flag}\n")
                    if verdict.openai_visual_issue_lines:
                        f.write("\nOpenAI visual QA issue lines used for auto-fix decisions:\n")
                        for line in verdict.openai_visual_issue_lines[:40]:
                            f.write(f"- {line}\n")
        except Exception as exc:
            log.warn(f"Could not parse OpenAI visual QA report: {exc}")

    return {"verdict": verdict, "qa_json": qa_json, "qa_txt": qa_txt}


def _categorize_visual_findings(verdict: QAVerdict, issue_text: str) -> None:
    """Categorize visual QA findings into structured flags."""
    if re.search(r"header|runner|running head|rule|collision|crowd", issue_text, re.I):
        verdict.possible_header_collisions.append(
            {"page": -1, "issue": "OpenAI visual QA flagged possible running-head/header-rule issue"}
        )
        add_visual_flag(verdict, "OpenAI visual QA flagged possible running-head/header-rule issue.")
    if re.search(
        r"justif|ragged|word spacing|river|paragraph|indent|line spill|single-letter|broken word|narrow|overwide",
        issue_text,
        re.I,
    ):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible body-typography/justification issue.")
    if re.search(r"chapter|title|heading|opener|stranded", issue_text, re.I):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible chapter/title placement issue.")
    if re.search(r"TOC|contents|leader|duplicate|page number", issue_text, re.I):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible TOC/page-number issue.")
    if re.search(r"folio|page number|roman|arabic|numbering", issue_text, re.I):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible folio/page-numbering issue.")
    if re.search(r"image|caption|portrait|plate|illustration|cropped|oversized", issue_text, re.I):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible image/caption cleanup issue.")
    if re.search(r"poetry|verse|drama|cast|stage direction|blockquote|letter", issue_text, re.I):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible poetry/drama/special-form issue.")
    if re.search(r"blank|empty|title-only|dark|black|artifact|raw ebook|blue|underlined|hyperlink|browser", issue_text, re.I):
        add_visual_flag(verdict, "OpenAI visual QA flagged possible blank/artifact/raw-ebook issue.")


# ======================================================================================
# Auto-fix engine
# ======================================================================================


def bump_float_setting(
    settings: Settings, attr: str, delta: float, maximum: float, log: BuildLog, reason: str
) -> bool:
    old_value = float(getattr(settings, attr))
    new_value = min(maximum, round(old_value + delta, 3))
    if new_value <= old_value + 0.0001:
        return False
    setattr(settings, attr, new_value)
    log.css_auto_fixes.append(f"{reason}: {attr} {old_value:g} -> {new_value:g}.")
    return True


def set_bool_setting(settings: Settings, attr: str, value: bool, log: BuildLog, reason: str) -> bool:
    old_value = bool(getattr(settings, attr))
    if old_value == value:
        return False
    setattr(settings, attr, value)
    log.css_auto_fixes.append(f"{reason}: {attr} {old_value} -> {value}.")
    return True


def auto_fix_settings(settings: Settings, verdict: QAVerdict, log: BuildLog) -> bool:
    """Read QA verdict flags and apply safe CSS/config adjustments.

    Returns True if any setting was changed.
    """
    changed = False
    if verdict.possible_header_collisions:
        changed |= bump_float_setting(
            settings,
            "runner_body_clearance_mm",
            2.0,
            14.0,
            log,
            "Increased runner/body clearance after local/AI header-collision warning",
        )
    if has_visual_feedback(verdict, r"body-typography|justif|ragged|word spacing|river|paragraph|indent|line spill|single-letter|broken word|narrow|overwide|crowd"):
        changed |= set_bool_setting(settings, "justify_prose", True, log, "Enabled prose justification after AI body-typography warning")
        changed |= set_bool_setting(settings, "hyphenate", True, log, "Enabled hyphenation after AI body-typography warning")
        changed |= bump_float_setting(settings, "line_height", 0.025, 1.32, log, "Opened body line-height after AI body-typography warning")
    if has_visual_feedback(verdict, r"chapter/title|chapter title|work title|major work|chapter|heading|opener|stranded|too close"):
        changed |= bump_float_setting(
            settings, "subdivision_margin_bottom_mm", 1.2, 8.0, log,
            "Increased chapter-title bottom spacing after AI title-placement warning",
        )
        changed |= bump_float_setting(
            settings, "major_opener_bottom_margin_mm", 2.0, 22.0, log,
            "Increased major-opener bottom spacing after AI title-placement warning",
        )
    if has_visual_feedback(verdict, r"\bTOC\b|contents|leader|duplicate|page number|page-number"):
        changed |= bump_float_setting(settings, "toc_line_height", 0.02, 1.2, log, "Opened TOC line-height after AI TOC/page-number warning")
        changed |= bump_float_setting(settings, "toc_entry_gap_mm", 0.4, 4.8, log, "Opened TOC entry spacing after AI TOC/page-number warning")
    # Non-auto-fixable issues (just log for review)
    if has_visual_feedback(verdict, r"folio|page number|roman|arabic|numbering"):
        log.warn("AI QA flagged folio/page-numbering. This is reported for review; no safe generic auto-fix was applied.")
    if has_visual_feedback(verdict, r"image|caption|portrait|plate|illustration|cropped|oversized"):
        log.warn("AI QA flagged image/caption cleanup. This is reported for review; no safe generic auto-fix was applied.")
    if has_visual_feedback(verdict, r"poetry|verse|drama|cast|stage direction|blockquote|letter"):
        log.warn("AI QA flagged poetry/drama/special-form layout. This is reported for review; no safe generic auto-fix was applied.")
    if has_visual_feedback(verdict, r"blank|empty|title-only|dark|black|artifact|raw ebook|blue|underlined|hyperlink|browser"):
        log.warn("AI QA flagged blank/artifact/raw-ebook styling. This is reported for review; no safe generic auto-fix was applied.")
    if verdict.ai_rule_suggestion_file:
        log.warn(
            f"AI suggested regex cleanup rules for review: {verdict.ai_rule_suggestion_file}. "
            f"Reviewed rules can improve the next run after being added to rule_packs."
        )
    if verdict.possible_narrow_columns:
        log.warn("Narrow columns detected after normalization; inspect generated HTML around those pages.")
    return changed


# ======================================================================================
# Main pipeline entry point
# ======================================================================================


def build_pipeline(args: Any) -> None:
    """High-level pipeline entry point: config → build → auto-fix → output."""
    if getattr(args, "write_default_config", None):
        write_default_config(args.write_default_config)
        return

    epub_path = Path(args.epub).expanduser().resolve()
    if not epub_path.exists():
        raise SystemExit(f"EPUB not found: {epub_path}")
    out_pdf = resolve_output_pdf(args)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir = resolve_artifact_dir(args, out_pdf)

    # Precedence: built-in defaults → config → CLI overrides
    settings = Settings()
    settings = load_config(args.config, settings)
    settings = apply_cli_overrides(settings, args)

    if not settings.no_sample_requirement and not args.sample_pages and not args.full_without_sample:
        print(
            "WARNING: Your production rule says sample-first. Use --sample-pages 50 for review, "
            "or --full-without-sample to intentionally render the full book.",
            file=sys.stderr,
        )

    log = BuildLog()
    apply_rule_packs(settings, log)

    # Plugin discovery and integration
    plugin_dir = Path("plugins").expanduser().resolve()
    if plugin_dir.exists():
        discovered = discover_plugins(plugin_dir, log)
        if discovered:
            log.warn(f"[Plugins] Discovered {len(discovered)} plugin(s): {', '.join(discovered)}")
    apply_plugin_regex_patterns(settings, log)

    final_verdict: Optional[QAVerdict] = None
    qa_json = qa_txt = build_dir = None
    for pass_no in range(args.max_auto_fix_passes + 1):
        if pass_no > 0:
            print(f"Auto-fix pass {pass_no}: regenerating PDF with adjusted CSS settings...")
        final_verdict, qa_json, qa_txt, build_dir = build_once(
            epub_path, out_pdf, artifact_dir, settings, args, log
        )
        if pass_no >= args.max_auto_fix_passes:
            break
        fix_count_before = len(log.css_auto_fixes)
        if not auto_fix_settings(settings, final_verdict, log):
            break
        for fix_note in log.css_auto_fixes[fix_count_before:]:
            print(f"Auto-fix queued: {fix_note}")

    assert final_verdict is not None and qa_json is not None and qa_txt is not None and build_dir is not None

    # Write build summary
    build_summary = artifact_dir / "build_summary.json"
    build_summary.write_text(
        json.dumps(
            {
                "output_pdf": str(out_pdf),
                "artifact_dir": str(artifact_dir),
                "title": settings.title,
                "title_source": log.title_source,
                "page_count": final_verdict.page_count,
                "settings": dataclasses.asdict(settings),
                "settings_precedence": "built-in defaults -> config file -> explicit CLI flags",
                "qa_blockers": final_verdict.has_blockers,
                "hard_failures": log.hard_failures,
                "warnings": log.warnings,
                "css_auto_fixes": log.css_auto_fixes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Print summary
    print(f"Wrote PDF: {out_pdf}")
    if log.title_source:
        print(f"Book title ({log.title_source}): {settings.title}")
    print(f"Wrote QA report: {qa_txt}")
    print(f"Wrote QA verdict JSON: {qa_json}")
    print(f"Wrote build summary: {build_summary}")
    if not args.no_qa_render:
        print(f"Wrote QA page renders: {artifact_dir / 'qa'}")
    if args.debug_html:
        print(f"Kept HTML build folder: {build_dir}")
    elif build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)

    if args.strict and (final_verdict.has_blockers or log.hard_failures):
        raise SystemExit(
            f"Strict mode failed: delivery-blocking QA warnings remain. "
            f"Inspect {qa_txt} and {artifact_dir / 'qa'} renders."
        )
