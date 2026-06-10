#!/usr/bin/env python3
"""
Deluxe EPUB -> print-ready A4 PDF pipeline.

This is a serious local production pipeline for large public-domain collected works.
It is designed around a strict book-production prompt: professional structure, A4
single-page PDF output, recto/verso starts, roman/Arabic pagination, running heads,
poetry/drama handling, image/plate cleanup, TOC page numbers, and PDF preflight.

Important limitation: no script can literally guarantee human typesetting taste for
all malformed EPUBs. This pipeline therefore combines deterministic rules, optional
OpenAI-assisted structure/image/visual review, and hard QA gates. In --strict mode it
exits non-zero if delivery-blocking warnings remain.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import html
import json
import os
import posixpath
import re
import shutil
import sys
import textwrap
import urllib.parse
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning
from ebooklib import ITEM_DOCUMENT, ITEM_IMAGE, epub
from weasyprint import CSS, HTML

# --------------------------------------------------------------------------------------
# Constants and patterns
# --------------------------------------------------------------------------------------

A4_WIDTH_PT = 595.276
A4_HEIGHT_PT = 841.890
PT_PER_MM = 72.0 / 25.4

PROMO_PATTERNS = re.compile(
    r"(delphi classics|also available|other books by|more books by|subscribe|newsletter|"
    r"visit our website|follow us|kindle|ebook|smashwords|gutenberg license|project gutenberg license|"
    r"catalogue|catalog|advertisement|promotion|publisher's note to the reader|download our|"
    r"copyrighted images? removed|www\.|https?://|isbn|app store|google play|goodreads)",
    re.I,
)

BACKMATTER_PATTERNS = re.compile(
    r"^(notes|endnotes|appendix|appendices|bibliography|glossary|index|letters|notebooks|"
    r"biography|chronology|translator'?s notes?|editor'?s notes?|commentary|source notes?)$",
    re.I,
)

LOCAL_TOC_HEADINGS = re.compile(r"^(contents|table of contents|chapter list|list of chapters)$", re.I)

COLLECTION_DIVISIONS = re.compile(
    r"^(the )?(novels|short stories|stories|plays|poetry|poems|memoirs|letters|notebooks|"
    r"essays|biography|appendices|tales|sketches|dramas|translations|miscellanies|"
    r"non[- ]fiction|verse|narrative poems|lyric poems)$",
    re.I,
)

MAJOR_WORK_HINTS = re.compile(
    r"^(book|part|volume)\s+[ivxlcdm0-9]+$|^(novel|play|poem|drama|story|tale)s?\b",
    re.I,
)

CHAPTER_HEADINGS = re.compile(
    r"^(chapter|scene|act|section|proposition|article|letter|canto|book)\b|^[ivxlcdm]+$|^\d+$",
    re.I,
)

CAST_HEADINGS = re.compile(
    r"^(dramatis personae|characters|persons|the persons of the play|names of the characters|"
    r"the characters|cast of characters|personages)$",
    re.I,
)

ACT_SCENE_HEADINGS = re.compile(r"^(act|scene)\b|^act\s+[ivxlcdm0-9]+$", re.I)

PLATE_CAPTION_PATTERNS = re.compile(
    r"(delphi classics|decorative title|title illustration|frontispiece|"
    r"title page of the first edition|first edition|standalone plate|plate page|"
    r"portrait of|author portrait|photographic portrait|photograph of|photo of|pictured above|"
    r"\b(the )?author[’']?s (birthplace|parents|father|mother|family|home|house|grave|tomb|portrait)\b|"
    r"\b(birthplace|parents|father|mother|family|grave|tomb|statue|monument|museum)\b|"
    r"\b(house|home|residence) of\b|view of .* house|monument to|"
    r"\b(chekhov|turgenev|dostoevsky|dostoyevsky|tolstoy)\s+as\s+(a|an|the)?\s*(boy|child|young|young man|student|writer)\b|"
    r"dostoevsky at the beginning|dostoyevsky at the beginning|beginning of (his|her) literary career|"
    r"hospital for the poor|where (his|her) father worked|"
    r"illustration from .* edition|engraving from .* edition|painting by|"
    r"\btranslated by\b)",
    re.I,
)

FUNCTIONAL_IMAGE_CLUES = re.compile(
    r"(map|diagram|chart|table|figure|rune|inscription|alphabet|script|seal|facsimile|"
    r"manuscript|author-?drawn|drawn by the author|drawing|plan|musical notation|score|"
    r"see (the )?(figure|map|diagram|chart|drawing|plan)|"
    r"dotted lines|following figure|shown below|shown above|as follows|the accompanying|"
    r"illustrated below|engraving below|symbol|glyph|sign)",
    re.I,
)

FUNCTIONAL_IMAGE_SRC_CLUES = re.compile(
    r"(map|diagram|chart|figure|rune|inscription|alphabet|script|seal|facsimile|"
    r"manuscript|drawing|plan|score|genealogy)",
    re.I,
)

PUBLISHER_IMAGE_SRC_CLUES = re.compile(
    r"(cover|frontispiece|portrait|photo|photograph|author|birthplace|house|home|grave|"
    r"tomb|museum|plate|illustration|delphi|title|logo|catalog|catalogue|promo)",
    re.I,
)

LOCAL_CONTENTS_LINE_RE = re.compile(
    r"^(contents|chapter|letter|act|scene|book|part|volume|section|[ivxlcdm]+|\d+|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
    r"(st|nd|rd|th)?\.?)\b",
    re.I,
)

POETRY_CLASS_RE = re.compile(r"poem|poetry|stanza|verse|line|canto|song|epigram|sonnet|ode", re.I)

ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.I)

RULE_PACK_KEYS = {
    "promo_patterns": "PROMO_PATTERNS",
    "backmatter_patterns": "BACKMATTER_PATTERNS",
    "local_toc_headings": "LOCAL_TOC_HEADINGS",
    "collection_divisions": "COLLECTION_DIVISIONS",
    "major_work_hints": "MAJOR_WORK_HINTS",
    "chapter_headings": "CHAPTER_HEADINGS",
    "cast_headings": "CAST_HEADINGS",
    "plate_caption_patterns": "PLATE_CAPTION_PATTERNS",
    "functional_image_clues": "FUNCTIONAL_IMAGE_CLUES",
    "functional_image_src_clues": "FUNCTIONAL_IMAGE_SRC_CLUES",
    "publisher_image_src_clues": "PUBLISHER_IMAGE_SRC_CLUES",
    "local_contents_line": "LOCAL_CONTENTS_LINE_RE",
    "poetry_class": "POETRY_CLASS_RE",
}

SMART_QUOTES_MAP = {
    "--": "—",
    "...": "…",
}

# --------------------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------------------

@dataclass
class Settings:
    # Book identity
    title: str = ""

    # Page / trim. The current production baseline is A4; other trim sizes require
    # additional QA because the heuristics and page-area checks are tuned for A4.
    trim_size: str = "A4"

    # Core type system
    body_size_pt: float = 11.6
    line_height: float = 1.23
    font_stack: str = '"EB Garamond", "Cormorant Garamond", Garamond, Georgia, serif'
    embed_font_files: bool = True
    font_dir: str = "fonts"
    embedded_font_family: str = "EB Garamond"
    embedded_font_regular: str = "EBGaramond-wght.ttf"
    embedded_font_italic: str = "EBGaramond-Italic-wght.ttf"
    embedded_font_weight: str = "400 800"
    text_color: str = "#111"
    body_font_weight: str = "400"
    hyphenate: bool = True
    justify_prose: bool = True

    # Margins and live area
    margin_top_mm: float = 24.0
    margin_side_mm: float = 22.0
    margin_bottom_mm: float = 25.0
    front_margin_top_mm: float = 25.0
    front_margin_bottom_mm: float = 24.0

    # Running heads / folios
    runner_font_pt: float = 9.4
    runner_letter_spacing_em: float = 0.04
    runner_rule_gap_mm: float = 3.2
    runner_body_clearance_mm: float = 6.0
    runner_rule_y_mm: float = 17.0
    runner_title_top_mm: float = 8.5
    runner_rule_weight_pt: float = 0.45
    runner_rule_color: str = "#222"
    # Header layout: "centered_single_rule" gives one centered running head with
    # one rule beneath it. "dual_full_rule" gives the older collection/work split:
    # collection title at left, current major work at right, and one continuous rule below.
    # "alternating" keeps old verso/recto logic.
    runner_layout: str = "right_title_full_rule"
    runner_rule_style: str = "full_width"  # full_width, single, split, none
    runner_collection_transform: str = "none"
    runner_work_transform: str = "uppercase"
    folio_font_pt: float = 10.0
    front_folio_font_pt: float = 9.3

    # Paragraphs / blocks
    paragraph_indent_em: float = 1.25
    blockquote_side_margin_mm: float = 10.0
    blockquote_font_percent: float = 96.0

    # Major headings and openers
    major_opener_top_margin_mm: float = 55.0
    major_opener_bottom_margin_mm: float = 15.0
    major_work_font_pt: float = 23.5
    collection_division_font_pt: float = 25.0
    subdivision_font_pt: float = 14.8
    subdivision_margin_top_mm: float = 10.0
    subdivision_margin_bottom_mm: float = 5.0
    h3_font_pt: float = 13.0
    minor_heading_font_pt: float = 11.4

    # Contents page
    toc_title_font_pt: float = 19.0
    toc_level_1_font_pt: float = 11.4
    toc_level_2_font_pt: float = 10.5
    toc_level_3_font_pt: float = 10.0
    toc_level_4_font_pt: float = 9.7
    toc_line_height: float = 1.11
    toc_entry_gap_mm: float = 2.9

    # Poetry / verse
    verse_max_width_mm: float = 128.0
    verse_line_height: float = 1.15
    verse_hanging_indent_em: float = 1.4
    verse_block_margin_top_mm: float = 4.0
    verse_block_margin_bottom_mm: float = 5.0

    # Drama / cast lists
    cast_max_width_mm: float = 132.0
    cast_line_height: float = 1.16

    # Images and cleanup
    image_policy: str = "functional"  # functional, keep-all, remove-all
    smart_punctuation: bool = True
    rule_pack_dir: str = "rules"
    rule_packs: str = "generic_epub.yaml"
    write_ai_rule_suggestions: bool = True

    # Front matter generation. By default, do not insert process labels into the book.
    title_page_subtitle: str = ""
    include_source_note: bool = False
    source_note_text: str = "Prepared as a single-page A4 print interior from the supplied EPUB source."

    # Pipeline behavior
    strict: bool = False
    no_sample_requirement: bool = False

@dataclass
class TocEntry:
    level: int
    title: str
    target_id: str
    kind: str = "work"  # division, work, backmatter

@dataclass
class SpineDoc:
    index: int
    item_id: str
    name: str
    href: str
    raw: bytes
    headings: list[str] = field(default_factory=list)
    text_sample: str = ""
    text_length: int = 0
    kind: str = "unknown"
    remove: bool = False
    major_title: Optional[str] = None
    current_division: Optional[str] = None
    contains_poetry: bool = False
    contains_drama: bool = False
    contains_images: bool = False
    confidence: float = 0.0
    notes: str = ""

@dataclass
class BuildLog:
    removed_blocks: list[str] = field(default_factory=list)
    removed_documents: list[str] = field(default_factory=list)
    kept_images: list[str] = field(default_factory=list)
    removed_images: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hard_failures: list[str] = field(default_factory=list)
    ai_decisions: list[str] = field(default_factory=list)
    title_source: str = ""
    detected_poetry_blocks: int = 0
    detected_poetry_sequences: int = 0
    detected_cast_sections: int = 0
    normalized_cast_entries: int = 0
    local_tocs_removed: int = 0
    typographic_fixes: int = 0
    css_auto_fixes: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def fail(self, msg: str) -> None:
        self.hard_failures.append(msg)

    def removed(self, reason: str, sample: str = "") -> None:
        s = clean_text(sample)[:220]
        self.removed_blocks.append(f"{reason}: {s}" if s else reason)

@dataclass
class QAVerdict:
    page_count: int = 0
    non_a4_pages: list[dict[str, Any]] = field(default_factory=list)
    possible_line_spills: list[dict[str, Any]] = field(default_factory=list)
    dark_pages: list[int] = field(default_factory=list)
    possible_blank_page_artifacts: list[dict[str, Any]] = field(default_factory=list)
    possible_header_collisions: list[dict[str, Any]] = field(default_factory=list)
    possible_narrow_columns: list[dict[str, Any]] = field(default_factory=list)
    toc_page_number_warnings: list[str] = field(default_factory=list)
    toc_duplicate_warnings: list[str] = field(default_factory=list)
    empty_content_pages: list[dict[str, Any]] = field(default_factory=list)
    first_body_folio_warnings: list[str] = field(default_factory=list)
    openai_visual_flags: list[str] = field(default_factory=list)
    openai_visual_issue_lines: list[str] = field(default_factory=list)
    text_qa_flags: list[str] = field(default_factory=list)
    text_qa_issue_lines: list[str] = field(default_factory=list)
    ai_rule_suggestion_file: str = ""
    fonts_seen: list[str] = field(default_factory=list)
    images_seen: int = 0
    qa_renders: list[str] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return bool(
            self.non_a4_pages
            or self.dark_pages
            or self.possible_blank_page_artifacts
            or self.possible_header_collisions
            or self.possible_line_spills
            or self.toc_duplicate_warnings
            or self.empty_content_pages
            or self.first_body_folio_warnings
            or self.openai_visual_flags
            or self.text_qa_flags
        )

# --------------------------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------------------------

def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str, fallback: str = "section") -> str:
    value = clean_text(value).lower()
    value = re.sub(r"['’]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or fallback


def unique_id(base: str, used: set[str]) -> str:
    base = slugify(base)
    ident = base
    n = 2
    while ident in used:
        ident = f"{base}-{n}"
        n += 1
    used.add(ident)
    return ident


def normalized_title_key(value: str | None) -> str:
    """Normalize headings for duplicate detection without destroying display text."""
    text = clean_text(value or "").lower()
    text = re.sub(r"\[\*+\]", "", text)
    text = re.sub(r"\b(deluxe print interior|a novel|a story|a tale|a play)\b", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def visible_word_count(value: str | None) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", clean_text(value or "")))


def clean_display_title(value: str | None) -> str:
    """Remove EPUB/navigation artifacts that should not appear in print headings."""
    text = clean_text(value or "")
    text = re.sub(r"\[\*+\]", "", text)
    return clean_text(text)


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    cwd_path = (Path.cwd() / path).resolve()
    if cwd_path.exists():
        return cwd_path
    return (Path(__file__).resolve().parent / path).resolve()


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit("YAML support requires PyYAML: pip install pyyaml\n" + str(exc))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Rule pack must contain a top-level mapping: {path}")
    return data


def rule_pack_names(settings: Settings) -> list[str]:
    raw = clean_text(settings.rule_packs)
    if not raw:
        return []
    return [name.strip() for name in re.split(r"[,;]", raw) if name.strip()]


def compile_extended_pattern(existing: re.Pattern, additions: list[str], source: Path, key: str) -> re.Pattern:
    valid: list[str] = []
    for pattern in additions:
        pattern = str(pattern).strip()
        if not pattern:
            continue
        try:
            re.compile(pattern, existing.flags)
        except re.error as exc:
            raise SystemExit(f"Invalid regex in {source} under {key}: {pattern}\n{exc}")
        valid.append(pattern)
    if not valid:
        return existing
    combined = f"(?:{existing.pattern})|(?:{'|'.join(valid)})"
    return re.compile(combined, existing.flags)


def apply_rule_packs(settings: Settings, log: Optional["BuildLog"] = None) -> None:
    """Extend built-in regexes with reviewed YAML rule packs."""
    names = rule_pack_names(settings)
    if not names:
        return
    rule_dir = resolve_project_path(settings.rule_pack_dir)
    if not rule_dir.exists():
        if log:
            log.warn(f"Rule-pack directory not found: {rule_dir}")
        return
    loaded: list[str] = []
    for name in names:
        path = (rule_dir / name).resolve()
        if not path.exists():
            if log:
                log.warn(f"Rule pack not found: {path}")
            continue
        data = load_yaml_file(path)
        for key, global_name in RULE_PACK_KEYS.items():
            values = data.get(key) or []
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                raise SystemExit(f"Rule pack key must be a list or string: {path}::{key}")
            if values:
                globals()[global_name] = compile_extended_pattern(globals()[global_name], values, path, key)
        loaded.append(path.name)
    if loaded and log:
        log.warn("Loaded regex rule packs: " + ", ".join(loaded))


def normalize_src(src: str, doc_name: str) -> str:
    src = urllib.parse.unquote((src or "").split("#", 1)[0])
    if not src:
        return ""
    base = posixpath.dirname(doc_name)
    return posixpath.normpath(posixpath.join(base, src))


def strip_tag(tag: Tag) -> None:
    try:
        tag.decompose()
    except Exception:
        try:
            tag.extract()
        except Exception:
            pass


def item_bytes(item) -> bytes:
    data = item.get_content()
    if isinstance(data, str):
        return data.encode("utf-8", errors="ignore")
    return data


def parse_html(raw: bytes) -> BeautifulSoup:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        return BeautifulSoup(raw, "lxml")


def remove_comments_scripts_styles(soup: BeautifulSoup) -> None:
    for c in soup.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    for tag in soup.find_all(["script", "style", "iframe", "object", "embed"]):
        strip_tag(tag)


def simple_typographic_cleanup(soup: BeautifulSoup, log: BuildLog) -> None:
    """Conservative punctuation cleanup. Does not touch pre/code/verse/cast blocks."""
    skip_parents = {"pre", "code", "kbd", "samp"}
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent and parent.name in skip_parents:
            continue
        text = str(node)
        new = text.replace("\xa0", " ")
        new = re.sub(r"[ \t\r\f\v]+", " ", new)
        # Conservative dash/ellipsis normalization.
        new = new.replace("...", "…")
        new = re.sub(r"\s+--\s+", " — ", new)
        new = re.sub(r"(?<=\w)--(?=\w)", "—", new)
        # Remove double spaces after periods.
        new = re.sub(r"(?<=[.!?]) {2,}", " ", new)
        if new != text:
            node.replace_with(new)
            log.typographic_fixes += 1


def extract_probe(raw: bytes, limit: int = 9000) -> tuple[list[str], str, int, bool, bool, bool]:
    soup = parse_html(raw)
    remove_comments_scripts_styles(soup)
    headings: list[str] = []
    for h in soup.find_all(re.compile(r"^h[1-6]$"))[:40]:
        t = clean_text(h.get_text(" "))
        if t:
            headings.append(f"{h.name.upper()}: {t}")
    text = clean_text(soup.get_text(" "))
    cls_text = " ".join(str(x.get("class", "")) for x in soup.find_all(True)[:500])
    contains_poetry = bool(POETRY_CLASS_RE.search(cls_text)) or _text_looks_like_poetry(soup)
    contains_drama = bool(any(CAST_HEADINGS.match(clean_text(h.get_text(" "))) for h in soup.find_all(re.compile(r"^h[1-6]$"))))
    contains_images = bool(soup.find("img"))
    return headings, text[:limit], len(text), contains_poetry, contains_drama, contains_images


def _text_looks_like_poetry(soup: BeautifulSoup) -> bool:
    br_blocks = 0
    for tag in soup.find_all(["p", "div"]):
        if len(tag.find_all("br")) >= 2 and len(clean_text(tag.get_text(" "))) < 1600:
            br_blocks += 1
            if br_blocks >= 2:
                return True
    short_run = 0
    for p in soup.find_all("p")[:200]:
        t = clean_text(p.get_text(" "))
        if 2 <= len(t) <= 64 and not t.endswith("."):
            short_run += 1
            if short_run >= 6:
                return True
        elif t:
            short_run = 0
    return False

# --------------------------------------------------------------------------------------
# OpenAI integration
# --------------------------------------------------------------------------------------

SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["frontmatter", "division", "major_work", "chapter", "poetry", "play", "backmatter", "promo", "local_toc", "unknown"]},
                    "remove_document": {"type": "boolean"},
                    "major_title": {"type": ["string", "null"]},
                    "current_division": {"type": ["string", "null"]},
                    "contains_poetry": {"type": "boolean"},
                    "contains_drama_or_cast": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "notes": {"type": "string"},
                },
                "required": ["index", "kind", "remove_document", "major_title", "current_division", "contains_poetry", "contains_drama_or_cast", "confidence", "notes"],
            },
        }
    },
    "required": ["documents"],
}

IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["keep", "reason", "confidence"],
}


def require_openai_client():
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        raise SystemExit("Install OpenAI support with: pip install openai\n" + str(exc))
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. PowerShell: $env:OPENAI_API_KEY='sk-...' ")
    return OpenAI()


def require_deepseek_client():
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        raise SystemExit("Install OpenAI-compatible client support with: pip install openai\n" + str(exc))
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is not set. PowerShell: $env:DEEPSEEK_API_KEY='sk-...' ")
    return OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")


def require_ai_client(provider: str):
    if provider == "none":
        return None
    if provider == "deepseek":
        return require_deepseek_client()
    return require_openai_client()


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def openai_json(client, model: str, system: str, user: str, schema: dict[str, Any], name: str, provider: str = "openai") -> dict[str, Any]:
    if provider == "deepseek":
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system + " Return only valid JSON."},
                {"role": "user", "content": user + "\n\nJSON schema:\n" + json.dumps(schema, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        return extract_json(resp.choices[0].message.content or "{}")
    try:
        resp = client.responses.create(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        )
        return extract_json(resp.output_text)
    except Exception:
        # Compatibility fallback for SDK/API variants.
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system + " Return only valid JSON."},
                {"role": "user", "content": user + "\n\nJSON schema:\n" + json.dumps(schema)},
            ],
        )
        return extract_json(resp.output_text)


def apply_openai_book_plan(client, model: str, docs: list[SpineDoc], title: str, log: BuildLog, batch_size: int = 24, provider: str = "openai") -> None:
    system = (
        "You are a conservative professional book-structure classifier for an EPUB-to-print-PDF pipeline. "
        "You must preserve legitimate book text. Remove only obvious promotional/catalogue/ebook-navigation junk. "
        "Classify front matter, body divisions, major works, chapters, poetry, plays/cast lists, and back matter. "
        "Do not classify ordinary chapter names as running-head major works."
    )
    for offset in range(0, len(docs), batch_size):
        batch = docs[offset: offset + batch_size]
        parts = []
        for d in batch:
            parts.append({
                "index": d.index,
                "href": d.href,
                "headings": d.headings[:20],
                "text_sample": d.text_sample[:3500],
                "text_length": d.text_length,
                "local_contains_poetry": d.contains_poetry,
                "local_contains_drama": d.contains_drama,
                "local_contains_images": d.contains_images,
            })
        user = (
            f"Book title: {title}\n"
            "Classify these EPUB spine documents for deluxe print-book conversion. Return JSON only.\n"
            + json.dumps(parts, ensure_ascii=False)
        )
        try:
            result = openai_json(client, model, system, user, SECTION_SCHEMA, "book_structure_plan", provider=provider)
        except Exception as exc:
            log.warn(f"{provider.title()} book-plan classification failed for batch starting {offset}: {exc}")
            continue
        by_index = {int(x.get("index")): x for x in result.get("documents", []) if "index" in x}
        for d in batch:
            x = by_index.get(d.index)
            if not x:
                continue
            d.kind = str(x.get("kind") or d.kind)
            d.remove = bool(x.get("remove_document")) and float(x.get("confidence", 0)) >= 0.86
            d.major_title = clean_text(x.get("major_title")) or d.major_title
            d.current_division = clean_text(x.get("current_division")) or d.current_division
            d.contains_poetry = bool(x.get("contains_poetry", d.contains_poetry))
            d.contains_drama = bool(x.get("contains_drama_or_cast", d.contains_drama))
            d.confidence = float(x.get("confidence", 0) or 0)
            d.notes = str(x.get("notes") or "")
            log.ai_decisions.append(f"{d.index} {d.href}: kind={d.kind} remove={d.remove} major={d.major_title!r} division={d.current_division!r} conf={d.confidence:.2f} {d.notes}")


def ai_image_decision(client, model: str, src: str, context: str, provider: str = "openai") -> dict[str, Any]:
    system = (
        "You classify EPUB images for a print-book pipeline. Keep authorial/functionally necessary images: maps, diagrams, charts, symbols, runes, inscriptions, facsimiles, image-texts, and images directly referenced by surrounding text. "
        "Remove publisher-added plates, portraits, unrelated illustrations, catalogue/promotional images, and orphan captions. Be conservative when uncertain."
    )
    user = f"Image src: {src}\nContext/caption around the image:\n{context[:3000]}"
    return openai_json(client, model, system, user, IMAGE_SCHEMA, "image_decision", provider=provider)


def openai_visual_qa(client, model: str, pdf_path: Path, qa_json: Path, qa_dir: Path, max_pages: int) -> Path:
    images = render_selected_pages(pdf_path, qa_dir, prefix="openai_page", max_pages=max_pages, jpg=True)
    report_excerpt = qa_json.read_text(encoding="utf-8", errors="ignore")[:12000] if qa_json.exists() else ""
    prompt = (
        "Review these rendered pages from an A4 deluxe print-book PDF as a strict book-production QA inspector.\n\n"
        "Check all of these prompt requirements:\n"
        "1. Body typography: prose paragraphs should be fully justified, not ragged-right, except legitimate poetry, drama, TOC, headings, captions, and front matter. Watch for loose rivers, bad word spacing, broken words, single-letter line spills, and narrow/overwide columns.\n"
        "2. Paragraph rhythm: first-line indents should be consistent, prose should not have ebook-like blank gaps between ordinary paragraphs, and paragraphs should not collide with headings, runners, folios, or page edges.\n"
        "3. Chapter and work titles: major work openers should be centered and placed with deliberate vertical space; ordinary chapter titles should be centered with proper space before/after, not blue/underlined, not inline with body text, and not stranded at the bottom of a page.\n"
        "4. Running heads and rules: body pages should have one clean rule, enough clearance from body text, no crowding/collision, and correct alternating logic: collection title on verso/left pages, current work/chapter on recto/right pages where applicable. Title pages, blank pages, and major openers should not show inappropriate runners.\n"
        "5. Folios/page numbers: front matter and body numbering should look intentional, centered, unobtrusive, and absent from true blanks/title pages where expected.\n"
        "6. Contents/TOC: generated contents should look print-native, with no blue hyperlinks or underlines, no duplicate local mini-TOCs, and page numbers/leaders aligned cleanly.\n"
        "7. Image and plate cleanup: publisher-added portraits, decorative plates, catalogue pages, and orphan captions should be gone; authorial or functional images, if present, should not be cropped or oversized.\n"
        "8. Poetry/drama/special forms: verse lineation, hanging indents, cast lists, stage directions, letters, and block quotes should look intentional rather than flattened into ordinary prose.\n"
        "9. Ebook artifacts: flag raw ebook layout, colored links, browser-like styling, bad CSS remnants, dark/black pages, accidental blank/title-only pages, cropped text, or anything that looks non-print-ready.\n\n"
        "Output exactly this structure:\n"
        "FINAL: PASS or FAIL\n"
        "SUMMARY: one short paragraph\n"
        "FINDINGS:\n"
        "- Page N: [category] issue and suggested fix\n"
        "If there are no issues, write '- None'.\n"
        "CHECKED:\n"
        "- Body justification: OK or ISSUE\n"
        "- Chapter/title placement: OK or ISSUE\n"
        "- Running heads/rules: OK or ISSUE\n"
        "- Folios/page numbers: OK or ISSUE\n"
        "- TOC: OK or ISSUE\n"
        "- Image cleanup: OK or ISSUE\n"
        "- Poetry/drama/special forms: OK or ISSUE\n"
        "- Ebook artifacts: OK or ISSUE\n\n"
        "Do not mark an item as ISSUE unless the rendered pages visibly show a real problem. Give page-specific findings and a final PASS/FAIL.\n\n"
        "Local QA JSON excerpt:\n" + report_excerpt
    )
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for img in images:
        b64 = base64.b64encode(img.read_bytes()).decode("utf-8")
        content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})
    resp = client.responses.create(model=model, input=[{"role": "user", "content": content}])
    out = qa_dir.parent / "openai_visual_qa.txt"
    out.write_text(resp.output_text, encoding="utf-8")
    return out


def openai_visual_issue_lines(visual_text: str) -> list[str]:
    issue_terms = re.compile(
        r"\b(FAIL|ISSUE|problem|warning|collision|crowd(?:ed|ing)?|touch(?:es|ing)?|"
        r"misalign(?:ed|ment)?|wrong|broken|spill|overwide|narrow|ragged|unjustified|"
        r"not justified|stranded|orphan|duplicate|blue|underlined|raw ebook|artifact|"
        r"blank|empty|dark|black|cropped|oversized|missing|inappropriate)\b",
        re.I,
    )
    ok_terms = re.compile(r"\b(OK|PASS|passes|acceptable|clean|fine|none|no issues?|no problems?|not observed)\b", re.I)
    lines: list[str] = []
    for raw_line in visual_text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        if not issue_terms.search(line):
            continue
        if ok_terms.search(line) and not re.search(r"\bFAIL\b|\bISSUE\b", line, re.I):
            continue
        lines.append(line)
    return lines


def ai_text_issue_lines(report_text: str) -> list[str]:
    issue_terms = re.compile(
        r"\b(FAIL|ISSUE|problem|warning|duplicate|missing|wrong|residue|artifact|"
        r"promo|publisher|caption|image|TOC|contents|chapter|heading|folio|page number|"
        r"ragged|unjustified|not justified|line spill|single-letter|blank|empty|raw ebook|"
        r"blue|underlined|hyperlink|poetry|verse|drama|cast|stage direction)\b",
        re.I,
    )
    ok_terms = re.compile(r"\b(OK|PASS|passes|acceptable|clean|fine|none|no issues?|no problems?|not observed)\b", re.I)
    lines: list[str] = []
    for raw_line in report_text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        if not issue_terms.search(line):
            continue
        if ok_terms.search(line) and not re.search(r"\bFAIL\b|\bISSUE\b", line, re.I):
            continue
        lines.append(line)
    return lines


def add_visual_flag(verdict: QAVerdict, flag: str) -> None:
    if flag not in verdict.openai_visual_flags:
        verdict.openai_visual_flags.append(flag)


def add_text_qa_flag(verdict: QAVerdict, flag: str) -> None:
    if flag not in verdict.text_qa_flags:
        verdict.text_qa_flags.append(flag)


def visual_feedback_text(verdict: QAVerdict) -> str:
    return "\n".join(
        verdict.openai_visual_flags
        + verdict.openai_visual_issue_lines
        + verdict.text_qa_flags
        + verdict.text_qa_issue_lines
    )


def has_visual_feedback(verdict: QAVerdict, pattern: str) -> bool:
    return re.search(pattern, visual_feedback_text(verdict), re.I) is not None


def extract_pdf_text_for_ai(pdf_path: Path, max_pages: int = 28) -> str:
    import fitz
    doc = fitz.open(pdf_path)
    chunks: list[str] = []
    try:
        for i in range(min(doc.page_count, max_pages)):
            text = clean_text(doc[i].get_text("text"))
            if text:
                chunks.append(f"--- PAGE {i + 1} ---\n{text[:3500]}")
    finally:
        doc.close()
    return "\n\n".join(chunks)[:60000]


def chat_text(client, model: str, system: str, user: str, provider: str) -> str:
    if provider == "deepseek":
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""
    resp = client.responses.create(model=model, input=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return resp.output_text


def extract_review_rule_suggestions(report: str) -> dict[str, list[str]]:
    yaml_text = ""
    m = re.search(r"```(?:yaml|yml)\s*(.*?)```", report, re.S | re.I)
    if m:
        yaml_text = m.group(1)
    else:
        start = report.find("rule_suggestions:")
        if start >= 0:
            yaml_text = report[start:]
    if not yaml_text.strip():
        return {}
    try:
        import yaml
        data = yaml.safe_load(yaml_text) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    data = data.get("rule_suggestions", data)
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key in RULE_PACK_KEYS:
        values = data.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        cleaned: list[str] = []
        for value in values:
            pattern = clean_text(str(value))
            if not pattern:
                continue
            try:
                re.compile(pattern, re.I)
            except re.error:
                continue
            cleaned.append(pattern)
        if cleaned:
            out[key] = cleaned
    return out


def write_review_rule_suggestions(path: Path, suggestions: dict[str, list[str]], source_report: Path) -> None:
    lines = [
        "# AI-suggested regex rules for human review.",
        "# Do not load this file directly until each pattern has been checked against real EPUB samples.",
        f"# Source report: {source_report.name}",
        "",
    ]
    for key in RULE_PACK_KEYS:
        values = suggestions.get(key, [])
        if not values:
            continue
        lines.append(f"{key}:")
        for value in values:
            lines.append("  - " + json.dumps(value, ensure_ascii=False))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def ai_text_qa(client, model: str, provider: str, pdf_path: Path, qa_json: Path, qa_txt: Path, artifact_dir: Path, settings: Settings, log: BuildLog) -> tuple[Path, Optional[Path]]:
    page_text = extract_pdf_text_for_ai(pdf_path)
    qa_report = qa_txt.read_text(encoding="utf-8", errors="ignore")[:20000] if qa_txt.exists() else ""
    qa_verdict = qa_json.read_text(encoding="utf-8", errors="ignore")[:20000] if qa_json.exists() else ""
    system = (
        "You are a conservative text-and-structure QA reviewer for a deluxe EPUB-to-print-PDF pipeline. "
        "You cannot see rendered page images. Review only the provided local QA, extracted PDF text, removed-block logs, and settings. "
        "Focus on textual/structural problems and regex cleanup opportunities. Do not invent visual claims."
    )
    user = (
        "Review this generated book after local deterministic QA has already run.\n\n"
        "Return this structure:\n"
        "FINAL: PASS or FAIL\n"
        "SUMMARY: short paragraph\n"
        "FINDINGS:\n- Page/area: [category] issue and suggested fix, or '- None'\n"
        "AUTO_FIXABLE_SIGNALS:\n"
        "- Use these words only when visibly supported by extracted text/local QA: body-typography, chapter/title placement, TOC spacing/page-number, runner/header clearance, folio/page-numbering, ebook artifact, image/caption cleanup, poetry/drama/special-form.\n"
        "- For safe layout tuning, say ISSUE with one of those categories. For structural cleanup requiring regex rules, put the proposed pattern in REGEX_RULE_SUGGESTIONS.\n"
        "REGEX_RULE_SUGGESTIONS:\n"
        "Provide a fenced yaml block named rule_suggestions using only these optional keys: "
        + ", ".join(RULE_PACK_KEYS)
        + ". Include only high-confidence patterns that would help future EPUB cleanup. Do not include broad patterns likely to remove real literature.\n\n"
        "Current settings excerpt:\n" + json.dumps(dataclasses.asdict(settings), ensure_ascii=False)[:12000]
        + "\n\nLocal QA report:\n" + qa_report
        + "\n\nLocal QA verdict JSON:\n" + qa_verdict
        + "\n\nRemoved documents:\n" + "\n".join(log.removed_documents[:80])
        + "\n\nRemoved blocks:\n" + "\n".join(log.removed_blocks[:80])
        + "\n\nRemoved images:\n" + "\n".join(log.removed_images[:80])
        + "\n\nExtracted PDF page text sample:\n" + page_text
    )
    report_text = chat_text(client, model, system, user, provider)
    report_path = artifact_dir / f"{provider}_text_qa.txt"
    report_path.write_text(report_text, encoding="utf-8")
    suggestions_path: Optional[Path] = None
    if settings.write_ai_rule_suggestions:
        suggestions = extract_review_rule_suggestions(report_text)
        if suggestions:
            suggestions_path = artifact_dir / f"{provider}_rule_suggestions.review.yaml"
            write_review_rule_suggestions(suggestions_path, suggestions, report_path)
    return report_path, suggestions_path

# --------------------------------------------------------------------------------------
# EPUB scanner and classifiers
# --------------------------------------------------------------------------------------


def read_epub(path: Path) -> tuple[Any, list[Any]]:
    book = epub.read_epub(str(path))
    items: list[Any] = []
    for entry in book.spine:
        idref = entry[0] if isinstance(entry, tuple) else entry
        if idref == "nav":
            continue
        item = book.get_item_with_id(idref)
        if item and item.get_type() == ITEM_DOCUMENT:
            items.append(item)
    if not items:
        items = list(book.get_items_of_type(ITEM_DOCUMENT))
    return book, items


def infer_title_with_source(book, fallback: str) -> tuple[str, str]:
    titles = book.get_metadata("DC", "title")
    for title_entry in titles or []:
        if not title_entry or not title_entry[0]:
            continue
        title = clean_display_title(html.unescape(str(title_entry[0])))
        if title:
            return title, "EPUB metadata"
    fallback_title = clean_display_title(fallback) or "Untitled"
    return fallback_title, "EPUB filename"


def infer_title(book, fallback: str) -> str:
    return infer_title_with_source(book, fallback)[0]


def scan_spine_items(items: list[Any]) -> list[SpineDoc]:
    docs: list[SpineDoc] = []
    for i, item in enumerate(items):
        raw = item_bytes(item)
        headings, sample, text_len, poetry, drama, images = extract_probe(raw)
        doc = SpineDoc(
            index=i,
            item_id=item.get_id() or str(i),
            name=item.get_name(),
            href=item.get_name(),
            raw=raw,
            headings=headings,
            text_sample=sample,
            text_length=text_len,
            contains_poetry=poetry,
            contains_drama=drama,
            contains_images=images,
        )
        heuristic_classify_doc(doc)
        docs.append(doc)
    return docs


def heuristic_classify_doc(doc: SpineDoc) -> None:
    joined_headings = " | ".join(doc.headings)
    first_heading = ""
    if doc.headings:
        first_heading = clean_text(re.sub(r"^H\d:\s*", "", doc.headings[0]))
    sample = doc.text_sample
    sample_l = sample.lower()

    if re.search(r"full project gutenberg.*license|project gutenberg.*license", first_heading + " " + sample[:1200], re.I):
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.95
        doc.notes = "Removed Project Gutenberg legal boilerplate/license document."
        return
    if doc.text_length < 60 and not doc.contains_images:
        doc.kind = "unknown"
    if PROMO_PATTERNS.search(sample) and doc.text_length < 3500:
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.78
    if LOCAL_TOC_HEADINGS.match(first_heading or "") and sample.count(" ") < 800:
        doc.kind = "local_toc"
        doc.remove = True
        doc.confidence = 0.80
    if BACKMATTER_PATTERNS.match(first_heading or ""):
        doc.kind = "backmatter"
        doc.major_title = first_heading
    elif COLLECTION_DIVISIONS.match(first_heading or ""):
        doc.kind = "division"
        doc.major_title = first_heading
        doc.current_division = first_heading
    elif CAST_HEADINGS.match(first_heading or "") or doc.contains_drama:
        doc.kind = "play"
    elif doc.contains_poetry:
        doc.kind = "poetry"
    elif first_heading and not CHAPTER_HEADINGS.match(first_heading) and not PROMO_PATTERNS.search(first_heading):
        # Conservative: long unique H1-ish document heading often starts a work.
        if doc.headings and doc.headings[0].startswith("H1") and len(first_heading) < 100:
            doc.kind = "major_work"
            doc.major_title = first_heading
    if any(x in sample_l for x in ["preface", "introduction", "foreword", "editor"]):
        if doc.index < 5 and doc.kind == "unknown":
            doc.kind = "frontmatter"
    if doc.kind == "unknown":
        doc.kind = "chapter"


def copy_assets(book, build_dir: Path, log: BuildLog) -> dict[str, str]:
    assets = build_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for item in book.get_items():
        if item.get_type() != ITEM_IMAGE:
            continue
        name = item.get_name()
        suffix = Path(name).suffix.lower() or ".img"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).name)
        if not safe.lower().endswith(suffix):
            safe += suffix
        target = assets / safe
        n = 2
        while target.exists():
            target = assets / f"{Path(safe).stem}_{n}{suffix}"
            n += 1
        try:
            target.write_bytes(item_bytes(item))
        except Exception as exc:
            log.warn(f"Could not extract image {name}: {exc}")
            continue
        rel = f"assets/{target.name}"
        mapping[name] = rel
        mapping[Path(name).name] = rel
        mapping[posixpath.normpath(name)] = rel
    return mapping


def resolve_local_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    cwd_path = (Path.cwd() / path).resolve()
    if cwd_path.exists():
        return cwd_path
    return (Path(__file__).resolve().parent / path).resolve()


def prepare_embedded_fonts(build_dir: Path, settings: Settings, log: BuildLog) -> str:
    if not settings.embed_font_files:
        return ""
    font_dir = resolve_local_path(settings.font_dir)
    regular_name = clean_text(settings.embedded_font_regular)
    italic_name = clean_text(settings.embedded_font_italic)
    family = clean_text(settings.embedded_font_family) or "EB Garamond"
    weight = clean_text(settings.embedded_font_weight) or "400"
    if not font_dir.exists():
        if family.lower() in settings.font_stack.lower():
            log.warn(f"Font embedding enabled, but font_dir was not found: {font_dir}")
        return ""

    font_targets: list[tuple[str, str]] = []
    for filename, style in [(regular_name, "normal"), (italic_name, "italic")]:
        if not filename:
            continue
        source = (font_dir / filename).resolve()
        if not source.exists():
            log.warn(f"Configured font file was not found: {source}")
            continue
        target_dir = build_dir / "fonts"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name)
        target = target_dir / safe_name
        shutil.copyfile(source, target)
        font_targets.append((f"fonts/{urllib.parse.quote(target.name)}", style))

    if not font_targets:
        if family.lower() in settings.font_stack.lower():
            log.warn(f"No configured font files were embedded for {family}; PDF may fall back to system fonts.")
        return ""

    css_blocks = []
    escaped_family = family.replace('"', '\\"')
    for rel_url, style in font_targets:
        css_blocks.append(f'''@font-face {{
  font-family: "{escaped_family}";
  src: url("{rel_url}") format("truetype");
  font-style: {style};
  font-weight: {weight};
  font-display: block;
}}''')
    return "\n".join(css_blocks)

# --------------------------------------------------------------------------------------
# HTML cleanup and normalization
# --------------------------------------------------------------------------------------


def strip_bad_attributes(soup: BeautifulSoup) -> None:
    allowed = {"href", "src", "alt", "title", "id", "class", "colspan", "rowspan"}
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
        # Remove ebook classes that usually encode bad widths/margins; keep semantic-ish classes.
        classes = tag.get("class")
        if classes:
            if isinstance(classes, str):
                classes = classes.split()
            kept = [c for c in classes if re.search(r"poem|poetry|verse|stanza|line|cast|character|stage|note|footnote|endnote|chapter|title", c, re.I)]
            if kept:
                tag["class"] = kept
            elif "class" in tag.attrs:
                del tag.attrs["class"]


def unwrap_useless_inline_tags(soup: BeautifulSoup) -> None:
    for tag in list(soup.find_all(["span", "font"])):
        if tag.name == "font" or not tag.attrs:
            tag.unwrap()


def remove_promotional_blocks(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["section", "div", "aside", "nav"])):
        text = clean_text(tag.get_text(" "))
        if not text:
            continue
        if PROMO_PATTERNS.search(text) and len(text) < 2800:
            log.removed("Removed promotional/publisher block", text)
            strip_tag(tag)
    for tag in list(soup.find_all(["p", "li"] )):
        text = clean_text(tag.get_text(" "))
        if PROMO_PATTERNS.search(text) and len(text) < 360:
            log.removed("Removed promotional line", text)
            strip_tag(tag)


def remove_local_mini_tocs(soup: BeautifulSoup, log: BuildLog) -> None:
    for heading in list(soup.find_all(re.compile(r"^h[1-6]$"))):
        htext = clean_text(heading.get_text(" "))
        if not LOCAL_TOC_HEADINGS.match(htext):
            continue
        followers: list[Any] = []
        cur = heading.next_sibling
        link_count = 0
        total_text = ""
        while cur is not None:
            nxt = cur.next_sibling
            if isinstance(cur, NavigableString):
                if not clean_text(str(cur)):
                    followers.append(cur)
                else:
                    break
            elif isinstance(cur, Tag):
                if re.match(r"h[1-6]", cur.name or ""):
                    break
                links = cur.find_all("a")
                text = clean_text(cur.get_text(" "))
                if cur.name in {"nav", "ol", "ul"} or len(links) >= 2 or (cur.name in {"p", "div"} and len(text) < 120 and links):
                    followers.append(cur)
                    link_count += len(links)
                    total_text += " " + text
                elif len(total_text) < 1000 and len(links) > 0:
                    followers.append(cur)
                    link_count += len(links)
                else:
                    break
            else:
                break
            cur = nxt
        if followers and (link_count >= 2 or len(clean_text(total_text)) < 1200):
            log.local_tocs_removed += 1
            log.removed("Removed local mini-contents", htext)
            strip_tag(heading)
            for f in followers:
                try:
                    f.extract()
                except Exception:
                    pass
    for heading in list(soup.find_all(["p", "div"])):
        htext = clean_text(heading.get_text(" "))
        if not LOCAL_TOC_HEADINGS.match(htext):
            continue
        followers: list[Any] = []
        cur = heading.next_sibling
        link_count = 0
        nav_lines = 0
        while cur is not None:
            nxt = cur.next_sibling
            if isinstance(cur, NavigableString):
                if not clean_text(str(cur)):
                    followers.append(cur)
                else:
                    break
            elif isinstance(cur, Tag):
                if re.match(r"h[1-6]", cur.name or ""):
                    break
                text = clean_text(cur.get_text(" "))
                links = cur.find_all("a")
                if not text:
                    followers.append(cur)
                elif cur.name in {"nav", "ol", "ul"} and (links or looks_like_compact_local_contents(cur)):
                    followers.append(cur)
                    link_count += max(1, len(links))
                    nav_lines += 1
                elif links and len(text) < 140 and LOCAL_CONTENTS_LINE_RE.search(text):
                    followers.append(cur)
                    link_count += len(links)
                    nav_lines += 1
                else:
                    break
            else:
                break
            cur = nxt
        if followers and link_count >= 2 and nav_lines >= 2:
            log.local_tocs_removed += 1
            log.removed("Removed sibling local mini-contents", htext)
            strip_tag(heading)
            for f in followers:
                try:
                    f.extract()
                except Exception:
                    pass


def block_text_lines(tag: Tag) -> list[str]:
    return [clean_text(x) for x in tag.get_text("\n").splitlines() if clean_text(x)]


def looks_like_compact_local_contents(tag: Tag) -> bool:
    text = clean_text(tag.get_text(" "))
    if not text or len(text) > 2600:
        return False
    first = clean_text(" ".join(block_text_lines(tag)[:4]))[:220]
    if not re.search(r"\bcontents\b", first, re.I):
        return False
    links = len(tag.find_all("a"))
    lines = block_text_lines(tag)
    if links >= 3:
        return True
    if len(lines) < 6:
        return False
    short_lines = sum(1 for line in lines if visible_word_count(line) <= 8)
    nav_lines = sum(1 for line in lines if LOCAL_CONTENTS_LINE_RE.search(line))
    return nav_lines >= 4 and short_lines / max(1, len(lines)) >= 0.70


def remove_compact_local_contents_blocks(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["nav", "ol", "ul", "p", "div"])):
        if tag.find_parent(["nav", "ol", "ul", "p", "div"]) and not tag.find_all("a"):
            continue
        if looks_like_compact_local_contents(tag):
            log.local_tocs_removed += 1
            log.removed("Removed compact local mini-contents", tag.get_text(" "))
            strip_tag(tag)


def img_context(img: Tag) -> str:
    parts: list[str] = []
    alt = img.get("alt") or img.get("title") or ""
    if alt:
        parts.append(str(alt))
    parent = img.parent if isinstance(img.parent, Tag) else None
    if parent:
        parts.append(parent.get_text(" "))
        for sib in [parent.find_previous(["p", "figcaption", "h1", "h2", "h3"]), parent.find_next(["p", "figcaption", "h1", "h2", "h3"] )]:
            if sib:
                parts.append(sib.get_text(" "))
    return clean_text(" ".join(parts))


def should_keep_image(img: Tag, settings: Settings, context: str, src: str, ai_client=None, ai_model: str = "gpt-5.4-mini", ai_provider: str = "openai", log: Optional[BuildLog] = None) -> bool:
    if settings.image_policy == "keep-all":
        return True
    if settings.image_policy == "remove-all":
        return False
    lower_src = src.lower()
    combined = clean_text(f"{src} {context}")
    has_functional_context = bool(FUNCTIONAL_IMAGE_CLUES.search(combined) or FUNCTIONAL_IMAGE_SRC_CLUES.search(lower_src))
    has_plate_context = bool(PLATE_CAPTION_PATTERNS.search(combined) or PUBLISHER_IMAGE_SRC_CLUES.search(lower_src))
    if has_plate_context and not has_functional_context:
        return False
    if ai_client is not None:
        try:
            decision = ai_image_decision(ai_client, ai_model, src, context, provider=ai_provider)
            keep = bool(decision.get("keep", True))
            if log:
                log.ai_decisions.append(f"image {src}: keep={keep} conf={decision.get('confidence')} reason={decision.get('reason')}")
            if float(decision.get("confidence", 0) or 0) >= 0.70:
                return keep
        except Exception as exc:
            if log:
                log.warn(f"{ai_provider.title()} image decision failed for {src}: {exc}")
    if has_plate_context and not has_functional_context:
        return False
    if has_functional_context:
        return True
    if "cover" in lower_src or ("title" in lower_src and len(context) < 140):
        return False
    return False


def rewrite_images(soup: BeautifulSoup, src_map: dict[str, str], doc: SpineDoc, settings: Settings, log: BuildLog, ai_client=None, ai_model: str = "gpt-5.4-mini", ai_provider: str = "openai") -> None:
    for img in list(soup.find_all("img")):
        src = img.get("src") or img.get("href") or ""
        norm = normalize_src(src, doc.name)
        mapped = src_map.get(norm) or src_map.get(Path(norm).name) or src_map.get(src)
        context = img_context(img)
        keep = should_keep_image(img, settings, context, src, ai_client=ai_client, ai_model=ai_model, ai_provider=ai_provider, log=log)
        if not keep:
            block = img.find_parent(["figure", "div", "p", "section"])
            log.removed_images.append((context or src)[:240])
            if block and len(clean_text(block.get_text(" "))) < 700:
                strip_tag(block)
            else:
                strip_tag(img)
            continue
        if not mapped:
            log.warn(f"Image source not found and removed: {src}")
            strip_tag(img)
            continue
        img["src"] = mapped
        img.attrs.pop("href", None)
        log.kept_images.append(f"{mapped}: {context[:160]}")
    remove_orphan_image_captions(soup, log)


def remove_orphan_image_captions(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["figcaption", "p", "div"])):
        if tag.find("img"):
            continue
        text = clean_text(tag.get_text(" "))
        if not text or len(text) >= 700:
            continue
        is_figcaption = tag.name == "figcaption"
        class_text = " ".join(tag.get("class", [])) if isinstance(tag.get("class", []), list) else str(tag.get("class", ""))
        caption_class = bool(re.search(r"caption|fig|image|photo|plate", class_text, re.I))
        caption_front = clean_text(text[:240])
        caption_like = bool(PLATE_CAPTION_PATTERNS.search(caption_front))
        starts_like_caption = bool(re.match(r"^(the )?author[’']?s |^(portrait|photo|photograph|frontispiece|illustration|"
                                            r"birthplace|grave|tomb|statue|monument|museum|translated by)\b",
                                            caption_front, flags=re.I))
        if caption_like and (is_figcaption or caption_class or starts_like_caption or visible_word_count(text) <= 18):
            log.removed("Removed orphan plate caption", text)
            strip_tag(tag)


def remove_empty_layout_shells(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in reversed(list(soup.find_all(["figure", "div", "section"]))):
        if tag.find(["img", "svg", "table"]):
            continue
        text = clean_text(tag.get_text(" "))
        if not text:
            strip_tag(tag)
        elif len(text) < 700 and PLATE_CAPTION_PATTERNS.search(text):
            log.removed("Removed empty plate shell", text)
            strip_tag(tag)


def remove_duplicate_current_work_title_line(soup: BeautifulSoup, current_work: Optional[str], doc: SpineDoc, log: BuildLog) -> None:
    if not current_work or doc.kind in {"major_work", "play", "backmatter", "division"}:
        return
    body = soup.body or soup
    first = first_significant_tag(body)
    if not isinstance(first, Tag) or first.name not in {"p", "div"}:
        return
    text = clean_text(first.get_text(" "))
    if visible_word_count(text) <= 10 and normalized_title_key(text) == normalized_title_key(current_work):
        log.removed("Removed duplicate current-work title line", text)
        strip_tag(first)


def split_br_verse_block(tag: Tag, soup: BeautifulSoup, log: BuildLog) -> None:
    """Convert a p/div with <br> lineation into a verse block with line spans."""
    lines: list[str] = []
    cur = ""
    for child in list(tag.children):
        if isinstance(child, Tag) and child.name == "br":
            lines.append(clean_text(cur))
            cur = ""
        else:
            cur += child.get_text(" ") if isinstance(child, Tag) else str(child)
    if clean_text(cur):
        lines.append(clean_text(cur))
    lines = [x for x in lines if x]
    if len(lines) < 2:
        return
    block = soup.new_tag("div")
    block["class"] = ["verse-block"]
    for line in lines:
        span = soup.new_tag("span")
        span["class"] = ["verse-line"]
        span.string = line
        block.append(span)
    tag.replace_with(block)
    log.detected_poetry_blocks += 1


def normalize_poetry(soup: BeautifulSoup, doc: SpineDoc, log: BuildLog) -> None:
    for tag in list(soup.find_all(["p", "div"])):
        if len(tag.find_all("br")) >= 2 and len(clean_text(tag.get_text(" "))) < 2200:
            split_br_verse_block(tag, soup, log)
            continue
        classes = " ".join(tag.get("class", [])) if isinstance(tag.get("class", []), list) else str(tag.get("class", ""))
        if POETRY_CLASS_RE.search(classes):
            tag["class"] = list(set((tag.get("class", []) if isinstance(tag.get("class", []), list) else [tag.get("class")]) + ["verse-block"]))
            log.detected_poetry_blocks += 1

    # Group runs of short paragraphs into verse blocks only when document is poetry-like.
    if not doc.contains_poetry:
        return
    body = soup.body or soup
    run: list[Tag] = []
    def flush() -> None:
        nonlocal run
        if len(run) >= 4:
            block = soup.new_tag("div")
            block["class"] = ["verse-block", "verse-sequence"]
            for p in run:
                line = clean_text(p.get_text(" "))
                if not line:
                    continue
                span = soup.new_tag("span")
                span["class"] = ["verse-line"]
                span.string = line
                block.append(span)
            run[0].insert_before(block)
            for p in run:
                strip_tag(p)
            log.detected_poetry_sequences += 1
        run = []
    for child in list(body.descendants):
        if not isinstance(child, Tag) or child.name != "p":
            continue
        if child.find_parent(["blockquote", "table", "div"]):
            continue
        t = clean_text(child.get_text(" "))
        if 2 <= len(t) <= 76 and not t.endswith(".") and not CHAPTER_HEADINGS.match(t):
            run.append(child)
        else:
            flush()
    flush()


def normalize_cast_and_drama(soup: BeautifulSoup, log: BuildLog) -> None:
    for h in list(soup.find_all(re.compile(r"^h[1-6]$"))):
        text = clean_text(h.get_text(" "))
        if CAST_HEADINGS.match(text):
            h["class"] = add_classes(h, ["cast-heading", "formal-opener"])
            log.detected_cast_sections += 1
            cur = h.next_sibling
            while cur is not None:
                nxt = cur.next_sibling
                if isinstance(cur, Tag):
                    if re.match(r"h[1-6]", cur.name or ""):
                        ht = clean_text(cur.get_text(" "))
                        if ACT_SCENE_HEADINGS.match(ht):
                            cur["class"] = add_classes(cur, ["act-opening"])
                        break
                    if cur.name in {"p", "div", "ul", "ol", "table"}:
                        cur["class"] = add_classes(cur, ["cast-list"])
                        normalize_cast_entries(cur, log)
                cur = nxt
        elif ACT_SCENE_HEADINGS.match(text):
            h["class"] = add_classes(h, ["act-scene-heading"])
    for p in soup.find_all("p"):
        t = clean_text(p.get_text(" "))
        if re.match(r"^\[.*\]$|^\(.*\)$", t) and len(t) < 240:
            p["class"] = add_classes(p, ["stage-direction"])


def normalize_cast_entries(container: Tag, log: BuildLog) -> None:
    # Mostly CSS-driven, but add a marker class when entries look like NAME - description.
    for el in container.find_all(["p", "li"], recursive=True):
        t = clean_text(el.get_text(" "))
        if not t or len(t) > 240:
            continue
        if re.match(r"^[A-Z][A-Z .'-]{2,35}([—–-]|,|\s{2,})", t) or t.isupper():
            el["class"] = add_classes(el, ["cast-entry"])
            log.normalized_cast_entries += 1


def add_classes(tag: Tag, classes: list[str]) -> list[str]:
    existing = tag.get("class", [])
    if isinstance(existing, str):
        existing = existing.split()
    return list(dict.fromkeys(existing + classes))


def normalize_notes_refs(soup: BeautifulSoup) -> None:
    for a in soup.find_all("a"):
        href = str(a.get("href", ""))
        text = clean_text(a.get_text(" "))
        if ("note" in href.lower() or "fn" in href.lower()) and re.match(r"^\[?\d+\]?$", text):
            sup = soup.new_tag("sup")
            sup["class"] = ["note-ref"]
            sup.string = text.strip("[]")
            a.clear()
            a.append(sup)


def promote_paragraph_headings(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["p", "div"])):
        if tag.find_parent(["blockquote", "table", "figcaption"]):
            continue
        if tag.find(["img", "svg", "table"]):
            continue
        text = clean_display_title(tag.get_text(" "))
        if not text or len(text) > 80 or visible_word_count(text) > 8:
            continue
        if not CHAPTER_HEADINGS.match(text):
            continue
        anchors = tag.find_all("a")
        first_anchor_id = ""
        for a in anchors:
            first_anchor_id = str(a.get("id") or "").strip()
            if first_anchor_id:
                break
        tag.name = "h2"
        tag["class"] = add_classes(tag, ["subdivision"])
        if first_anchor_id and not tag.get("id"):
            tag["id"] = first_anchor_id
        tag.clear()
        tag.string = text


def normalize_headings(soup: BeautifulSoup, doc: SpineDoc, toc: list[TocEntry], used_ids: set[str], current_work: Optional[str], current_division: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    doc_major_key = normalized_title_key(doc.major_title)
    doc_division_key = normalized_title_key(doc.current_division)
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        text = clean_display_title(h.get_text(" "))
        if not text:
            strip_tag(h)
            continue
        # Replace EPUB-internal anchor/footnote-marker clutter with a clean print heading.
        h.clear()
        h.string = text
        level = int(h.name[1])
        text_key = normalized_title_key(text)
        is_division = (
            COLLECTION_DIVISIONS.match(text) is not None
            or (doc.kind == "division" and bool(doc_division_key) and text_key == doc_division_key)
            or (doc.kind == "division" and bool(doc_major_key) and text_key == doc_major_key and not re.match(r"^part\s+", text, re.I))
        )
        is_backmatter = BACKMATTER_PATTERNS.match(text) is not None or doc.kind == "backmatter"
        is_chapterish = CHAPTER_HEADINGS.match(text) is not None
        is_major = False
        if level == 1 and not is_chapterish:
            is_major = True
        if doc_major_key and doc_major_key == text_key and doc.kind in {"major_work", "play", "poetry", "backmatter"}:
            is_major = True
        # Collected-work EPUBs often encode a parent division as h1 ("The Novels")
        # and the actual work as h2 ("Poor Folk"). Those h2 work headings must
        # drive runners/openers; ordinary chapterish h2 headings must not.
        if level == 2 and current_division and not is_chapterish and len(text) < 90:
            is_major = True
        if is_division:
            current_division = text
            is_major = True
            h["class"] = add_classes(h, ["collection-division", "formal-opener"])
        elif is_backmatter:
            current_work = text
            h["class"] = add_classes(h, ["backmatter-opener", "formal-opener"])
        elif is_major:
            current_work = text
            h["class"] = add_classes(h, ["major-work", "formal-opener"])
        elif level == 2:
            h["class"] = add_classes(h, ["subdivision"])
        else:
            h["class"] = add_classes(h, ["minor-heading"])
        ident = h.get("id") or unique_id(text, used_ids)
        if ident in used_ids:
            ident = unique_id(text, used_ids)
        else:
            used_ids.add(str(ident))
        h["id"] = ident
        # TOC policy: include divisions, major works, backmatter, and meaningful h2; exclude tiny chapterish h2 unless complete TOC is requested by design.
        if is_division:
            toc.append(TocEntry(1, text, str(ident), "division"))
        elif is_backmatter or is_major:
            toc.append(TocEntry(1 if not current_division else 2, text, str(ident), "backmatter" if is_backmatter else "work"))
        elif level == 2 and not is_chapterish and len(text) < 90:
            toc.append(TocEntry(3 if current_division else 2, text, str(ident), "work"))
    return current_work, current_division


def add_synthetic_opener_if_needed(soup: BeautifulSoup, doc: SpineDoc, toc: list[TocEntry], used_ids: set[str], current_work: Optional[str]) -> Optional[str]:
    if not doc.major_title:
        return current_work
    title = clean_display_title(doc.major_title)
    title_key = normalized_title_key(title)
    current_key = normalized_title_key(current_work)
    if current_key and current_key == title_key:
        return current_work
    # OpenAI plans often annotate ordinary chapter fragments with the current major
    # work title. That should inform running heads, not create a fresh display page.
    if doc.kind not in {"major_work", "play", "backmatter"}:
        return current_work or title
    first_text = clean_text((soup.body or soup).get_text(" "))[:350].lower()
    if title.lower() in first_text:
        return current_work or title
    ident = unique_id(title, used_ids)
    h = soup.new_tag("h1")
    h["class"] = ["major-work", "formal-opener", "synthetic-opener"]
    h["id"] = ident
    h.string = title
    body = soup.body or soup
    body.insert(0, h)
    toc.append(TocEntry(1, title, ident, "work"))
    return title


def fragment_is_title_only(frag: str, settings: Settings) -> bool:
    soup = BeautifulSoup(frag, "lxml")
    if soup.find(["img", "svg", "table"]):
        return False
    text = clean_text(soup.get_text(" "))
    if not text:
        return True
    words = visible_word_count(text)
    key = normalized_title_key(text)
    title_key = normalized_title_key(settings.title)
    # Typical duplicate spine fragments contain only the book/work title, sometimes
    # with a generic subtitle like "A Novel". Keep real notes/prefaces because they
    # exceed these length thresholds.
    if title_key and key == title_key and words <= 14:
        return True
    if title_key and key.startswith(title_key) and words <= 18:
        tail = key[len(title_key):].strip()
        if not tail or tail in {"a novel", "novel", "book"}:
            return True
    # No real paragraphs, just short headings/title strings.
    bodyish = [clean_text(x.get_text(" ")) for x in soup.find_all(["p", "blockquote", "li"])]
    bodyish = [x for x in bodyish if x]
    if words <= 20 and not any(visible_word_count(x) > 10 for x in bodyish):
        return True
    return False


def first_significant_tag(body: Tag | BeautifulSoup) -> Optional[Tag]:
    for child in body.children:
        if isinstance(child, NavigableString):
            if clean_text(str(child)):
                return None
            continue
        if isinstance(child, Tag):
            if clean_text(child.get_text(" ")) or child.find(["img", "svg", "table"]):
                return child
    return None


def clean_document(doc: SpineDoc, src_map: dict[str, str], settings: Settings, toc: list[TocEntry], used_ids: set[str], current_work: Optional[str], current_division: Optional[str], log: BuildLog, ai_client=None, ai_model: str = "gpt-5.4-mini", ai_provider: str = "openai") -> tuple[str, Optional[str], Optional[str]]:
    soup = parse_html(doc.raw)
    remove_comments_scripts_styles(soup)
    strip_bad_attributes(soup)
    remove_local_mini_tocs(soup, log)
    remove_compact_local_contents_blocks(soup, log)
    remove_promotional_blocks(soup, log)
    rewrite_images(soup, src_map, doc, settings, log, ai_client=ai_client, ai_model=ai_model, ai_provider=ai_provider)
    remove_empty_layout_shells(soup, log)
    unwrap_useless_inline_tags(soup)
    if settings.smart_punctuation:
        simple_typographic_cleanup(soup, log)
    normalize_notes_refs(soup)
    normalize_poetry(soup, doc, log)
    remove_compact_local_contents_blocks(soup, log)
    normalize_cast_and_drama(soup, log)
    promote_paragraph_headings(soup, log)
    current_work = add_synthetic_opener_if_needed(soup, doc, toc, used_ids, current_work)
    current_work, current_division = normalize_headings(soup, doc, toc, used_ids, current_work, current_division)
    remove_duplicate_current_work_title_line(soup, current_work, doc, log)
    remove_empty_layout_shells(soup, log)

    body = soup.body or soup
    for tag in list(body.find_all(["p", "div"])):
        if tag.find(["img", "svg", "table", "span"]):
            continue
        if not clean_text(tag.get_text(" ")):
            strip_tag(tag)

    wrapper_classes = ["epub-doc"]
    if doc.kind:
        wrapper_classes.append(f"doc-{doc.kind}")
    first_tag = first_significant_tag(body)
    if isinstance(first_tag, Tag):
        first_classes = first_tag.get("class", [])
        if isinstance(first_classes, str):
            first_classes = first_classes.split()
        if "major-work" in first_classes or "collection-division" in first_classes or "backmatter-opener" in first_classes:
            wrapper_classes.append("starts-major-work")
        elif first_tag.name in {"h1", "h2", "h3"}:
            ft = clean_text(first_tag.get_text(" "))
            if CHAPTER_HEADINGS.match(ft) or "subdivision" in first_classes:
                wrapper_classes.append("starts-chapter-opener")
    if doc.contains_poetry:
        wrapper_classes.append("contains-poetry")
    if doc.contains_drama:
        wrapper_classes.append("contains-drama")
    attrs = f'class="{" ".join(wrapper_classes)}"'
    if current_work:
        attrs += f' data-current-work="{html.escape(current_work)}"'
    frag = "\n".join(str(child) for child in body.children if str(child).strip())
    current_marker = f'<span class="set-current-work">{html.escape(current_work)}</span>\n' if current_work else ""
    return f'<!-- source: {html.escape(doc.href)} -->\n<section {attrs}>\n{current_marker}{frag}\n</section>', current_work, current_division


def sample_word_budget(sample_pages: int) -> int:
    """Approximate body words needed for a print sample before expensive PDF layout.

    The final PDF is still capped exactly after rendering, but this prevents sample
    mode from laying out thousands of pages for large complete-works EPUBs.
    """
    if sample_pages <= 0:
        return 0
    return max(6000, sample_pages * 700)


# --------------------------------------------------------------------------------------
# CSS and book composer
# --------------------------------------------------------------------------------------


def build_toc(toc: list[TocEntry], settings: Optional[Settings] = None, page_numbers: Optional[dict[str, int]] = None) -> str:
    """Build a practical print TOC, aggressively suppressing duplicate ebook headings.

    EPUBs often repeat the book/work title in nav docs, local mini-TOCs, title pages,
    and spine fragments. A print TOC must include each structural entry once.
    """
    if not toc:
        return '<p class="no-indent toc-empty">No reliable table of contents could be inferred from the EPUB structure.</p>'

    book_key = normalized_title_key(settings.title if settings else "")
    explicit_numbers = page_numbers is not None
    list_class = "toc-list toc-explicit" if explicit_numbers else "toc-list"
    out = [f'<ol class="{list_class}">']
    seen_keys: set[str] = set()
    title_seen = False
    emitted = 0
    duplicate_counts: dict[str, int] = {}

    for e in toc:
        title = clean_display_title(e.title)
        if not title:
            continue
        key = normalized_title_key(title)
        if not key:
            continue
        duplicate_counts[key] = duplicate_counts.get(key, 0) + 1

        # Suppress repeated copies of the collection/book title. The title page
        # already carries it; the print TOC should lead with useful divisions.
        if key == book_key:
            title_seen = True
            continue

        # Suppress identical titles even if they have different generated ids. This is almost
        # always an EPUB/nav artifact in collected or public-domain exports.
        if key in seen_keys:
            continue
        seen_keys.add(key)

        level = min(max(e.level, 1), 4)
        target = html.escape(e.target_id)
        if explicit_numbers:
            page_no = page_numbers.get(e.target_id) if page_numbers else None
            page_text = "" if page_no is None else str(page_no)
            out.append(
                f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)}">'
                f'<a href="#{target}"><span class="toc-entry-title">{html.escape(title)}</span>'
                f'<span class="toc-leader" aria-hidden="true"></span>'
                f'<span class="toc-page-number">{html.escape(page_text)}</span></a></li>'
            )
        else:
            out.append(f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)}"><a href="#{target}">{html.escape(title)}</a></li>')
        emitted += 1

    if emitted == 0:
        return '<p class="no-indent toc-empty">No reliable table of contents could be inferred from the EPUB structure.</p>'
    out.append("</ol>")
    return "\n".join(out)


def css_text(settings: Settings, font_face_css: str = "") -> str:
    title = settings.title.replace('"', '\\"')
    fs = settings.font_stack
    hyphens = "auto" if settings.hyphenate else "none"
    prose_align = "justify" if settings.justify_prose else "left"
    runner_layout = settings.runner_layout.strip().lower()
    runner_rule_style = settings.runner_rule_style.strip().lower()
    body_top_mm = settings.margin_top_mm + max(0.0, settings.runner_body_clearance_mm)
    rule_css = ""
    if runner_rule_style != "none":
        rule_css = f"border-bottom: {settings.runner_rule_weight_pt}pt solid {settings.runner_rule_color}; padding-bottom: {settings.runner_rule_gap_mm}mm;"
    split_rule_css = rule_css if runner_rule_style == "split" else ""
    if runner_layout == "right_title_full_rule":
        body_left_header_css = f'''
  @top-left {{
    content: string(collection-title);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
    color: #111; vertical-align: top; text-align: left; padding-top: {settings.runner_title_top_mm}mm;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}'''
        body_right_header_css = f'''
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{
    content: string(current-work);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; vertical-align: top; text-align: right; padding-top: {settings.runner_title_top_mm}mm;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}'''
    elif runner_layout == "centered_single_rule":
        body_left_header_css = f'''
  @top-left {{ content: normal; }}
  @top-center {{
    content: string(current-work);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {rule_css} vertical-align: bottom; text-align: center;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}
  @top-right {{ content: normal; }}'''
        body_right_header_css = body_left_header_css
    elif runner_layout != "alternating":
        body_left_header_css = f'''
  @top-left {{
    content: string(collection-title);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
    color: #111; {split_rule_css} vertical-align: bottom;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}
  @top-center {{
    content: " ";
    {rule_css if runner_rule_style == "single" else ""}
    vertical-align: bottom; line-height: 1;
  }}
  @top-right {{
    content: string(current-work);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {split_rule_css} vertical-align: bottom;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}'''
        body_right_header_css = body_left_header_css
    else:
        body_left_header_css = f'''
  @top-left {{
    content: string(collection-title);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
    color: #111; {rule_css} vertical-align: bottom;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}'''
        body_right_header_css = f'''
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{
    content: string(current-work);
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {rule_css} vertical-align: bottom;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}'''
    base_css = f"""
