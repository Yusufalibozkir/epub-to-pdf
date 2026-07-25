"""
Compiled regex patterns and dimensional constants for the EPUB-to-PDF pipeline.

All regex patterns are stored both as module-level variables (for convenience)
and in _PATTERN_DICT (for dynamic runtime updates via apply_rule_packs()).
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------------------
# Dimensional constants
# --------------------------------------------------------------------------------------

A4_WIDTH_PT: float = 595.276
A4_HEIGHT_PT: float = 841.890
PT_PER_MM: float = 72.0 / 25.4

# --------------------------------------------------------------------------------------
# Internal pattern registry
# --------------------------------------------------------------------------------------

_PATTERN_DICT: dict[str, re.Pattern] = {}


def _register(name: str, pattern: str, flags: int = re.I) -> re.Pattern:
    """Compile a regex and register it in both the module variable and _PATTERN_DICT."""
    p = re.compile(pattern, flags)
    globals()[name] = p
    _PATTERN_DICT[name] = p
    return p


def update_pattern(name: str, pattern: re.Pattern) -> None:
    """Update a registered pattern at runtime (used by apply_rule_packs())."""
    globals()[name] = pattern
    _PATTERN_DICT[name] = pattern


def get_pattern(name: str) -> re.Pattern:
    """Get the current version of a registered pattern."""
    return _PATTERN_DICT.get(name)


# --------------------------------------------------------------------------------------
# Pattern definitions (registered in _PATTERN_DICT automatically)
# --------------------------------------------------------------------------------------

PROMO_PATTERNS: re.Pattern = _register("PROMO_PATTERNS",
    r"(delphi classics|also available|other books by|more books by|subscribe|newsletter|"
    r"visit our website|follow us|kindle|ebook|smashwords|gutenberg license|project gutenberg license|"
    r"catalogue|catalog|advertisement|promotion|publisher's note to the reader|download our|"
    r"copyrighted images? removed|copyright|all rights reserved|beautifully illustrated|"
    r"comprehensive editions|bonus texts|explore (science and philosophy|our wide range)|"
    r"the delphi classics catalogue|www\.|https?://|isbn|app store|google play|goodreads)",
)

BACKMATTER_PATTERNS: re.Pattern = _register("BACKMATTER_PATTERNS",
    r"^(notes|endnotes|appendix|appendices|bibliography|glossary|index|letters|notebooks|"
    r"biography|chronology|translator'?s notes?|editor'?s notes?|commentary|source notes?)$",
)

FRONTMATTER_PATTERNS: re.Pattern = _register("FRONTMATTER_PATTERNS",
    r"^(preface|foreword|introduction|prologue|author'?s note|note by the author)$",
)

LOCAL_TOC_HEADINGS: re.Pattern = _register("LOCAL_TOC_HEADINGS",
    r"^(contents|table of contents|chapter list|list of chapters|illustrations|list of illustrations)$",
)

COLLECTION_DIVISIONS: re.Pattern = _register("COLLECTION_DIVISIONS",
    r"^(the )?(novels|short stories|stories|plays|poetry|poems|memoirs|letters|notebooks|"
    r"essays|biograph(y|ies)|appendices|tales|sketches|dramas|translations|miscellanies|"
    r"non[- ]fiction|verse|narrative poems|lyric poems|spurious works|epistles|greek texts)$",
)

MAJOR_WORK_HINTS: re.Pattern = _register("MAJOR_WORK_HINTS",
    r"^(book|part|volume)\s+[ivxlcdm0-9]+$|^(novel|play|poem|drama|story|tale)s?\b",
)

CHAPTER_HEADINGS: re.Pattern = _register("CHAPTER_HEADINGS",
    r"^(chapter|chap\.?|part|scene|act|section|proposition|article|letter|canto|book)\b|^[ivxlcdm]+$|^\d+$",
)

CAST_HEADINGS: re.Pattern = _register("CAST_HEADINGS",
    r"^(dramatis personae|characters|persons|the persons of the play|names of the characters|"
    r"the characters|cast of characters|personages)$",
)

ACT_SCENE_HEADINGS: re.Pattern = _register("ACT_SCENE_HEADINGS",
    r"^(act|scene)\b|^act\s+[ivxlcdm0-9]+$",
)

PLATE_CAPTION_PATTERNS: re.Pattern = _register("PLATE_CAPTION_PATTERNS",
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
)

FUNCTIONAL_IMAGE_CLUES: re.Pattern = _register("FUNCTIONAL_IMAGE_CLUES",
    r"(map|diagram|chart|table|figure|rune|inscription|alphabet|script|seal|facsimile|"
    r"manuscript|author-?drawn|drawn by the author|drawing|plan|musical notation|score|"
    r"see (the )?(figure|map|diagram|chart|drawing|plan)|"
    r"dotted lines|following figure|shown below|shown above|as follows|the accompanying|"
    r"illustrated below|engraving below|symbol|glyph|sign)",
)

FUNCTIONAL_IMAGE_SRC_CLUES: re.Pattern = _register("FUNCTIONAL_IMAGE_SRC_CLUES",
    r"(map|diagram|chart|figure|rune|inscription|alphabet|script|seal|facsimile|"
    r"manuscript|drawing|plan|score|genealogy)",
)

PUBLISHER_IMAGE_SRC_CLUES: re.Pattern = _register("PUBLISHER_IMAGE_SRC_CLUES",
    r"(cover|frontispiece|portrait|photo|photograph|author|birthplace|house|home|grave|"
    r"tomb|museum|plate|illustration|delphi|title|logo|catalog|catalogue|promo|"
    r"decorative|ornament|headpiece|tailpiece|vignette|border|divider|"
    r"^(?:image|fig|pic|img)[_\-]?\d)",
)

LOCAL_CONTENTS_LINE_RE: re.Pattern = _register("LOCAL_CONTENTS_LINE_RE",
    r"^(contents|chapter|letter|act|scene|book|part|volume|section|[ivxlcdm]+|\d+|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
    r"(st|nd|rd|th)?\.?)\b",
)

POETRY_CLASS_RE: re.Pattern = _register("POETRY_CLASS_RE",
    r"poem|poetry|stanza|verse|line|canto|song|epigram|sonnet|ode",
)

ROMAN_RE: re.Pattern = _register("ROMAN_RE", r"^[ivxlcdm]+$", re.I)

# --------------------------------------------------------------------------------------
# Rule-pack key mapping
# --------------------------------------------------------------------------------------

RULE_PACK_KEYS: dict[str, str] = {
    "promo_patterns": "PROMO_PATTERNS",
    "backmatter_patterns": "BACKMATTER_PATTERNS",
    "frontmatter_patterns": "FRONTMATTER_PATTERNS",
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

SMART_QUOTES_MAP: dict[str, str] = {
    "--": "—",
    "...": "…",
}
