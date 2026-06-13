"""
EPUB scanner, spine-item extraction, and document classification.

Provides the initial structural understanding of the EPUB: reading spine items,
extracting probe data (headings, text samples, poetry/drama/image flags), and
applying both heuristic and AI-assisted classification.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag
from ebooklib import ITEM_DOCUMENT, epub

from pipeline import _constants as C
from pipeline._models import BuildLog, SpineDoc
from pipeline._utils import (
    clean_display_title,
    clean_text,
    parse_html,
    remove_comments_scripts_styles,
)


# --------------------------------------------------------------------------------------
# EPUB reader
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


# --------------------------------------------------------------------------------------
# Title inference
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# Text probe
# --------------------------------------------------------------------------------------


def extract_probe(raw: bytes, limit: int = 9000) -> tuple[list[str], str, int, bool, bool, bool]:
    """Extract headings, text sample, and feature flags from a raw EPUB document."""
    soup = parse_html(raw)
    remove_comments_scripts_styles(soup)
    headings: list[str] = []
    for h in soup.find_all(re.compile(r"^h[1-6]$"))[:40]:
        t = clean_text(h.get_text(" "))
        if t:
            headings.append(f"{h.name.upper()}: {t}")
    text = clean_text(soup.get_text(" "))
    cls_text = " ".join(str(x.get("class", "")) for x in soup.find_all(True)[:500])
    contains_poetry = bool(C.POETRY_CLASS_RE.search(cls_text)) or _text_looks_like_poetry(soup)
    contains_drama = bool(
        any(C.CAST_HEADINGS.match(clean_text(h.get_text(" "))) for h in soup.find_all(re.compile(r"^h[1-6]$")))
    )
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
# Spine scanning
# --------------------------------------------------------------------------------------


def scan_spine_items(items: list[Any]) -> list[SpineDoc]:
    """Extract probe data for each spine document and run heuristic classification."""
    docs: list[SpineDoc] = []
    for i, item in enumerate(items):
        raw = _item_bytes(item)
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
    _mark_early_orphan_frontmatter_media(docs)
    return docs


def _item_bytes(item) -> bytes:
    data = item.get_content()
    if isinstance(data, str):
        return data.encode("utf-8", errors="ignore")
    return data


# --------------------------------------------------------------------------------------
# Heuristic classification
# --------------------------------------------------------------------------------------


def heuristic_classify_doc(doc: SpineDoc) -> None:
    """Assign a deterministic kind and remove flag using built-in regex patterns."""
    joined_headings = " | ".join(doc.headings)
    first_heading = ""
    if doc.headings:
        first_heading = clean_text(re.sub(r"^H\d:\s*", "", doc.headings[0]))
    sample = doc.text_sample
    sample_l = sample.lower()

    if _looks_like_source_contents_document(first_heading, sample, doc.text_length):
        doc.kind = "local_toc"
        doc.remove = True
        doc.confidence = 0.90
        doc.notes = "Removed source contents/argument-summary apparatus."
        return

    if _looks_like_delphi_books_apparatus(doc, first_heading, sample):
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.88
        doc.notes = "Removed Delphi-style frontmatter 'The Books' apparatus."
        return

    # Project Gutenberg license boilerplate
    if re.search(
        r"full project gutenberg.*license|project gutenberg.*license",
        first_heading + " " + sample[:1200],
        re.I,
    ):
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.95
        doc.notes = "Removed Project Gutenberg legal boilerplate/license document."
        return

    if doc.text_length < 60 and not doc.contains_images:
        doc.kind = "unknown"

    if C.PROMO_PATTERNS.search(sample) and doc.text_length < 3500:
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.78

    if C.LOCAL_TOC_HEADINGS.match(first_heading or "") and sample.count(" ") < 800:
        doc.kind = "local_toc"
        doc.remove = True
        doc.confidence = 0.80

    if C.FRONTMATTER_PATTERNS.match(first_heading or ""):
        doc.kind = "frontmatter"
        doc.major_title = first_heading
    elif C.BACKMATTER_PATTERNS.match(first_heading or ""):
        doc.kind = "backmatter"
        doc.major_title = first_heading
    elif C.COLLECTION_DIVISIONS.match(first_heading or ""):
        doc.kind = "division"
        doc.major_title = first_heading
        doc.current_division = first_heading
    elif C.CAST_HEADINGS.match(first_heading or "") or doc.contains_drama:
        doc.kind = "play"
    elif doc.contains_poetry:
        doc.kind = "poetry"
    elif first_heading and not C.CHAPTER_HEADINGS.match(first_heading) and not C.PROMO_PATTERNS.search(first_heading):
        if doc.headings and doc.headings[0].startswith("H1") and len(first_heading) < 100:
            doc.kind = "major_work"
            doc.major_title = first_heading

    if any(x in sample_l for x in ["preface", "introduction", "foreword", "editor"]):
        if doc.index < 5 and doc.kind == "unknown":
            doc.kind = "frontmatter"

    if doc.kind == "unknown":
        doc.kind = "chapter"


def _looks_like_source_contents_document(first_heading: str, sample: str, text_length: int) -> bool:
    heading = clean_text(first_heading).strip(" .")
    if re.fullmatch(r"(the\s+)?principal\s+contents", heading, flags=re.I):
        return True
    if re.fullmatch(r"series\s+contents|alphabetical\s+list\s+of\s+titles", heading, flags=re.I):
        return True
    if re.fullmatch(r"(list\s+of\s+)?illustrations", heading, flags=re.I):
        return text_length < 12000
    if re.fullmatch(r"contents|table\s+of\s+contents", heading, flags=re.I):
        return text_length < 12000
    first_words = clean_text(sample)[:500]
    if re.match(r"^(the\s+)?principal\s+contents\b", first_words, flags=re.I):
        return True
    if re.match(r"^(list\s+of\s+)?illustrations\b", first_words, flags=re.I):
        return True
    return False


def _looks_like_delphi_books_apparatus(doc: SpineDoc, first_heading: str, sample: str) -> bool:
    """Detect Delphi Classics' generic 'The Books' frontmatter gallery pages."""
    heading = clean_text(first_heading).strip(" .")
    if doc.index > 20 or not re.fullmatch(r"the\s+books", heading, flags=re.I):
        return False
    if doc.text_length > 1800:
        return False
    text = clean_text(sample).lower()
    return doc.contains_images or any(
        phrase in text
        for phrase in (
            "delphi classics",
            "born in",
            "modern maps",
            "market town",
            "village",
        )
    )


def _mark_early_orphan_frontmatter_media(docs: list[SpineDoc]) -> None:
    """Remove short image/caption fragments attached to removed early frontmatter."""
    seen_real_work = False
    after_removed_books_apparatus = False
    for doc in docs:
        if not doc.remove and doc.kind == "major_work":
            seen_real_work = True
            after_removed_books_apparatus = False
            continue

        if (
            not seen_real_work
            and after_removed_books_apparatus
            and doc.index <= 25
            and doc.contains_images
            and not doc.headings
            and doc.text_length < 350
        ):
            doc.kind = "promo"
            doc.remove = True
            doc.confidence = max(doc.confidence, 0.86)
            doc.notes = "Removed orphan frontmatter image/caption after 'The Books' apparatus."
            after_removed_books_apparatus = True
            continue

        after_removed_books_apparatus = bool(
            doc.remove and "The Books" in (doc.notes or "")
        )
