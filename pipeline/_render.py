"""
PDF rendering, optimization, preflight QA, and post-render vector drawing.

All functions that operate on rendered PDF pages live here: WeasyPrint rendering,
pikepdf optimization, PyMuPDF-based preflight checks (page size, header clearance,
line spills, dark pages, TOC sanity, etc.), and the vector runner-rule drawing pass.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from pipeline import _constants as C
from pipeline._models import BuildLog, QAVerdict, Settings
from pipeline._utils import clean_text, normalized_title_key, visible_word_count


# ======================================================================================
# BUILD / RENDER
# ======================================================================================


def write_build(build_dir: Path, html_doc: str, css: str) -> None:
    (build_dir / "book.html").write_text(html_doc, encoding="utf-8")
    (build_dir / "style.css").write_text(css, encoding="utf-8")


def render_pdf(build_dir: Path, out_pdf: Path) -> None:
    from weasyprint import CSS, HTML

    try:
        HTML(filename=str(build_dir / "book.html"), base_url=str(build_dir)).write_pdf(
            str(out_pdf), stylesheets=[CSS(filename=str(build_dir / "style.css"))]
        )
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write PDF '{out_pdf}'. Close it in any PDF viewer/preview pane, "
            "or choose a different --out path."
        ) from exc


# ======================================================================================
# PDF OPTIMIZATION
# ======================================================================================


def optimize_pdf(path: Path) -> None:
    try:
        import pikepdf
    except Exception:
        return
    tmp = path.with_suffix(".optimized.tmp.pdf")
    with pikepdf.open(path) as pdf:
        pdf.save(
            tmp,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            linearize=True,
        )
    tmp.replace(path)


def subset_pdf(path: Path, pages: int) -> None:
    if pages <= 0:
        return
    import fitz

    doc = fitz.open(path)
    if doc.page_count <= pages:
        doc.close()
        return
    out = fitz.open()
    out.insert_pdf(doc, from_page=0, to_page=pages - 1)
    tmp = path.with_suffix(".sample.tmp.pdf")
    out.save(tmp, garbage=4, deflate=True)
    out.close()
    doc.close()
    tmp.replace(path)


# ======================================================================================
# VECTOR RUNNER RULE DRAWING (post-render)
# ======================================================================================


def parse_hex_color(value: str) -> tuple[float, float, float]:
    text = clean_text(value).lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (0.0, 0.0, 0.0)
    try:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return (0.0, 0.0, 0.0)


def page_has_running_head(page, settings: Settings) -> bool:
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox") or [0, 0, 0, 0]
            y = float(bbox[1])
            if y > 95:
                continue
            spans = line.get("spans", [])
            text = clean_text("".join(span.get("text", "") for span in spans))
            if not text:
                continue
            if re.fullmatch(r"[ivxlcdm]+|\d+", text, flags=re.I):
                continue
            sizes = [float(span.get("size", 0) or 0) for span in spans if clean_text(span.get("text", ""))]
            if sizes and max(sizes) > settings.runner_font_pt + 1.2:
                continue
            if visible_word_count(text) <= 14:
                return True
    return False


def find_first_body_page_index(doc) -> Optional[int]:
    """Return the zero-based page index where Arabic body folio 1 is visible."""
    toc_indices = [
        i
        for i in range(min(doc.page_count, 30))
        if re.search(r"\bCONTENTS\b|\bContents\b", doc[i].get_text("text"))
    ]
    start = (max(toc_indices) + 1) if toc_indices else 0
    for i in range(start, min(doc.page_count, start + 12)):
        data = doc[i].get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                text = clean_text("".join(span.get("text", "") for span in line.get("spans", [])))
                bbox = line.get("bbox") or [0, 0, 0, 0]
                if text == "1" and float(bbox[1]) > 760:
                    return i
    return None


def draw_vector_runner_rules(pdf_path: Path, settings: Settings) -> None:
    """Draw safe full-width runner rules as explicit stroke-only vector paths."""
    if settings.runner_layout.strip().lower() != "right_title_full_rule":
        return
    if settings.runner_rule_style.strip().lower() != "full_width":
        return
    try:
        import fitz
    except Exception:
        return

    doc = fitz.open(pdf_path)
    first_body = find_first_body_page_index(doc) or 0
    color = parse_hex_color(settings.runner_rule_color)
    y = settings.runner_rule_y_mm * C.PT_PER_MM
    x0 = settings.margin_side_mm * C.PT_PER_MM
    x1 = C.A4_WIDTH_PT - x0
    changed = False
    try:
        for i in range(first_body, doc.page_count):
            page = doc[i]
            if not page_has_running_head(page, settings):
                continue
            shape = page.new_shape()
            shape.draw_line(fitz.Point(x0, y), fitz.Point(x1, y))
            shape.finish(color=color, fill=None, width=settings.runner_rule_weight_pt, closePath=False)
            shape.commit()
            changed = True
        if changed:
            tmp = pdf_path.with_suffix(".runner.tmp.pdf")
            doc.save(tmp, garbage=4, deflate=True)
            doc.close()
            tmp.replace(pdf_path)
        else:
            doc.close()
    except Exception:
        doc.close()
        raise


# ======================================================================================
# TOC PAGE NUMBER RESOLUTION
# ======================================================================================


def resolve_toc_page_numbers(
    pdf_path: Path, toc: list[Any], log: BuildLog
) -> dict[str, int]:
    """Resolve TOC links to Arabic body page numbers after the first render pass."""
    if not toc:
        return {}
    try:
        import fitz

        doc = fitz.open(pdf_path)
    except Exception as exc:
        log.warn(f"Could not open first-pass PDF for TOC page-number resolution: {exc}")
        return {}

    wanted = {e.target_id for e in toc}
    target_pages: dict[str, int] = {}
    link_rows: list[tuple[int, float, float, dict[str, Any]]] = []
    try:
        for page_index in range(doc.page_count):
            page_text = doc[page_index].get_text("text")
            if not re.search(r"\bCONTENTS\b|\bContents\b", page_text):
                if link_rows:
                    break
                continue
            for link in doc[page_index].get_links():
                rect = link.get("from")
                y = float(rect.y0) if rect else 0.0
                x = float(rect.x0) if rect else 0.0
                link_rows.append((page_index, y, x, link))

        for _, _, _, link in sorted(link_rows, key=lambda row: (row[0], row[1], row[2])):
            dest = clean_text(str(link.get("nameddest") or link.get("id") or ""))
            target_page = link.get("page")
            if not dest or dest not in wanted or not isinstance(target_page, int):
                continue
            target_pages.setdefault(dest, target_page)

        if not target_pages:
            return {}

        first_body = find_first_body_page_index(doc)
        if first_body is None:
            first_body = min(target_pages.values())
            log.warn(
                "Could not detect visible body folio 1; using earliest TOC target as body page 1."
            )

        resolved = {
            target_id: max(1, page_index - first_body + 1)
            for target_id, page_index in target_pages.items()
        }
        return resolved
    finally:
        try:
            doc.close()
        except Exception:
            pass


# ======================================================================================
# LINE ANALYSIS HELPERS
# ======================================================================================


def line_text_from_page(page) -> list[str]:
    data = page.get_text("dict")
    lines: list[str] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            txt = "".join(span.get("text", "") for span in line.get("spans", []))
            txt = clean_text(txt)
            if txt:
                lines.append(txt)
    return lines


def looks_like_bad_spill(line: str) -> bool:
    s = line.strip().strip(".,;:!?()[]{}'\"\u201c\u201d\u2018\u2019\u2014\u2013-")
    if not s:
        return False
    if C.ROMAN_RE.match(s) or s.isdigit():
        return False
    if re.fullmatch(r"[bcdefghjklmnopqrstuvwxyz]", s):
        return True
    if re.match(r"^[a-z](-)?$", s):
        return True
    return False


def render_selected_pages(
    pdf_path: Path, qa_dir: Path, prefix: str = "page", max_pages: int = 12, jpg: bool = False
) -> list[Path]:
    """Render selected PDF pages as PNG/JPG images for visual QA."""
    import fitz

    qa_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    candidates: list[int] = []
    if doc.page_count:
        seed = [0, 1, 2, 3, 4, 5, 8, 12, 20, 30, 50, 100, doc.page_count - 1]
        for x in seed:
            if 0 <= x < doc.page_count and x not in candidates:
                candidates.append(x)
            if len(candidates) >= max_pages:
                break
    out: list[Path] = []
    for i in candidates:
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        suffix = "jpg" if jpg else "png"
        path = qa_dir / f"{prefix}_{i+1:04d}.{suffix}"
        if jpg:
            pix.save(str(path), jpg_quality=82)
        else:
            pix.save(str(path))
        out.append(path)
    doc.close()
    return out


# ======================================================================================
# HEADER / WORK-DESCRIPTION / NARROW-COLUMN ANALYSIS
# ======================================================================================


def analyze_header_clearance(
    page, page_no: int, settings: Optional[Settings] = None
) -> Optional[dict[str, Any]]:
    data = page.get_text("dict")
    y_values: list[float] = []
    title_key = normalized_title_key(settings.title if settings else "")
    runner_font_limit = (settings.runner_font_pt + 0.8) if settings else 10.5
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = clean_text("".join(span.get("text", "") for span in spans))
            if not text:
                continue
            bbox = line.get("bbox") or [0, 0, 0, 0]
            y = float(bbox[1])
            span_sizes = [float(span.get("size", 0) or 0) for span in spans if clean_text(span.get("text", ""))]
            if y < 60:
                continue
            if y < 135 and span_sizes and max(span_sizes) <= runner_font_limit:
                continue
            if y < 135 and title_key and normalized_title_key(text) == title_key:
                continue
            if y > 790:
                continue
            y_values.append(y)
    if not y_values:
        return None
    first = min(y_values)
    if settings:
        min_first = (settings.runner_rule_y_mm + settings.runner_body_clearance_mm + 1.5) * C.PT_PER_MM
    else:
        min_first = 74.0
    if first < min_first:
        return {
            "page": page_no,
            "first_text_y_pt": round(first, 2),
            "issue": "body text may be too close to running head/rule",
        }
    return None


def _looks_like_editorial_description_line(text: str) -> bool:
    s = clean_text(text)
    if visible_word_count(s) < 8:
        return False
    if not s[:1].isupper():
        return False
    if not re.search(
        r"\b(Dostoevsky|Dostoyevsky|published|publication|novel|novella|story|tale|poem|play|work|"
        r"author|translator|editor|unfinished|fragment|prologue|exile|arrest|Siberia)\b",
        s,
        re.I,
    ):
        return False
    return bool(
        re.match(
            r"^(?:"
            r"A\s+(?:sketch|note|account|fragment)\b.{0,140}\b(?:author|translator|editor|published|exile|arrest|execution|Siberia)\b|"
            r"[A-Z][\w'':\-\s]{1,80}\s+(?:was|were|is|are)\s+(?:first\s+)?(?:published|written|composed|inspired|a|an)\b|"
            r"The\s+(?:novel|novella|story|tale|poem|play|work)\b.{0,140}\b(?:was|is|deals|concerns|features|published|written|inspired)\b|"
            r"This\s+(?:novel|novella|story|tale|poem|play|work)\b.{0,140}\b(?:was|is|deals|concerns|features|published|written|inspired)\b"
            r")",
            s,
        )
    )


def analyze_work_description_style(
    page, page_no: int, settings: Optional[Settings] = None
) -> list[dict[str, Any]]:
    if settings is None:
        return []
    expected_size = max(6.0, settings.body_size_pt + settings.work_description_font_delta_pt)
    warnings_out: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = clean_text("".join(span.get("text", "") for span in line.get("spans", [])))
            if not _looks_like_editorial_description_line(text):
                continue
            bbox = line.get("bbox") or [0, 0, 0, 0]
            if float(bbox[1]) > 220:
                continue
            spans = [span for span in line.get("spans", []) if clean_text(span.get("text", ""))]
            if not spans:
                continue
            sizes = [float(span.get("size", 0) or 0) for span in spans]
            fonts = [str(span.get("font", "")) for span in spans]
            italic = any("italic" in font.lower() or "ital" in font.lower() for font in fonts)
            small_enough = bool(sizes) and max(sizes) <= expected_size + 0.25
            if not italic or not small_enough:
                warnings_out.append(
                    {
                        "page": page_no,
                        "text": text[:180],
                        "fonts": sorted(set(fonts)),
                        "sizes_pt": sorted({round(x, 2) for x in sizes}),
                        "expected_max_size_pt": round(expected_size + 0.25, 2),
                        "issue": "editorial work description should be italic and smaller than body text",
                    }
                )
    return warnings_out


def analyze_narrow_columns(page, page_no: int) -> Optional[dict[str, Any]]:
    data = page.get_text("dict")
    widths = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox") or [0, 0, 0, 0]
        w = float(bbox[2]) - float(bbox[0])
        y0 = float(bbox[1])
        if 80 < y0 < 760 and w > 20:
            widths.append(w)
    if not widths:
        return None
    median = sorted(widths)[len(widths) // 2]
    if median < 300:
        text = clean_text(page.get_text("text"))[:200]
        if not re.search(r"dramatis|characters|act |scene |contents", text, re.I):
            return {
                "page": page_no,
                "median_text_block_width_pt": round(median, 1),
                "issue": "possible narrow ebook column",
            }
    return None


def analyze_page_balance(page, page_no: int, settings) -> Optional[dict[str, Any]]:
    """Flag pages that end a chapter with very few lines (orphan pages)."""
    text = clean_text(page.get_text("text"))
    if not text:
        return None
    # Only check pages past page 10 that have very few lines
    lines = line_text_from_page(page)
    # Skip opener/display pages (few words, mostly uppercase)
    meaningful = [
        l for l in lines
        if len(l.strip()) > 3 and not l.strip().isupper()
    ]
    if page_no > 10 and len(meaningful) <= 3 and len(meaningful) > 0:
        return {
            "page": page_no,
            "lines": len(meaningful),
            "issue": "possible orphan page (very few body lines; may be a weak chapter ending)",
        }
    return None


def analyze_widows_orphans(page, page_no: int, settings) -> list[dict[str, Any]]:
    """Detect widows (single line at top of page continuing from previous) and
    orphans (single line stranded at bottom of page)."""
    findings: list[dict[str, Any]] = []
    lines = line_text_from_page(page)
    if len(lines) < 3:
        return findings

    # Widow: first body line is short and follows from previous page
    # (weak heuristic: first meaningful line is short and doesn't start with capital)
    for i, line in enumerate(lines):
        s = line.strip()
        if len(s) < 15 and i < 3 and page_no > 1:
            # Check it's not a heading, folio, or standard short line
            if not re.match(r"^[IVXLCDM]+$|\d+$|^(CHAPTER|PART|ACT|SCENE)\b", s, re.I):
                findings.append({
                    "page": page_no,
                    "line": s[:80],
                    "line_no": i + 1,
                    "issue": "possible widow (short line at page top continuing from previous page)",
                })
            break

    # Orphan: last text line at page bottom that starts a new paragraph
    # (single line at bottom before a page break)
    if len(lines) >= 2:
        last_line = lines[-1].strip()
        second_last = lines[-2].strip()
        if (
            last_line
            and len(last_line) > 3
            and len(lines) >= 2
            and not re.match(r"^[IVXLCDM]+$|\d+$|^(CHAPTER|PART|ACT|SCENE)\b", last_line, re.I)
        ):
            # If the last 1-2 lines of the page are the end of a paragraph
            findings.append({
                "page": page_no,
                "line": last_line[:80],
                "issue": "possible orphan (last line at page bottom; may strand alone at top of next page)",
            })

    return findings


# ======================================================================================
# STRANDED-HEADING DETECTION
# ======================================================================================


def analyze_stranded_headings(page, prev_page, page_no: int, settings) -> Optional[dict[str, Any]]:
    """Detect if a page ends with a chapter/section heading with no following body text.

    A heading stranded at the page bottom (with no lines of text after it) creates
    an awkward layout. The fix is CSS ``break-after: avoid-page`` + ``widows: 3``,
    but this QA check catches cases the renderer couldn't avoid.
    """
    lines = line_text_from_page(page)
    if len(lines) < 3:
        return None

    # Check the last 3 lines of the page for heading-like text
    tail = [l.strip() for l in lines[-4:] if l.strip()]
    heading_pattern = re.compile(
        r"^(CHAPTER|PART|SECTION|ACT|SCENE|BOOK|CANTO|EPILOGUE|PROLOGUE|APPENDIX|"
        r"INTRODUCTION|PREFACE|FOREWORD)\b",
        re.I,
    )
    for line in tail:
        if heading_pattern.match(line):
            # Check if this page is followed by body text (not a heading page)
            return {
                "page": page_no,
                "line": line[:100],
                "issue": "heading stranded at page bottom with no following body text",
            }
    return None


# ======================================================================================
# PREFLIGHT QA
# ======================================================================================


def preflight_pdf(
    pdf_path: Path,
    out_dir: Path,
    log: BuildLog,
    settings: Optional[Settings] = None,
    render_pngs: bool = True,
) -> tuple[QAVerdict, Path, Path]:
    """Run the full QA preflight on a rendered PDF. Returns (verdict, qa_json_path, qa_txt_path)."""
    import fitz

    doc = fitz.open(pdf_path)
    verdict = QAVerdict(page_count=doc.page_count)
    first_body_index = find_first_body_page_index(doc)
    title_key = normalized_title_key(settings.title if settings else "")

    # A4 page-size check
    for i, page in enumerate(doc):
        rect = page.rect
        if abs(rect.width - C.A4_WIDTH_PT) > 2 or abs(rect.height - C.A4_HEIGHT_PT) > 2:
            verdict.non_a4_pages.append(
                {
                    "page": i + 1,
                    "width_pt": round(rect.width, 2),
                    "height_pt": round(rect.height, 2),
                }
            )

    # Font / image inventory
    fonts = set()
    images_seen = 0
    for i in range(doc.page_count):
        try:
            for f in doc.get_page_fonts(i):
                fonts.add(str(f[3]))
        except Exception:
            pass
        try:
            page = doc[i]
            for img in doc.get_page_images(i):
                xref = img[0]
                if page.get_image_rects(xref):
                    images_seen += 1
        except Exception:
            pass
    verdict.fonts_seen = sorted(fonts)
    verdict.images_seen = images_seen
    if settings and settings.embed_font_files and settings.embedded_font_family:
        expected_font_key = re.sub(r"[^a-z0-9]+", "", settings.embedded_font_family.lower())
        seen_font_keys = [re.sub(r"[^a-z0-9]+", "", name.lower()) for name in verdict.fonts_seen]
        if expected_font_key and not any(expected_font_key in key for key in seen_font_keys):
            verdict.font_embedding_warnings.append(
                f"Expected embedded font family '{settings.embedded_font_family}' was not detected in final PDF fonts."
            )

    # Line spills, header clearance, narrow columns, work descriptions
    for i, page in enumerate(doc):
        lines = line_text_from_page(page)
        bad = [x for x in lines if looks_like_bad_spill(x)]
        if bad:
            verdict.possible_line_spills.append({"page": i + 1, "lines": bad[:8]})
        text = clean_text(page.get_text("text"))
        filename_hits = sorted(
            set(re.findall(r"\b(?:img|image|pic|fig|figure)[_\- ]?\d+\.(?:jpe?g|png|gif|webp)\b", text, flags=re.I))
        )
        if filename_hits:
            verdict.visible_image_filename_artifacts.append(
                {
                    "page": i + 1,
                    "filenames": filename_hits[:12],
                    "issue": "visible raw image filename text rendered in PDF",
                }
            )
        front_or_display = (
            (first_body_index is not None and i < first_body_index)
            or bool(re.search(r"\bCONTENTS\b|\bContents\b", text))
            or (title_key and visible_word_count(text) <= 14 and normalized_title_key(text).startswith(title_key))
        )
        if not front_or_display:
            hc = analyze_header_clearance(page, i + 1, settings)
            if hc:
                verdict.possible_header_collisions.append(hc)
            nc = analyze_narrow_columns(page, i + 1)
            if nc:
                verdict.possible_narrow_columns.append(nc)
            pb = analyze_page_balance(page, i + 1, settings)
            if pb:
                verdict.possible_orphan_pages.append(pb)
            verdict.possible_widow_lines.extend(analyze_widows_orphans(page, i + 1, settings))
            if i > 0:
                sh = analyze_stranded_headings(page, doc[i - 1], i + 1, settings)
                if sh:
                    verdict.possible_widow_lines.append(sh)
            verdict.work_description_style_warnings.extend(
                analyze_work_description_style(page, i + 1, settings)
            )

        drawings = page.get_drawings()
        visible_image_count = 0
        for img in page.get_images(full=True):
            try:
                if page.get_image_rects(img[0]):
                    visible_image_count += 1
            except Exception:
                pass
        if not text and (drawings or visible_image_count):
            verdict.possible_blank_page_artifacts.append(
                {
                    "page": i + 1,
                    "drawings": len(drawings),
                    "images": visible_image_count,
                    "issue": "blank-looking page contains graphical objects",
                }
            )

    # Empty/title-only content pages
    for i, page in enumerate(doc):
        if first_body_index is not None and i < first_body_index:
            continue
        text = clean_text(page.get_text("text"))
        if not text:
            continue
        words = visible_word_count(text)
        key = normalized_title_key(text)
        if i >= 4 and words <= 12 and title_key and (key == title_key or key.startswith(title_key)):
            verdict.empty_content_pages.append(
                {
                    "page": i + 1,
                    "text": text[:180],
                    "issue": "page contains only title/running-head/folio-like text",
                }
            )

    # Duplicate title pages
    if title_key:
        title_like_pages: list[int] = []
        scan_limit = first_body_index if first_body_index is not None else min(doc.page_count, 12)
        for i in range(min(doc.page_count, scan_limit)):
            text = clean_text(doc[i].get_text("text"))
            if not text or re.search(r"\bCONTENTS\b|\bContents\b", text):
                continue
            key = normalized_title_key(text)
            if visible_word_count(text) <= 14 and (key == title_key or key.startswith(title_key)):
                title_like_pages.append(i + 1)
        if len(title_like_pages) > 1:
            verdict.duplicate_title_page_warnings.append(
                "Opening matter appears to contain duplicate generated title/half-title pages: "
                + ", ".join(str(x) for x in title_like_pages)
            )

    # Opener/page warnings
    for i, page in enumerate(doc):
        lines = line_text_from_page(page)
        if not lines:
            continue
        text = clean_text("\n".join(lines))
        if re.search(r"\bCONTENTS\b|\bContents\b", text):
            continue
        trailing_folio = bool(re.fullmatch(r"\d+|[ivxlcdm]+", lines[-1].strip(), flags=re.I))
        title_lines = lines[:-1] if trailing_folio else lines
        title_text = clean_text(" ".join(title_lines))
        if not title_text:
            continue
        if title_key and normalized_title_key(title_text).startswith(title_key):
            continue
        if visible_word_count(title_text) > 8:
            continue
        if len(title_lines) > 3:
            continue
        titleish = (
            title_text.isupper()
            or bool(re.fullmatch(r"[A-Z0-9][A-Z0-9'':\-\s,;.]+", title_text))
            or (visible_word_count(title_text) <= 4 and title_text[:1].isupper())
        )
        if not titleish:
            continue
        if trailing_folio:
            verdict.opener_page_warnings.append(
                {
                    "page": i + 1,
                    "text": text[:180],
                    "issue": "opener/display title page appears to carry a printed page number",
                }
            )
        if not trailing_folio:
            prev_text = clean_text(doc[i - 1].get_text("text")) if i > 0 else ""
            next_text = clean_text(doc[i + 1].get_text("text")) if i + 1 < doc.page_count else ""
            if prev_text:
                verdict.opener_page_warnings.append(
                    {
                        "page": i + 1,
                        "text": title_text[:180],
                        "issue": "opener/display title page is not preceded by a blank page",
                    }
                )
            if next_text:
                verdict.opener_page_warnings.append(
                    {
                        "page": i + 1,
                        "text": title_text[:180],
                        "issue": "opener/display title page is not followed by a blank page",
                    }
                )

    # TOC duplicate/uniformity checks
    toc_text_parts: list[str] = []
    in_toc = False
    toc_scan_limit = first_body_index if first_body_index is not None else min(doc.page_count, 20)
    for i in range(min(doc.page_count, toc_scan_limit)):
        page_text = doc[i].get_text("text")
        if re.search(r"\bCONTENTS\b|\bContents\b", page_text):
            in_toc = True
        if in_toc:
            toc_text_parts.append(page_text)
            if i > 0 and len(toc_text_parts) >= 4:
                break
    toc_text = "\n".join(toc_text_parts)
    if toc_text:
        toc_lines = [clean_text(x) for x in toc_text.splitlines() if clean_text(x)]
        normalized_lines = [
            normalized_title_key(re.sub(r"\.{2,}\s*\d+\s*$", "", x)) for x in toc_lines
        ]
        counts: dict[str, int] = {}
        for k in normalized_lines:
            if k and k not in {"contents"}:
                counts[k] = counts.get(k, 0) + 1
        dupes = {k: v for k, v in counts.items() if v >= 4}
        if dupes:
            verdict.toc_duplicate_warnings.append(
                "TOC appears to contain repeated duplicate entries: " + json.dumps(dupes, ensure_ascii=False)
            )
        page_nums = re.findall(r"\.{2,}\s*(\d+)\s*$", toc_text, flags=re.M)
        if len(page_nums) >= 8 and len(set(page_nums)) <= 2:
            verdict.toc_page_number_warnings.append(
                "Many TOC entries resolve to the same page number; inspect target ids and body page-counter reset."
            )

    # First body folio check
    toc_page_indices = [
        i
        for i in range(min(doc.page_count, 20))
        if re.search(r"\bCONTENTS\b|\bContents\b", doc[i].get_text("text"))
    ]
    if toc_page_indices:
        start = max(toc_page_indices) + 1
        for j in range(start, min(doc.page_count, start + 6)):
            lines = line_text_from_page(doc[j])
            meaningful = [x for x in lines if not re.fullmatch(r"[ivxlcdm]+|\d+", x.strip(), flags=re.I)]
            if not meaningful:
                continue
            trailing_nums = [x.strip() for x in lines[-4:] if re.fullmatch(r"\d+", x.strip())]
            if trailing_nums and trailing_nums[-1] != "1":
                verdict.first_body_folio_warnings.append(
                    f"First apparent body page is physical page {j+1} but printed folio appears to be {trailing_nums[-1]}, not 1."
                )
            break

    # Basic TOC page number sanity
    first_pages_text = "\n".join(doc[i].get_text("text") for i in range(min(doc.page_count, 12)))
    if "Contents" in first_pages_text and not re.search(r"\.{2,}\s*\d+", first_pages_text):
        verdict.toc_page_number_warnings.append(
            "Could not detect dot leaders/page numbers in early Contents text extraction; visually inspect TOC."
        )

    # Dark-page check
    qa_dir = out_dir / "qa"
    if render_pngs:
        paths = render_selected_pages(pdf_path, qa_dir, max_pages=16)
        verdict.qa_renders = [str(p) for p in paths]
        for p in paths:
            try:
                from PIL import Image

                im = Image.open(p).convert("L")
                sample = im.resize((64, 90))
                data = (
                    sample.get_flattened_data()
                    if hasattr(sample, "get_flattened_data")
                    else sample.getdata()
                )
                vals = list(data)
                avg = sum(vals) / len(vals)
                if avg < 45:
                    m = re.search(r"_(\d+)\.png$", p.name)
                    verdict.dark_pages.append(int(m.group(1)) if m else -1)
            except Exception:
                pass

    # Build reports
    removed_documents = list(dict.fromkeys(log.removed_documents))
    removed_blocks = list(dict.fromkeys(log.removed_blocks))
    kept_images = list(dict.fromkeys(log.kept_images))
    removed_images = list(dict.fromkeys(log.removed_images))
    ai_decisions = list(dict.fromkeys(log.ai_decisions))
    warnings_seen = list(dict.fromkeys(log.warnings))
    hard_failures = list(dict.fromkeys(log.hard_failures))

    qa_json = out_dir / "qa_verdict.json"
    qa_json.write_text(
        json.dumps(dataclass_to_dict(verdict), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report_lines = [
        f"PDF: {pdf_path.name}",
        f"Pages: {verdict.page_count}",
        f"A4 pages: {'OK' if not verdict.non_a4_pages else 'WARN'}",
        "Fonts seen: " + (", ".join(verdict.fonts_seen) if verdict.fonts_seen else "not detected"),
        f"Images seen in final PDF: {verdict.images_seen}",
        f"Removed documents: {len(removed_documents)}",
        f"Removed blocks: {len(removed_blocks)}",
        f"Removed local mini-TOCs: {log.local_tocs_removed}",
        f"Detected poetry blocks/sequences: {log.detected_poetry_blocks}/{log.detected_poetry_sequences}",
        f"Detected cast headings / normalized entries: {log.detected_cast_sections}/{log.normalized_cast_entries}",
        f"Kept images / removed images: {len(kept_images)}/{len(removed_images)}",
        f"Typographic text-node fixes: {log.typographic_fixes}",
        "",
        "Delivery blockers: " + ("YES" if verdict.has_blockers or log.hard_failures else "NO"),
    ]
    _append_list(report_lines, "Font embedding warnings", verdict.font_embedding_warnings)
    _append_section(report_lines, "Non-A4 pages", verdict.non_a4_pages)
    _append_section(report_lines, "Possible header/rule collisions", verdict.possible_header_collisions)
    _append_section(report_lines, "Possible broken word/single-letter line spills", verdict.possible_line_spills)
    _append_section(report_lines, "Possible narrow columns", verdict.possible_narrow_columns)
    _append_section(report_lines, "Visible raw image filename artifacts", verdict.visible_image_filename_artifacts)
    _append_section(report_lines, "Possible blank-page artifacts", verdict.possible_blank_page_artifacts)
    _append_list(report_lines, "TOC page-number warnings", verdict.toc_page_number_warnings)
    _append_list(report_lines, "TOC duplicate warnings", verdict.toc_duplicate_warnings)
    _append_section(report_lines, "Empty/title-only content pages", verdict.empty_content_pages)
    _append_list(report_lines, "Duplicate title-page warnings", verdict.duplicate_title_page_warnings)
    _append_section(report_lines, "Opener/display-page warnings", verdict.opener_page_warnings)
    _append_section(report_lines, "Work-description style warnings", verdict.work_description_style_warnings)
    _append_list(report_lines, "Body folio warnings", verdict.first_body_folio_warnings)
    _append_section(report_lines, "Possible orphan pages", verdict.possible_orphan_pages)
    _append_section(report_lines, "Possible widow lines", verdict.possible_widow_lines)
    _append_list(report_lines, "AI visual QA flags", verdict.openai_visual_flags)
    _append_list(report_lines, "AI text QA flags", verdict.text_qa_flags)
    if verdict.ai_rule_suggestion_file:
        report_lines.append(f"\nAI regex rule suggestions: {verdict.ai_rule_suggestion_file}")
    _append_list(report_lines, "Removed documents", removed_documents[:100])
    _append_list(report_lines, "Removed block samples", removed_blocks[:100])
    _append_list(report_lines, "Kept image samples", kept_images[:100])
    _append_list(report_lines, "Removed image samples", removed_images[:100])
    _append_list(report_lines, "AI decisions", ai_decisions[:160])
    _append_list(report_lines, "Warnings", warnings_seen[:160])
    _append_list(report_lines, "Hard failures", hard_failures)

    qa_txt = out_dir / "qa_report.txt"
    qa_txt.write_text("\n".join(report_lines), encoding="utf-8")
    doc.close()
    return verdict, qa_json, qa_txt


def dataclass_to_dict(obj):
    """Recursively convert a dataclass to a dict for JSON serialization."""
    import dataclasses

    return dataclasses.asdict(obj)


def _append_section(lines: list[str], title: str, data: list) -> None:
    if data:
        lines.append(f"\n{title}:\n" + json.dumps(data[:80], indent=2, ensure_ascii=False))


def _append_list(lines: list[str], title: str, data: list) -> None:
    if data:
        lines.append(f"\n{title}:\n" + "\n".join("- " + x for x in data))
