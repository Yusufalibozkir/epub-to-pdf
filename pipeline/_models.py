"""
Data models for the EPUB-to-PDF pipeline.

All state is organized into typed @dataclass classes with sensible defaults.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Optional


# --------------------------------------------------------------------------------------
# SECTION_SCHEMA and IMAGE_SCHEMA are JSON schemas used by the AI integration.
# They are defined here because the AI module imports them, and they don't fit
# naturally anywhere else.
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
                    "kind": {
                        "type": "string",
                        "enum": [
                            "frontmatter", "division", "major_work", "chapter",
                            "poetry", "play", "backmatter", "promo", "local_toc", "unknown",
                        ],
                    },
                    "remove_document": {"type": "boolean"},
                    "major_title": {"type": ["string", "null"]},
                    "current_division": {"type": ["string", "null"]},
                    "contains_poetry": {"type": "boolean"},
                    "contains_drama_or_cast": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "notes": {"type": "string"},
                },
                "required": [
                    "index", "kind", "remove_document", "major_title",
                    "current_division", "contains_poetry",
                    "contains_drama_or_cast", "confidence", "notes",
                ],
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


# --------------------------------------------------------------------------------------
# Book identity & typographic settings
# --------------------------------------------------------------------------------------


@dataclass
class Settings:
    """Complete typographic and pipeline configuration.

    Three-layer override precedence:
      1. Built-in defaults (here)
      2. YAML/JSON config file via --config
      3. Explicit CLI flags
    """

    # Book identity
    title: str = ""
    author: str = ""
    volume_mode: str = "auto"  # auto, single, collection

    # Page / trim
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

    # Typographic refinements
    drop_caps: bool = True
    small_caps: bool = True
    ligature_setting: str = "common"  # common, none, all, discretionary
    footnote_handling: str = "auto"  # "auto", "endnotes-only", "disabled"

    # Margins and live area
    margin_top_mm: float = 24.0
    margin_side_mm: float = 22.0
    margin_bottom_mm: float = 25.0
    front_margin_top_mm: float = 25.0
    front_margin_bottom_mm: float = 24.0

    # Running heads / folios
    runner_font_pt: float = 9.4
    runner_left_font_pt: Optional[float] = None
    runner_right_font_pt: Optional[float] = None
    runner_letter_spacing_em: float = 0.04
    runner_rule_gap_mm: float = 3.2
    runner_body_clearance_mm: float = 7.0
    runner_rule_y_mm: float = 17.0
    runner_title_top_mm: float = 8.5
    runner_rule_weight_pt: float = 0.45
    runner_rule_color: str = "#222"
    runner_layout: str = "right_title_full_rule"
    runner_rule_style: str = "full_width"
    runner_collection_transform: str = "uppercase"
    runner_work_transform: str = "none"
    folio_font_pt: float = 10.0
    front_folio_font_pt: float = 9.3

    # Paragraphs / blocks
    paragraph_indent_em: float = 1.25
    blockquote_side_margin_mm: float = 10.0
    blockquote_font_percent: float = 96.0

    # Major openings / headings
    major_opener_top_margin_mm: float = 55.0
    major_opener_bottom_margin_mm: float = 15.0
    major_work_description_gap_mm: float = 3.0
    major_work_font_pt: float = 23.5
    collection_division_font_pt: float = 25.0
    work_description_font_delta_pt: float = -1.0
    work_description_bottom_margin_mm: float = 7.0
    author_note_bottom_margin_mm: float = 7.0
    part_heading_font_pt: float = 11.6
    part_heading_margin_bottom_mm: float = 6.0
    chapter_section_font_pt: float = 11.6
    chapter_section_margin_top_mm: float = 5.0
    chapter_section_margin_bottom_mm: float = 3.0
    subdivision_font_pt: float = 14.8
    subdivision_margin_top_mm: float = 10.0
    subdivision_margin_bottom_mm: float = 5.0
    h3_font_pt: float = 13.0
    minor_heading_font_pt: float = 11.4

    # Table of contents
    toc_mode: str = "auto"
    back_toc_mode: str = "off"
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
    verse_font_stack: str = ""
    verse_font_size_delta_pt: float = -0.4

    # Drama / cast lists
    cast_max_width_mm: float = 132.0
    cast_line_height: float = 1.16

    # Image behavior
    image_policy: str = "functional"

    # Cleanup behavior
    smart_punctuation: bool = True
    rule_pack_dir: str = "rules"
    rule_packs: str = "generic_epub.yaml"
    write_ai_rule_suggestions: bool = True

    # Front matter / work divider pages
    include_half_title_page: bool = False
    include_title_page: bool = True
    half_title_page_font_pt: float = 22.0
    title_page_font_pt: float = 30.0
    title_page_collection_font_pt: float = 21.0
    title_page_of_font_pt: float = 14.0
    title_page_subtitle: str = ""
    include_source_note: bool = False
    source_note_text: str = "Prepared as a single-page A4 print interior from the supplied EPUB source."
    major_opener_blank_before: bool = True
    major_opener_blank_after: bool = True

    # Pipeline behavior
    strict: bool = False
    no_sample_requirement: bool = False


# --------------------------------------------------------------------------------------
# EPUB spine document state
# --------------------------------------------------------------------------------------


@dataclass
class TocEntry:
    """A single table-of-contents entry."""
    level: int
    title: str
    target_id: str
    kind: str = "work"  # division, work, backmatter
    source_level: int = 0


@dataclass
class SpineDoc:
    """Represents one EPUB spine item after scanning and classification."""
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


# --------------------------------------------------------------------------------------
# Pipeline audit trail
# --------------------------------------------------------------------------------------


@dataclass
class BuildLog:
    """Complete audit trail for a single build.

    Tracks all removals, warnings, failures, AI decisions, and auto-fixes.
    """
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
        from pipeline._utils import clean_text
        s = clean_text(sample)[:220]
        self.removed_blocks.append(f"{reason}: {s}" if s else reason)


# --------------------------------------------------------------------------------------
# QA verdict
# --------------------------------------------------------------------------------------


@dataclass
class QAVerdict:
    """Comprehensive quality-assurance state for a rendered PDF."""
    page_count: int = 0
    non_a4_pages: list[dict[str, Any]] = field(default_factory=list)
    possible_line_spills: list[dict[str, Any]] = field(default_factory=list)
    dark_pages: list[int] = field(default_factory=list)
    possible_blank_page_artifacts: list[dict[str, Any]] = field(default_factory=list)
    possible_header_collisions: list[dict[str, Any]] = field(default_factory=list)
    possible_narrow_columns: list[dict[str, Any]] = field(default_factory=list)
    visible_image_filename_artifacts: list[dict[str, Any]] = field(default_factory=list)
    toc_page_number_warnings: list[str] = field(default_factory=list)
    toc_duplicate_warnings: list[str] = field(default_factory=list)
    empty_content_pages: list[dict[str, Any]] = field(default_factory=list)
    duplicate_title_page_warnings: list[str] = field(default_factory=list)
    opener_page_warnings: list[dict[str, Any]] = field(default_factory=list)
    work_description_style_warnings: list[dict[str, Any]] = field(default_factory=list)
    first_body_folio_warnings: list[str] = field(default_factory=list)
    source_apparatus_warnings: list[dict[str, Any]] = field(default_factory=list)
    openai_visual_flags: list[str] = field(default_factory=list)
    openai_visual_issue_lines: list[str] = field(default_factory=list)
    text_qa_flags: list[str] = field(default_factory=list)
    text_qa_issue_lines: list[str] = field(default_factory=list)
    ai_rule_suggestion_file: str = ""
    fonts_seen: list[str] = field(default_factory=list)
    font_embedding_warnings: list[str] = field(default_factory=list)
    images_seen: int = 0
    qa_renders: list[str] = field(default_factory=list)
    # Typographic QA
    possible_orphan_pages: list[dict[str, Any]] = field(default_factory=list)
    possible_widow_lines: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return bool(
            self.non_a4_pages
            or self.dark_pages
            or self.possible_blank_page_artifacts
            or self.possible_header_collisions
            or self.possible_line_spills
            or self.toc_duplicate_warnings
            or self.visible_image_filename_artifacts
            or self.empty_content_pages
            or self.duplicate_title_page_warnings
            or self.opener_page_warnings
            or self.work_description_style_warnings
            or self.first_body_folio_warnings
            or self.source_apparatus_warnings
            or self.openai_visual_flags
            or self.text_qa_flags
            or self.font_embedding_warnings
            or self.possible_orphan_pages
        )
