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


SOURCE_TOP_LEVEL_SKIP_RE = re.compile(
    r"^(?:title\s+page|copyright|the\s+books|contents|table\s+of\s+contents|"
    r"series\s+contents|alphabetical\s+list\s+of\s+titles|"
    r"list\s+of\s+essays\s+(?:in\s+)?alphabetical\s+order|"
    r"(?:the\s+)?(?:delphi\s+classics\s+)?catalogue)$",
    re.I,
)

SOURCE_AUTHORED_SUBWORK_RE = re.compile(
    r"^.+?\s+by\s+[A-Z][A-Za-z .,'\u2019\u2018\u2014\u2013\u2010-]{2,80}$",
    re.I,
)


def source_toc_top_level_titles(book: Any) -> list[str]:
    """Return print-usable top-level titles from the EPUB navigation tree."""
    titles: list[str] = []
    active_collection_division = False
    for entry in getattr(book, "toc", []) or []:
        link = entry[0] if isinstance(entry, tuple) and entry else entry
        title = clean_display_title(str(getattr(link, "title", "") or ""))
        if not title or not _source_toc_title_is_print_top_level(title):
            continue
        if active_collection_division and SOURCE_AUTHORED_SUBWORK_RE.fullmatch(title):
            continue
        titles.append(title)
        active_collection_division = C.COLLECTION_DIVISIONS.fullmatch(title.strip()) is not None
    return list(dict.fromkeys(titles))


def _source_toc_title_is_print_top_level(title: str) -> bool:
    text = clean_display_title(title)
    if not text:
        return False
    if SOURCE_TOP_LEVEL_SKIP_RE.fullmatch(text.strip()):
        return False
    if C.PROMO_PATTERNS.search(text) or C.BACKMATTER_PATTERNS.fullmatch(text.strip()):
        return False
    return True


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


def normalize_author_name(name: str) -> str:
    """Turn common library-style EPUB creator metadata into a clean display name."""
    text = clean_text(name)
    if not text:
        return ""
    display_titles = {
        "graf",
        "graaf",
        "count",
        "countess",
        "sir",
        "dame",
        "lord",
        "lady",
        "baron",
        "baroness",
    }
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        last = parts[0]
        given = parts[1]
        rest = [p for p in parts[2:] if p.lower().strip(".") not in display_titles]
        text = " ".join([given, *rest, last]).strip()
    words = text.split()
    while len(words) > 1 and words[0].lower().strip(".") in display_titles:
        words.pop(0)
    text = " ".join(words)
    return clean_text(text)


def infer_author_with_source(book, fallback: str = "") -> tuple[str, str]:
    creators = book.get_metadata("DC", "creator")
    for creator_entry in creators or []:
        if not creator_entry or not creator_entry[0]:
            continue
        author = normalize_author_name(html.unescape(str(creator_entry[0])))
        if author:
            return author, "EPUB metadata"
    fallback_author = normalize_author_name(fallback)
    if fallback_author:
        return fallback_author, "fallback"
    return "", ""


def infer_author(book, fallback: str = "") -> str:
    return infer_author_with_source(book, fallback)[0]


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
    _demote_late_frontmatter_like_sections(docs)
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

    if _looks_like_publisher_apparatus(first_heading, sample, doc.text_length):
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.94
        doc.notes = "Removed publisher/vendor catalogue or boilerplate apparatus."
        return

    if _looks_like_gutenberg_header_wrapper(first_heading, sample, doc.text_length):
        doc.kind = "promo"
        doc.remove = True
        doc.confidence = 0.96
        doc.notes = "Removed Project Gutenberg header/start-marker wrapper document."
        return

    # Project Gutenberg license boilerplate — remove if short, or if ALL
    # headings are Gutenberg-related.  Long documents that merely *start*
    # with a Gutenberg header but also contain literary chapter headings
    # contain real book text and must be kept.
    _gutenberg_heading_re = re.compile(
        r"gutenberg|project gutenberg.*license|full.*license|end of the project gutenberg",
        re.I,
    )
    if re.search(
        r"full project gutenberg.*license|project gutenberg.*license",
        first_heading + " " + sample[:1200],
        re.I,
    ):
        if doc.text_length < 8000:
            doc.kind = "promo"
            doc.remove = True
            doc.confidence = 0.95
            doc.notes = "Removed Project Gutenberg legal boilerplate/license document."
            return
        # Long document — only remove if EVERY heading is Gutenberg boilerplate
        # (meaning the document has no literary content at all).
        if doc.headings and all(
            _gutenberg_heading_re.search(clean_text(re.sub(r"^H\d:\s*", "", h)))
            for h in doc.headings
        ):
            doc.kind = "promo"
            doc.remove = True
            doc.confidence = 0.92
            doc.notes = "Removed standalone Project Gutenberg legal/license document."
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
        doc.remove = False
    elif doc.contains_poetry:
        doc.kind = "poetry"
        doc.remove = False
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
    if re.fullmatch(r"series\s+contents|alphabetical\s+list\s+of\s+titles|list\s+of\s+essays\s+(in\s+)?alphabetical\s+order", heading, flags=re.I):
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
    if _looks_like_compound_delphi_frontmatter_blob(sample, text_length):
        return True
    return False