@page {{
  size: {settings.trim_size};
  margin: {settings.front_margin_top_mm}mm {settings.margin_side_mm}mm {settings.front_margin_bottom_mm}mm {settings.margin_side_mm}mm;
}}
@page :blank {{
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}
  @bottom-center {{ content: normal; }}
}}
@page title {{
  size: {settings.trim_size};
  margin: {settings.front_margin_top_mm}mm {settings.margin_side_mm}mm {settings.front_margin_bottom_mm}mm {settings.margin_side_mm}mm;
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}
  @bottom-center {{ content: normal; }}
}}
@page front {{
  size: {settings.trim_size};
  margin: {settings.front_margin_top_mm}mm {settings.margin_side_mm}mm {settings.front_margin_bottom_mm}mm {settings.margin_side_mm}mm;
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}
  @bottom-center {{ content: counter(page, lower-roman); font-family: {fs}; font-size: {settings.front_folio_font_pt}pt; color: #222; }}
}}
@page front:first {{
  @bottom-center {{ content: normal; }}
}}
@page body {{
  size: {settings.trim_size};
  margin: {body_top_mm}mm {settings.margin_side_mm}mm {settings.margin_bottom_mm}mm {settings.margin_side_mm}mm;
  counter-increment: bodyPage;
  @bottom-center {{ content: counter(bodyPage); font-family: {fs}; font-size: {settings.folio_font_pt}pt; color: #222; }}
}}
@page body:left {{
{body_left_header_css}
}}
@page body:right {{
{body_right_header_css}
}}
@page opener {{
  size: {settings.trim_size};
  margin: {settings.margin_top_mm}mm {settings.margin_side_mm}mm {settings.margin_bottom_mm}mm {settings.margin_side_mm}mm;
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}
  counter-increment: bodyPage;
  @bottom-center {{ content: counter(bodyPage); font-family: {fs}; font-size: {settings.folio_font_pt}pt; color: #222; }}
}}
@page nofolio {{
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}
  @bottom-center {{ content: normal; }}
  counter-increment: bodyPage;
}}
html {{
  font-family: {fs};
  font-size: {settings.body_size_pt}pt;
  line-height: {settings.line_height};
  color: {settings.text_color};
  font-weight: {settings.body_font_weight};
  hyphens: {hyphens};
}}
body {{ margin: 0; }}
.set-collection {{ string-set: collection-title content(); height: 0; overflow: hidden; }}
.set-current-work {{ string-set: current-work content(); display: block; height: 0; overflow: hidden; font-size: 0; line-height: 0; color: transparent; }}
.frontmatter {{ page: front; }}
.half-title-page {{ page: title; }}
.title-page, .source-page {{ page: title; break-before: right; }}
.half-title-page {{ display: flex; align-items: center; justify-content: center; height: 220mm; }}
.half-title-page h1 {{ font-size: 22pt; font-weight: 400; letter-spacing: .06em; text-transform: uppercase; text-align: center; }}
.title-page {{ text-align: center; padding-top: 82mm; }}
.title-page h1 {{ font-size: 30pt; line-height: 1.08; font-weight: 400; letter-spacing: .035em; text-transform: uppercase; }}
.title-page .subtitle {{ margin-top: 12mm; font-size: 12pt; letter-spacing: .08em; text-transform: uppercase; }}
.source-page {{ padding-top: 92mm; text-align: center; font-size: 10.3pt; }}
.toc-page {{ page: front; break-before: right; }}
.toc-page h1 {{ margin: 0 0 14mm; text-align: center; font-size: {settings.toc_title_font_pt}pt; font-weight: 400; letter-spacing: .08em; text-transform: uppercase; }}
.toc-list {{ list-style: none; padding: 0; margin: 0; }}
.toc-list li {{ margin: 0 0 {settings.toc_entry_gap_mm}mm 0; line-height: {settings.toc_line_height}; }}
.toc-list a {{ color: inherit; text-decoration: none; }}
.toc-list a::after {{ content: leader('.') target-counter(attr(href), page); }}
.toc-explicit a {{ display: grid; grid-template-columns: auto 1fr auto; column-gap: .7em; align-items: baseline; }}
.toc-explicit a::after {{ content: none; }}
.toc-entry-title {{ min-width: 0; }}
.toc-leader {{ border-bottom: .45pt dotted #444; transform: translateY(-.18em); }}
.toc-page-number {{ min-width: 2.2em; text-align: right; }}
.toc-level-1 {{ font-size: {settings.toc_level_1_font_pt}pt; text-transform: uppercase; letter-spacing: .035em; margin-top: 4.8mm !important; }}
.toc-level-2 {{ margin-left: 8mm !important; font-size: {settings.toc_level_2_font_pt}pt; }}
.toc-level-3 {{ margin-left: 14mm !important; font-size: {settings.toc_level_3_font_pt}pt; }}
.toc-level-4 {{ margin-left: 19mm !important; font-size: {settings.toc_level_4_font_pt}pt; }}
.main a {{ color: inherit; text-decoration: none; }}
.main {{ page: body; counter-reset: bodyPage 0; break-before: right; string-set: collection-title "{title}"; }}
.body-page-reset {{ counter-reset: bodyPage 0; height: 0; line-height: 0; font-size: 0; page: body; }}
.epub-doc.starts-major-work {{ page: opener; break-before: right; }}
.epub-doc.starts-chapter-opener {{ break-before: auto; }}
.true-blank {{ page: nofolio; break-before: page; break-after: page; height: 0; }}
h1, h2, h3, h4, h5, h6 {{ font-weight: 400; break-after: avoid; page-break-after: avoid; hyphens: none; }}
h1.major-work, h2.major-work, h1.collection-division, h1.backmatter-opener {{
  break-before: right; string-set: current-work content();
  margin: {settings.major_opener_top_margin_mm}mm 0 {settings.major_opener_bottom_margin_mm}mm; text-align: center; font-size: {settings.major_work_font_pt}pt; line-height: 1.08; letter-spacing: .04em; text-transform: uppercase;
}}
.epub-doc.starts-major-work > h1.major-work,
.epub-doc.starts-major-work > h2.major-work,
.epub-doc.starts-major-work > h1.collection-division,
.epub-doc.starts-major-work > h1.backmatter-opener {{ break-before: auto; }}
h1.collection-division {{ font-size: {settings.collection_division_font_pt}pt; letter-spacing: .07em; }}
h2.subdivision {{ break-after: avoid-page; page-break-after: avoid; margin: {settings.subdivision_margin_top_mm}mm 0 {settings.subdivision_margin_bottom_mm}mm; text-align: center; font-size: {settings.subdivision_font_pt}pt; letter-spacing: .035em; }}
h2.subdivision + p {{ break-before: avoid-page; }}
h2.act-opening, h2.act-scene-heading, h3.act-scene-heading {{ break-before: page; margin: 22mm 0 7mm; text-align: center; font-size: 14pt; letter-spacing: .06em; text-transform: uppercase; }}
h3 {{ margin: 8mm 0 4mm; text-align: center; font-size: {settings.h3_font_pt}pt; }}
h4, h5, h6, .minor-heading {{ margin: 6mm 0 3mm; text-align: center; font-size: {settings.minor_heading_font_pt}pt; font-style: italic; }}
p {{ margin: 0; text-align: {prose_align}; text-indent: {settings.paragraph_indent_em}em; widows: 2; orphans: 2; }}
h1 + p, h2 + p, h3 + p, h4 + p, .no-indent, blockquote p:first-child, .stage-direction, .cast-list p {{ text-indent: 0; }}
p + p {{ margin-top: 0; }}
blockquote {{ margin: 4mm {settings.blockquote_side_margin_mm}mm; font-size: {settings.blockquote_font_percent}%; }}
hr {{ border: 0; border-top: .45pt solid #888; margin: 9mm auto; width: 35%; }}
.verse-block {{
  margin: {settings.verse_block_margin_top_mm}mm auto {settings.verse_block_margin_bottom_mm}mm; max-width: {settings.verse_max_width_mm}mm; line-height: {settings.verse_line_height}; text-align: left !important; text-indent: 0 !important; hyphens: none;
  break-inside: auto;
}}
.verse-line {{ display: block; text-indent: -{settings.verse_hanging_indent_em}em; padding-left: {settings.verse_hanging_indent_em}em; white-space: pre-wrap; }}
.verse-block + .verse-block {{ margin-top: 2mm; }}
.contains-poetry p {{ text-align: {prose_align}; }}
.contains-poetry .verse-block,
.contains-poetry .verse-block p,
.contains-poetry .verse-line {{ text-align: left !important; }}
.cast-heading {{ page: opener; break-before: page; text-align: center; text-transform: uppercase; letter-spacing: .06em; margin-top: 30mm; }}
.cast-list {{ text-align: left !important; text-indent: 0 !important; max-width: {settings.cast_max_width_mm}mm; margin: 0 auto 5mm; line-height: {settings.cast_line_height}; }}
.cast-list p, .cast-list li, .cast-entry {{ text-align: left !important; text-indent: 0 !important; }}
.stage-direction {{ text-align: center !important; font-style: italic; margin: 2.5mm auto; max-width: 120mm; }}
.note-ref {{ font-size: 67%; line-height: 0; vertical-align: super; }}
img {{ max-width: 100%; height: auto; display: block; margin: 5mm auto; }}
figure {{ margin: 6mm auto; text-align: center; }}
figcaption {{ font-size: 9.4pt; text-align: center; margin-top: 2mm; }}
table {{ border-collapse: collapse; margin: 5mm auto; max-width: 100%; font-size: 9.6pt; }}
td, th {{ padding: 1.3mm 2mm; vertical-align: top; }}
/* Hard normalization against ebook inherited wrappers and narrow columns. */
body, main, section, article, div {{ max-width: none; }}
[style] {{ max-width: none !important; width: auto !important; margin-left: initial !important; margin-right: initial !important; }}
"""
    if font_face_css.strip():
        return font_face_css.strip() + "\n\n" + base_css
    return base_css


def compose_html(settings: Settings, fragments: list[str], toc: list[TocEntry], toc_page_numbers: Optional[dict[str, int]] = None, font_face_css: str = "") -> tuple[str, str]:
    toc_html = build_toc(toc, settings, toc_page_numbers)
    body = "\n".join(fragments)
    subtitle_html = f'<div class="subtitle">{html.escape(settings.title_page_subtitle)}</div>' if clean_text(settings.title_page_subtitle) else ""
    source_html = (
        f'<section class="frontmatter source-page"><p class="no-indent">{html.escape(settings.source_note_text)}</p></section>'
        if settings.include_source_note and clean_text(settings.source_note_text) else ""
    )
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(settings.title)}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<section class="frontmatter half-title-page"><h1>{html.escape(settings.title)}</h1></section>
<section class="frontmatter title-page"><h1>{html.escape(settings.title)}</h1>{subtitle_html}</section>
{source_html}
<section class="frontmatter toc-page"><h1>Contents</h1>{toc_html}</section>
<main class="main">
{body}
</main>
</body>
</html>"""
    return doc, css_text(settings, font_face_css=font_face_css)

# --------------------------------------------------------------------------------------
# PDF render/optimization/preflight
# --------------------------------------------------------------------------------------


def write_build(build_dir: Path, html_doc: str, css: str) -> None:
    (build_dir / "book.html").write_text(html_doc, encoding="utf-8")
    (build_dir / "style.css").write_text(css, encoding="utf-8")


def render_pdf(build_dir: Path, out_pdf: Path) -> None:
    HTML(filename=str(build_dir / "book.html"), base_url=str(build_dir)).write_pdf(
        str(out_pdf), stylesheets=[CSS(filename=str(build_dir / "style.css"))]
    )


def parse_hex_color(value: str) -> tuple[float, float, float]:
    text = clean_text(value).lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (0.0, 0.0, 0.0)
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
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
    y = settings.runner_rule_y_mm * PT_PER_MM
    x0 = settings.margin_side_mm * PT_PER_MM
    x1 = A4_WIDTH_PT - x0
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


def find_first_body_page_index(doc) -> Optional[int]:
    """Return the zero-based page index where Arabic body folio 1 is visible."""
    toc_indices = [
        i for i in range(min(doc.page_count, 30))
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


def resolve_toc_page_numbers(pdf_path: Path, toc: list[TocEntry], log: BuildLog) -> dict[str, int]:
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
        for page_index in range(min(doc.page_count, 40)):
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
            log.warn("Could not detect visible body folio 1; using earliest TOC target as body page 1.")

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


def optimize_pdf(path: Path) -> None:
    try:
        import pikepdf
    except Exception:
        return
    tmp = path.with_suffix(".optimized.tmp.pdf")
    with pikepdf.open(path) as pdf:
        pdf.save(tmp, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate, linearize=True)
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
    s = line.strip().strip(".,;:!?()[]{}'\"“”‘’—–-")
    if not s:
        return False
    if ROMAN_RE.match(s) or s.isdigit():
        return False
    if re.fullmatch(r"[bcdefghjklmnopqrstuvwxyz]", s):
        return True
    if re.match(r"^[a-z](-)?$", s):
        return True
    return False


def render_selected_pages(pdf_path: Path, qa_dir: Path, prefix: str = "page", max_pages: int = 12, jpg: bool = False) -> list[Path]:
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


def analyze_header_clearance(page, page_no: int, settings: Optional[Settings] = None) -> Optional[dict[str, Any]]:
    # Heuristic: after top margin, first body text should not start too high. This catches runner-rule collisions.
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
            # Ignore folios and top running heads; the check is about live text
            # crowding the runner rule, not the runner text itself.
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
        min_first = (settings.runner_rule_y_mm + max(8.0, settings.runner_body_clearance_mm)) * PT_PER_MM
    else:
        min_first = 74.0
    if first < min_first:
        return {"page": page_no, "first_text_y_pt": round(first, 2), "issue": "body text may be too close to running head/rule"}
    return None


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
    median = sorted(widths)[len(widths)//2]
    if median < 300:  # A4 text block should be far wider than this except poetry/cast pages.
        text = clean_text(page.get_text("text"))[:200]
        if not re.search(r"dramatis|characters|act |scene |contents", text, re.I):
            return {"page": page_no, "median_text_block_width_pt": round(median, 1), "issue": "possible narrow ebook column"}
    return None


def preflight_pdf(pdf_path: Path, out_dir: Path, log: BuildLog, settings: Optional[Settings] = None, render_pngs: bool = True) -> tuple[QAVerdict, Path, Path]:
    import fitz
    doc = fitz.open(pdf_path)
    verdict = QAVerdict(page_count=doc.page_count)
    first_body_index = find_first_body_page_index(doc)
    title_key = normalized_title_key(settings.title if settings else "")

    for i, page in enumerate(doc):
        rect = page.rect
        if abs(rect.width - A4_WIDTH_PT) > 2 or abs(rect.height - A4_HEIGHT_PT) > 2:
            verdict.non_a4_pages.append({"page": i + 1, "width_pt": round(rect.width, 2), "height_pt": round(rect.height, 2)})

    fonts = set()
    images_seen = 0
    for i in range(doc.page_count):
        try:
            for f in doc.get_page_fonts(i):
                fonts.add(str(f[3]))
        except Exception:
            pass
        try:
            images_seen += len(doc.get_page_images(i))
        except Exception:
            pass
    verdict.fonts_seen = sorted(fonts)
    verdict.images_seen = images_seen

    for i, page in enumerate(doc):
        lines = line_text_from_page(page)
        bad = [x for x in lines if looks_like_bad_spill(x)]
        if bad:
            verdict.possible_line_spills.append({"page": i + 1, "lines": bad[:8]})
        text = clean_text(page.get_text("text"))
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
        drawings = page.get_drawings()
        imgs = page.get_images(full=True)
        if not text and (drawings or imgs):
            verdict.possible_blank_page_artifacts.append({"page": i + 1, "drawings": len(drawings), "images": len(imgs), "issue": "blank-looking page contains graphical objects"})

    # Catch title-only/generated pages that are not true blanks: runner + folio only.
    for i, page in enumerate(doc):
        if first_body_index is not None and i < first_body_index:
            continue
        text = clean_text(page.get_text("text"))
        if not text:
            continue
        words = visible_word_count(text)
        key = normalized_title_key(text)
        # Ignore legitimate front display pages; they are usually within the first few pages.
        if i >= 4 and words <= 12 and title_key and (key == title_key or key.startswith(title_key)):
            verdict.empty_content_pages.append({"page": i + 1, "text": text[:180], "issue": "page contains only title/running-head/folio-like text"})

    # Detect TOC duplicate explosions and unresolved/constant target-counter page numbers.
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
        normalized_lines = [normalized_title_key(re.sub(r"\.{2,}\s*\d+\s*$", "", x)) for x in toc_lines]
        counts: dict[str, int] = {}
        for k in normalized_lines:
            if k and k not in {"contents"}:
                counts[k] = counts.get(k, 0) + 1
        dupes = {k: v for k, v in counts.items() if v >= 4}
        if dupes:
            verdict.toc_duplicate_warnings.append("TOC appears to contain repeated duplicate entries: " + json.dumps(dupes, ensure_ascii=False))
        page_nums = re.findall(r"\.{2,}\s*(\d+)\s*$", toc_text, flags=re.M)
        if len(page_nums) >= 8 and len(set(page_nums)) <= 2:
            verdict.toc_page_number_warnings.append("Many TOC entries resolve to the same page number; inspect target ids and body page-counter reset.")

    # The first content page after the TOC must normally show Arabic folio 1, not
    # the physical PDF page number. This is heuristic but catches the exact bad sample.
    toc_page_indices = [i for i in range(min(doc.page_count, 20)) if re.search(r"\bCONTENTS\b|\bContents\b", doc[i].get_text("text"))]
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

    # Basic TOC page number sanity: TOC should contain at least some trailing numbers/dot leaders.
    first_pages_text = "\n".join(doc[i].get_text("text") for i in range(min(doc.page_count, 12)))
    if "Contents" in first_pages_text and not re.search(r"\.{2,}\s*\d+", first_pages_text):
        verdict.toc_page_number_warnings.append("Could not detect dot leaders/page numbers in early Contents text extraction; visually inspect TOC.")

    qa_dir = out_dir / "qa"
    if render_pngs:
        paths = render_selected_pages(pdf_path, qa_dir, max_pages=16)
        verdict.qa_renders = [str(p) for p in paths]
        # Dark-page check on rendered samples.
        for p in paths:
            try:
                from PIL import Image
                im = Image.open(p).convert("L")
                sample = im.resize((64, 90))
                data = sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata()
                vals = list(data)
                avg = sum(vals) / len(vals)
                if avg < 45:
                    m = re.search(r"_(\d+)\.png$", p.name)
                    verdict.dark_pages.append(int(m.group(1)) if m else -1)
            except Exception:
                pass

    removed_documents = list(dict.fromkeys(log.removed_documents))
    removed_blocks = list(dict.fromkeys(log.removed_blocks))
    kept_images = list(dict.fromkeys(log.kept_images))
    removed_images = list(dict.fromkeys(log.removed_images))
    ai_decisions = list(dict.fromkeys(log.ai_decisions))
    warnings_seen = list(dict.fromkeys(log.warnings))
    hard_failures = list(dict.fromkeys(log.hard_failures))

    qa_json = out_dir / "qa_verdict.json"
    qa_json.write_text(json.dumps(dataclasses.asdict(verdict), ensure_ascii=False, indent=2), encoding="utf-8")

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
    if verdict.non_a4_pages:
        report_lines.append("\nNon-A4 pages:\n" + json.dumps(verdict.non_a4_pages[:40], indent=2))
    if verdict.possible_header_collisions:
        report_lines.append("\nPossible header/rule collisions:\n" + json.dumps(verdict.possible_header_collisions[:80], indent=2))
    if verdict.possible_line_spills:
        report_lines.append("\nPossible broken word/single-letter line spills:\n" + json.dumps(verdict.possible_line_spills[:80], indent=2, ensure_ascii=False))
    if verdict.possible_narrow_columns:
        report_lines.append("\nPossible narrow columns:\n" + json.dumps(verdict.possible_narrow_columns[:80], indent=2))
    if verdict.possible_blank_page_artifacts:
        report_lines.append("\nPossible blank-page artifacts:\n" + json.dumps(verdict.possible_blank_page_artifacts[:80], indent=2))
    if verdict.toc_page_number_warnings:
        report_lines.append("\nTOC page-number warnings:\n" + "\n".join("- " + x for x in verdict.toc_page_number_warnings))
    if verdict.toc_duplicate_warnings:
        report_lines.append("\nTOC duplicate warnings:\n" + "\n".join("- " + x for x in verdict.toc_duplicate_warnings))
    if verdict.empty_content_pages:
        report_lines.append("\nEmpty/title-only content pages:\n" + json.dumps(verdict.empty_content_pages[:80], indent=2, ensure_ascii=False))
    if verdict.first_body_folio_warnings:
        report_lines.append("\nBody folio warnings:\n" + "\n".join("- " + x for x in verdict.first_body_folio_warnings))
    if verdict.openai_visual_flags:
        report_lines.append("\nOpenAI visual QA flags:\n" + "\n".join("- " + x for x in verdict.openai_visual_flags))
    if verdict.text_qa_flags:
        report_lines.append("\nAI text QA flags:\n" + "\n".join("- " + x for x in verdict.text_qa_flags))
    if verdict.ai_rule_suggestion_file:
        report_lines.append(f"\nAI regex rule suggestions: {verdict.ai_rule_suggestion_file}")
    if removed_documents:
        report_lines.append("\nRemoved documents:\n" + "\n".join("- " + x for x in removed_documents[:100]))
    if removed_blocks:
        report_lines.append("\nRemoved block samples:\n" + "\n".join("- " + x for x in removed_blocks[:100]))
    if kept_images:
        report_lines.append("\nKept image samples:\n" + "\n".join("- " + x for x in kept_images[:100]))
    if removed_images:
        report_lines.append("\nRemoved image samples:\n" + "\n".join("- " + x for x in removed_images[:100]))
    if ai_decisions:
        report_lines.append("\nAI decisions:\n" + "\n".join("- " + x for x in ai_decisions[:160]))
    if warnings_seen:
        report_lines.append("\nWarnings:\n" + "\n".join("- " + x for x in warnings_seen[:160]))
    if hard_failures:
        report_lines.append("\nHard failures:\n" + "\n".join("- " + x for x in hard_failures))

    qa_txt = out_dir / "qa_report.txt"
    qa_txt.write_text("\n".join(report_lines), encoding="utf-8")
    doc.close()
    return verdict, qa_json, qa_txt

# --------------------------------------------------------------------------------------
# Build orchestrator
# --------------------------------------------------------------------------------------


def build_once(epub_path: Path, out_pdf: Path, artifact_dir: Path, settings: Settings, args: argparse.Namespace, log: BuildLog) -> tuple[QAVerdict, Path, Path, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    build_dir = artifact_dir / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    try:
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
        docs = scan_spine_items(items)

        ai_provider = args.ai_provider
        ai_model = args.deepseek_model if ai_provider == "deepseek" else args.openai_model
        needs_text_qa = ai_provider == "deepseek" and not args.no_text_qa
        needs_primary_ai = ai_provider != "none" and (args.use_openai or args.openai_image_check or needs_text_qa)
        ai_client = require_ai_client(ai_provider) if needs_primary_ai else None
        visual_client = require_openai_client() if args.openai_qa else None
        if args.use_openai and ai_client is not None:
            apply_openai_book_plan(ai_client, ai_model, docs, settings.title, log, provider=ai_provider)

        src_map = copy_assets(book, build_dir, log)
        font_face_css = prepare_embedded_fonts(build_dir, settings, log)
        toc: list[TocEntry] = []
        used_ids: set[str] = set()
        fragments: list[str] = []
        current_work: Optional[str] = None
        current_division: Optional[str] = None
        sample_budget = sample_word_budget(args.sample_pages)
        sample_words = 0

        for doc in docs:
            if sample_budget and sample_words >= sample_budget:
                log.warn(f"Sample mode stopped body collection after about {sample_words} words before PDF layout.")
                break
            if doc.remove:
                log.removed_documents.append(f"{doc.index} {doc.href} kind={doc.kind} {doc.notes}")
                continue
            frag, current_work, current_division = clean_document(
                doc, src_map, settings, toc, used_ids, current_work, current_division, log,
                ai_client=(ai_client if args.openai_image_check else None), ai_model=ai_model, ai_provider=ai_provider,
            )
            if fragment_is_title_only(frag, settings):
                log.removed_documents.append(f"{doc.index} {doc.href} skipped as duplicate/empty title-only fragment")
                continue
            if clean_text(BeautifulSoup(frag, "lxml").get_text(" ")) or "<img" in frag or "<table" in frag:
                fragments.append(frag)
                sample_words += visible_word_count(BeautifulSoup(frag, "lxml").get_text(" "))

        if not fragments:
            raise SystemExit("No usable body content remained after cleanup. Retry with --keep-all-images or without --use-openai.")

        html_doc, css = compose_html(settings, fragments, toc, font_face_css=font_face_css)
        write_build(build_dir, html_doc, css)
        render_pdf(build_dir, out_pdf)
        toc_page_numbers = resolve_toc_page_numbers(out_pdf, toc, log)
        if toc_page_numbers:
            html_doc, css = compose_html(settings, fragments, toc, toc_page_numbers=toc_page_numbers, font_face_css=font_face_css)
            write_build(build_dir, html_doc, css)
            render_pdf(build_dir, out_pdf)
        elif toc:
            log.warn("Could not resolve explicit TOC page numbers; leaving renderer-generated TOC counters in place.")
        if args.sample_pages and args.sample_pages > 0:
            subset_pdf(out_pdf, args.sample_pages)
        draw_vector_runner_rules(out_pdf, settings)
        if not args.no_optimize:
            optimize_pdf(out_pdf)
        verdict, qa_json, qa_txt = preflight_pdf(out_pdf, artifact_dir, log, settings=settings, render_pngs=not args.no_qa_render)
        if needs_text_qa and ai_client is not None:
            try:
                text_report, suggestions_path = ai_text_qa(ai_client, ai_model, ai_provider, out_pdf, qa_json, qa_txt, artifact_dir, settings, log)
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
        if args.openai_qa and visual_client is not None:
            visual_report = openai_visual_qa(visual_client, args.openai_model, out_pdf, qa_json, artifact_dir / "qa", args.openai_qa_pages)
            try:
                visual_text = visual_report.read_text(encoding="utf-8", errors="ignore")
                issue_lines = openai_visual_issue_lines(visual_text)
                verdict.openai_visual_issue_lines = issue_lines[:80]
                issue_text = "\n".join(issue_lines)
                if re.search(r"FINAL\s*:\s*FAIL\b|\bFAIL\b", visual_text, re.I):
                    add_visual_flag(verdict, "OpenAI visual QA returned FAIL; inspect openai_visual_qa.txt.")
                if re.search(r"header|runner|running head|rule|collision|crowd", issue_text, re.I):
                    verdict.possible_header_collisions.append({"page": -1, "issue": "OpenAI visual QA flagged possible running-head/header-rule issue"})
                    add_visual_flag(verdict, "OpenAI visual QA flagged possible running-head/header-rule issue.")
                if re.search(r"justif|ragged|word spacing|river|paragraph|indent|line spill|single-letter|broken word|narrow|overwide", issue_text, re.I):
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
        return verdict, qa_json, qa_txt, build_dir
    except Exception:
        if not args.debug_html and build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        raise


def bump_float_setting(settings: Settings, attr: str, delta: float, maximum: float, log: BuildLog, reason: str) -> bool:
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
        changed |= bump_float_setting(
            settings,
            "line_height",
            0.025,
            1.32,
            log,
            "Opened body line-height after AI body-typography warning",
        )
    if has_visual_feedback(verdict, r"chapter/title|chapter title|work title|major work|chapter|heading|opener|stranded|too close"):
        changed |= bump_float_setting(
            settings,
            "subdivision_margin_bottom_mm",
            1.2,
            8.0,
            log,
            "Increased chapter-title bottom spacing after AI title-placement warning",
        )
        changed |= bump_float_setting(
            settings,
            "major_opener_bottom_margin_mm",
            2.0,
            22.0,
            log,
            "Increased major-opener bottom spacing after AI title-placement warning",
        )
    if has_visual_feedback(verdict, r"\bTOC\b|contents|leader|duplicate|page number|page-number"):
        changed |= bump_float_setting(
            settings,
            "toc_line_height",
            0.02,
            1.2,
            log,
            "Opened TOC line-height after AI TOC/page-number warning",
        )
        changed |= bump_float_setting(
            settings,
            "toc_entry_gap_mm",
            0.4,
            4.8,
            log,
            "Opened TOC entry spacing after AI TOC/page-number warning",
        )
    if has_visual_feedback(verdict, r"folio|page number|roman|arabic|numbering"):
        log.warn("AI QA flagged folio/page-numbering. This is reported for review; no safe generic auto-fix was applied.")
    if has_visual_feedback(verdict, r"image|caption|portrait|plate|illustration|cropped|oversized"):
        log.warn("AI QA flagged image/caption cleanup. This is reported for review; no safe generic auto-fix was applied.")
    if has_visual_feedback(verdict, r"poetry|verse|drama|cast|stage direction|blockquote|letter"):
        log.warn("AI QA flagged poetry/drama/special-form layout. This is reported for review; no safe generic auto-fix was applied.")
    if has_visual_feedback(verdict, r"blank|empty|title-only|dark|black|artifact|raw ebook|blue|underlined|hyperlink|browser"):
        log.warn("AI QA flagged blank/artifact/raw-ebook styling. This is reported for review; no safe generic auto-fix was applied.")
    if verdict.ai_rule_suggestion_file:
        log.warn(f"AI suggested regex cleanup rules for review: {verdict.ai_rule_suggestion_file}. Reviewed rules can improve the next run after being added to rule_packs.")
    if verdict.possible_narrow_columns:
        # Usually caused by EPUB CSS; our stripping is already aggressive. Add a note rather than guessing.
        log.warn("Narrow columns detected after normalization; inspect generated HTML around those pages.")
    return changed


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def coerce_setting_value(field: dataclasses.Field, value: Any) -> Any:
    """Lightly coerce YAML/JSON values into the dataclass field type."""
    default = field.default
    try:
        if isinstance(default, bool):
            return coerce_bool(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, str):
            return str(value)
    except Exception:
        return value
    return value


def load_config(path: Optional[str], settings: Settings) -> Settings:
    """Load YAML or JSON config. Missing keys keep the built-in defaults."""
    if not path:
        return settings
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.exists():
        raise SystemExit(f"Config file not found: {cfg_path}")
    if cfg_path.suffix.lower() == ".json":
        data = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        try:
            import yaml
        except Exception as exc:
            raise SystemExit("YAML config requires PyYAML: pip install pyyaml\n" + str(exc))
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit("Config file must contain a top-level mapping/object of setting names to values.")
    known = {f.name: f for f in dataclasses.fields(Settings)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        print("WARNING: ignoring unknown config keys: " + ", ".join(unknown), file=sys.stderr)
    for name, field_obj in known.items():
        if name in data:
            setattr(settings, name, coerce_setting_value(field_obj, data[name]))
    return settings


def apply_cli_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Apply only explicitly supplied CLI style overrides after config loading."""
    if args.title is not None:
        settings.title = args.title
    cli_to_setting = {
        "body_size": "body_size_pt",
        "line_height": "line_height",
        "font_stack": "font_stack",
        "font_dir": "font_dir",
        "embedded_font_family": "embedded_font_family",
        "embedded_font_regular": "embedded_font_regular",
        "embedded_font_italic": "embedded_font_italic",
        "embedded_font_weight": "embedded_font_weight",
        "margin_top": "margin_top_mm",
        "margin_side": "margin_side_mm",
        "margin_bottom": "margin_bottom_mm",
        "runner_font": "runner_font_pt",
        "folio_font": "folio_font_pt",
        "runner_rule_gap": "runner_rule_gap_mm",
        "runner_body_clearance": "runner_body_clearance_mm",
        "runner_rule_y": "runner_rule_y_mm",
        "runner_title_top": "runner_title_top_mm",
        "runner_layout": "runner_layout",
        "runner_rule_style": "runner_rule_style",
        "runner_collection_transform": "runner_collection_transform",
        "runner_work_transform": "runner_work_transform",
        "verse_line_height": "verse_line_height",
        "verse_max_width": "verse_max_width_mm",
        "paragraph_indent": "paragraph_indent_em",
        "subdivision_margin_top": "subdivision_margin_top_mm",
        "subdivision_margin_bottom": "subdivision_margin_bottom_mm",
    }
    for cli_name, setting_name in cli_to_setting.items():
        value = getattr(args, cli_name, None)
        if value is not None:
            setattr(settings, setting_name, value)
    if args.strict:
        settings.strict = True
    if args.no_sample_requirement:
        settings.no_sample_requirement = True
    if args.no_smart_punctuation:
        settings.smart_punctuation = False
    if args.no_embed_font_files:
        settings.embed_font_files = False
    if args.keep_all_images:
        settings.image_policy = "keep-all"
    elif args.remove_all_images:
        settings.image_policy = "remove-all"
    return settings


def write_default_config(path: str) -> None:
    target = Path(path).expanduser().resolve()
    data = dataclasses.asdict(Settings())
    lines = [
        "# Deluxe EPUB-to-print-PDF pipeline config",
        "# Missing keys keep the built-in defaults. You can delete anything you do not want to override.",
        "# Override order: built-in defaults -> this config file -> explicit CLI flags.",
        "",
    ]
    for key, value in data.items():
        if isinstance(value, str):
            encoded = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            encoded = "true" if value else "false"
        else:
            encoded = str(value)
        lines.append(f"{key}: {encoded}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote default config: {target}")


def resolve_output_pdf(args: argparse.Namespace) -> Path:
    out_arg = Path(args.out).expanduser()
    if out_arg.is_absolute() or out_arg.parent != Path("."):
        return out_arg.resolve()
    return (Path(args.output_dir).expanduser().resolve() / out_arg.name)


def resolve_artifact_dir(args: argparse.Namespace, out_pdf: Path) -> Path:
    artifact_root = Path(args.artifacts_dir).expanduser().resolve()
    return artifact_root / slugify(out_pdf.stem, "run")


def build_pipeline(args: argparse.Namespace) -> None:
    if getattr(args, "write_default_config", None):
        write_default_config(args.write_default_config)
        return

    epub_path = Path(args.epub).expanduser().resolve()
    if not epub_path.exists():
        raise SystemExit(f"EPUB not found: {epub_path}")
    out_pdf = resolve_output_pdf(args)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir = resolve_artifact_dir(args, out_pdf)

    # Precedence: built-in defaults -> config file -> explicit CLI overrides.
    settings = Settings()
    settings = load_config(args.config, settings)
    settings = apply_cli_overrides(settings, args)

    if not settings.no_sample_requirement and not args.sample_pages and not args.full_without_sample:
        print("WARNING: Your production rule says sample-first. Use --sample-pages 50 for review, or --full-without-sample to intentionally render the full book.", file=sys.stderr)

    log = BuildLog()
    apply_rule_packs(settings, log)
    final_verdict: Optional[QAVerdict] = None
    qa_json = qa_txt = build_dir = None
    for pass_no in range(args.max_auto_fix_passes + 1):
        if pass_no > 0:
            print(f"Auto-fix pass {pass_no}: regenerating PDF with adjusted CSS settings...")
        final_verdict, qa_json, qa_txt, build_dir = build_once(epub_path, out_pdf, artifact_dir, settings, args, log)
        if pass_no >= args.max_auto_fix_passes:
            break
        fix_count_before = len(log.css_auto_fixes)
        if not auto_fix_settings(settings, final_verdict, log):
            break
        for fix_note in log.css_auto_fixes[fix_count_before:]:
            print(f"Auto-fix queued: {fix_note}")

    assert final_verdict is not None and qa_json is not None and qa_txt is not None and build_dir is not None

    # Rewrite reports after auto-fix notes are known.
    build_summary = artifact_dir / "build_summary.json"
    build_summary.write_text(json.dumps({
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
    }, ensure_ascii=False, indent=2), encoding="utf-8")

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
        raise SystemExit(f"Strict mode failed: delivery-blocking QA warnings remain. Inspect {qa_txt} and {artifact_dir / 'qa'} renders.")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Convert an EPUB into a deluxe A4 print-oriented PDF with structural cleanup, configurable style settings, OpenAI-assisted options, and hard QA gates.",
        epilog=textwrap.dedent(
            """
            Examples:
              python deluxe_epub_to_pdf.py --write-default-config my_style.yaml
              python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --out sample.pdf --sample-pages 50 --use-openai --openai-qa --debug-html
              python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --out full.pdf --full-without-sample --use-openai --strict
              python deluxe_epub_to_pdf.py book.epub --body-size 12 --line-height 1.28 --font-family "Garamond, Georgia, serif" --sample-pages 50

            By default, PDFs are written to output/ and run artifacts to artifacts/<pdf-name>/.
            """
        ),
    )
    p.add_argument("epub", nargs="?", help="Input EPUB file")
    p.add_argument("--out", default="print_ready.pdf", help="Output PDF filename/path. Bare filenames are written under --output-dir.")
    p.add_argument("--output-dir", default="output", help="Folder for PDFs when --out is a bare filename")
    p.add_argument("--artifacts-dir", default="artifacts", help="Folder for QA reports, QA renders, and debug builds")
    p.add_argument("--title", default=None, help="Optional clean-title override. Omit to use EPUB metadata automatically.")
    p.add_argument("--config", default=None, help="Optional YAML or JSON config overriding style/settings")
    p.add_argument("--write-default-config", default=None, help="Write a complete editable default YAML config and exit")
    p.add_argument("--sample-pages", type=int, default=0, help="Create a first-N-pages sample PDF for review")
    p.add_argument("--full-without-sample", action="store_true", help="Bypass sample-first warning and build full PDF intentionally")
    p.add_argument("--no-sample-requirement", action="store_true", help="Disable sample-first warning in settings")

    # Style overrides. Defaults are None so they override config only when explicitly supplied.
    p.add_argument("--body-size", type=float, default=None, help="Body font size in pt; config key: body_size_pt")
    p.add_argument("--line-height", type=float, default=None, help="Body line-height multiplier; config key: line_height")
    p.add_argument("--font-stack", "--font-family", dest="font_stack", default=None, help="CSS font-family stack; config key: font_stack")
    p.add_argument("--font-dir", default=None, help="Folder containing configured local font files; config key: font_dir")
    p.add_argument("--embedded-font-family", default=None, help="CSS @font-face family name for local font files")
    p.add_argument("--embedded-font-regular", default=None, help="Regular/upright local font filename inside --font-dir")
    p.add_argument("--embedded-font-italic", default=None, help="Italic local font filename inside --font-dir")
    p.add_argument("--embedded-font-weight", default=None, help="CSS font-weight or range for embedded font files, e.g. 400 or '400 800'")
    p.add_argument("--no-embed-font-files", action="store_true", help="Disable local @font-face embedding and rely on installed system fonts")
    p.add_argument("--margin-top", type=float, default=None, help="Top margin in mm; config key: margin_top_mm")
    p.add_argument("--margin-side", type=float, default=None, help="Equal left/right margin in mm; config key: margin_side_mm")
    p.add_argument("--margin-bottom", type=float, default=None, help="Bottom margin in mm; config key: margin_bottom_mm")
    p.add_argument("--runner-font", type=float, default=None, help="Running-head font size in pt; config key: runner_font_pt")
    p.add_argument("--folio-font", type=float, default=None, help="Page-number/folio font size in pt; config key: folio_font_pt")
    p.add_argument("--runner-rule-gap", type=float, default=None, help="Gap between running-head text and rule in mm; config key: runner_rule_gap_mm")
    p.add_argument("--runner-body-clearance", type=float, default=None, help="Extra gap between runner rule area and body text in mm; config key: runner_body_clearance_mm")
    p.add_argument("--runner-rule-y", type=float, default=None, help="Full-width vector runner rule position from top trim in mm; config key: runner_rule_y_mm")
    p.add_argument("--runner-title-top", type=float, default=None, help="Running-head title offset from top trim in mm; config key: runner_title_top_mm")
    p.add_argument("--runner-layout", default=None, choices=["right_title_full_rule", "centered_single_rule", "dual_full_rule", "alternating"], help="Running-head layout; right_title_full_rule = reference-style right title plus full-width rule")
    p.add_argument("--runner-rule-style", default=None, choices=["full_width", "single", "split", "none"], help="Runner rule rendering style; config key: runner_rule_style")
    p.add_argument("--runner-collection-transform", default=None, help="CSS text-transform for collection title runner; e.g. none, uppercase")
    p.add_argument("--runner-work-transform", default=None, help="CSS text-transform for current-work runner; e.g. uppercase, none")
    p.add_argument("--paragraph-indent", type=float, default=None, help="Paragraph first-line indent in em; config key: paragraph_indent_em")
    p.add_argument("--subdivision-margin-top", type=float, default=None, help="Chapter/subdivision heading top margin in mm; config key: subdivision_margin_top_mm")
    p.add_argument("--subdivision-margin-bottom", type=float, default=None, help="Chapter/subdivision heading bottom margin in mm; config key: subdivision_margin_bottom_mm")
    p.add_argument("--verse-line-height", type=float, default=None, help="Verse line-height multiplier; config key: verse_line_height")
    p.add_argument("--verse-max-width", type=float, default=None, help="Verse block max width in mm; config key: verse_max_width_mm")

    p.add_argument("--keep-all-images", action="store_true", help="Keep all EPUB images; overrides image_policy")
    p.add_argument("--remove-all-images", action="store_true", help="Remove all images; overrides image_policy")
    p.add_argument("--no-smart-punctuation", action="store_true", help="Disable conservative punctuation cleanup")
    p.add_argument("--no-optimize", action="store_true", help="Skip pikepdf optimization")
    p.add_argument("--no-qa-render", action="store_true", help="Do not render PNG QA pages")
    p.add_argument("--debug-html", action="store_true", help="Keep generated HTML/CSS build folder")
    p.add_argument("--strict", action="store_true", help="Exit non-zero when QA blockers are detected")
    p.add_argument("--max-auto-fix-passes", type=int, default=1, help="Maximum rerenders after local/OpenAI QA-driven safe CSS/config fixes")
    p.add_argument("--ai-provider", choices=["openai", "deepseek", "none"], default="openai", help="Provider for text/structure AI tasks. DeepSeek runs text QA after local QA; OpenAI can also do visual QA.")
    p.add_argument("--use-openai", action="store_true", help="Use the selected --ai-provider for whole-book structure planning")
    p.add_argument("--openai-model", default="gpt-5.4-mini", help="OpenAI model for structure/image/visual QA")
    p.add_argument("--deepseek-model", default="deepseek-chat", help="DeepSeek model for structure/text QA")
    p.add_argument("--openai-image-check", action="store_true", help="Use the selected --ai-provider to classify each image/caption block; can cost more")
    p.add_argument("--openai-qa", action="store_true", help="Send rendered QA pages to OpenAI vision for visual review")
    p.add_argument("--openai-qa-pages", type=int, default=10, help="Maximum rendered pages sent to OpenAI visual QA")
    p.add_argument("--no-text-qa", action="store_true", help="Disable provider text QA, e.g. DeepSeek post-local-QA review")
    args = p.parse_args(argv)
    if args.keep_all_images and args.remove_all_images:
        p.error("Choose only one of --keep-all-images or --remove-all-images")
    if not args.epub and not args.write_default_config:
        p.error("the following argument is required: epub, unless using --write-default-config")
    return args


if __name__ == "__main__":
    build_pipeline(parse_args())