def _looks_like_compound_delphi_frontmatter_blob(sample: str, text_length: int) -> bool:
    """Detect Delphi title-page/contents blobs that arrive as one text-heavy spine doc.

    These sometimes bypass heading-based apparatus detection because the source file
    has no semantic heading tags and gets probe-classified as poetry from its many
    short uppercase lines.
    """
    if text_length > 6000:
        return False
    text = clean_text(sample)
    lower = text.lower()
    if "contents" not in lower or "version 1" not in lower:
        return False
    if "the biographies" not in lower and "the essays" not in lower:
        return False
    has_collection_title = bool(
        re.search(
            r"\b(?:the\s+)?(?:collected|complete)\s+works\s+of\b|\b[a-z][a-z .'-]+\s+(?:collected|complete)\s+works\b",
            text,
            re.I,
        )
    )
    has_delphi_style_listing = sum(
        phrase in lower
        for phrase in (
            "the books",
            "on the fourfold root of the principle of sufficient reason",
            "the world as will and idea",
            "schopenhauer by thomas whittaker",
            "schopenhauer by elbert hubbard",
            "arthur schopenhauer by william wallace",
        )
    )
    return has_collection_title and has_delphi_style_listing >= 3


def _looks_like_delphi_books_apparatus(doc: SpineDoc, first_heading: str, sample: str) -> bool:
    """Detect Delphi Classics' generic 'The Books' frontmatter gallery pages."""
    heading = clean_text(first_heading).strip(" .")
    if doc.index > 20:
        return False
    if doc.text_length > 1800:
        return False
    text = clean_text(sample).lower()
    has_books_heading = bool(re.fullmatch(r"the\s+books", heading, flags=re.I))
    has_books_gallery_signal = bool(
        re.search(
            r"\bthe\s+books\b|\bexplore science and philosophy with delphi classics\b|"
            r"\bbeautifully illustrated\b|\bcomprehensive editions\b|\bbonus texts\b",
            text,
            re.I,
        )
    )
    if not (has_books_heading or has_books_gallery_signal):
        return False
    return doc.contains_images or any(
        phrase in text
        for phrase in (
            "delphi classics",
            "born in",
            "modern maps",
            "market town",
            "village",
            "beautifully illustrated",
            "comprehensive editions",
            "bonus texts",
            "explore science and philosophy with delphi classics",
        )
    )


def _looks_like_publisher_apparatus(first_heading: str, sample: str, text_length: int) -> bool:
    """Detect standalone publisher/vendor material that should never enter the print body."""
    heading = clean_text(first_heading).strip(" .")
    heading_l = heading.lower()
    text = clean_text(sample).lower()
    combined = f"{heading_l} {text[:2500]}"

    if re.fullmatch(
        r"(the\s+)?(delphi\s+classics\s+)?catalogue|catalog|"
        r"(complete\s+)?catalogue\s+of\s+(english\s+)?titles|"
        r"project\s+gutenberg.*|full\s+project\s+gutenberg.*license",
        heading_l,
        flags=re.I,
    ):
        return True

    strong_hits = sum(
        bool(re.search(pattern, combined, re.I))
        for pattern in (
            r"\bdelphi classics\b",
            r"\bproject gutenberg\b",
            r"\bexplore science and philosophy with delphi classics\b",
            r"\bthe delphi classics catalogue\b",
            r"\bthe books\b",
            r"\bcomplete catalogue\b|\bcatalogue of english titles\b",
            r"\bwww\.delphiclassics\.com\b|\bdelphiclassics\.com\b",
            r"\bfacebook\.com/delphiebooks\b|\btwitter\.com/delphiclassics\b",
            r"\bsubscribe\b|\bnewsletter\b|\bbuying direct\b|\binstant updates\b",
            r"\binterested in .{1,80}\?",
            r"\bcomprehensive editions\b|\bbeautifully illustrated\b|\bbonus texts\b",
            r"\bereaders?\b|\bexplore our wide range\b",
            r"\bcopyright\b|\ball rights reserved\b",
            r"\bapp store\b|\bgoogle play\b|\bkindle\b|\bebook\b|\bisbn\b",
        )
    )
    exact_phrase_hit = bool(
        re.search(
            r"\bexplore science and philosophy with delphi classics\b|"
            r"\bthe delphi classics catalogue\b|"
            r"\bdelphi classics,\s*\d{4}\.?\s*all rights reserved\b",
            combined,
            re.I,
        )
    )
    return text_length < 20000 and (strong_hits >= 2 or exact_phrase_hit)


def _looks_like_gutenberg_header_wrapper(first_heading: str, sample: str, text_length: int) -> bool:
    """Detect Project Gutenberg's standalone opening wrapper page."""
    heading = clean_text(first_heading)
    text = clean_text(sample)
    combined = f"{heading} {text[:1200]}"
    if not re.search(r"\bthe\s+project\s+gutenberg\s+ebook\s+of\b", combined, flags=re.I):
        return False
    if not re.search(r"\*+\s*start\s+of\s+the\s+project\s+gutenberg\s+ebook\b", combined, flags=re.I):
        return False
    return text_length < 5000


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


def _demote_late_frontmatter_like_sections(docs: list[SpineDoc]) -> None:
    """Keep genuine opening front matter in place, but avoid hoisting late introductions."""
    seen_real_work = False
    for doc in docs:
        if doc.remove:
            continue
        if doc.kind in {"major_work", "play", "poetry", "backmatter"}:
            seen_real_work = True
            continue
        if doc.kind == "frontmatter" and seen_real_work:
            doc.kind = "chapter"
            doc.major_title = None
