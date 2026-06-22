"""
CSS generation and HTML composition for the print PDF.
"""
from __future__ import annotations

import dataclasses
import html
import json
import re
from typing import Any, Optional

from pipeline import _constants as C
from pipeline._models import Settings, TocEntry
from pipeline._utils import clean_display_title, clean_display_title_for_toc, clean_text, normalized_title_key


SOURCE_TOC_TITLE_RE = re.compile(
    r"^(?:the\s+)?principal\s+contents\.?$|^series\s+contents$|^alphabetical\s+list\s+of\s+titles$|"
    r"^(?:list\s+of\s+)?illustrations$",
    re.I,
)

TRIVIAL_STANDALONE_TOC_RE = re.compile(
    r"^(?:introduction|preface|foreword|prologue|chapter\s+(?:i|1)|part\s+(?:i|1))\.?$",
    re.I,
)


def usable_toc_entries(
    toc: list[TocEntry],
    settings: Optional[Settings] = None,
    available_target_ids: Optional[set[str]] = None,
) -> list[TocEntry]:
    """Return TOC entries worth printing in a generated book TOC."""
    book_key = normalized_title_key(settings.title if settings else "")
    toc_mode = clean_text(settings.toc_mode if settings else "hierarchical").strip().lower()
    simple_mode = toc_mode == "simple"
    prefer_source_top_level = simple_mode and any(e.source_level == 1 for e in toc)
    seen_keys: set[str] = set()
    usable: list[TocEntry] = []
    for e in toc:
        title = clean_display_title(e.title)
        if not title:
            continue
        key = normalized_title_key(title)
        if not key or SOURCE_TOC_TITLE_RE.fullmatch(title.strip()):
            continue
        if key == book_key or key in seen_keys:
            continue
        if simple_mode:
            if prefer_source_top_level:
                if e.source_level != 1 or e.kind in {"frontmatter", "backmatter"}:
                    continue
            elif e.level > 1:
                continue
        if available_target_ids is not None and e.target_id not in available_target_ids:
            continue
        seen_keys.add(key)
        usable.append(e)
    if len(usable) <= 1:
        return []
    return usable


def build_toc(
    toc: list[TocEntry],
    settings: Optional[Settings] = None,
    page_numbers: Optional[dict[str, int]] = None,
    available_target_ids: Optional[set[str]] = None,
) -> str:
    """Build a practical print TOC with either simple or hierarchical styling."""
    if not toc:
        return ""

    toc_mode = clean_text(settings.toc_mode if settings else "hierarchical").strip().lower()
    if toc_mode not in {"simple", "hierarchical"}:
        toc_mode = "hierarchical"
    simple_mode = toc_mode == "simple"
    entries = usable_toc_entries(toc, settings, available_target_ids)
    if not entries:
        return ""
    explicit_numbers = page_numbers is not None
    list_classes = ["toc-list", f"toc-{toc_mode}"]
    if explicit_numbers:
        list_classes.append("toc-explicit")
    out = [f'<ol class="{" ".join(list_classes)}">']
    emitted = 0
    for e in entries:
        title = clean_display_title_for_toc(e.title)
        key = normalized_title_key(title)

        level = 1 if simple_mode else min(max(e.level, 1), 4)
        target = html.escape(e.target_id)
        if explicit_numbers:
            page_no = page_numbers.get(e.target_id) if page_numbers else None
            page_text = "" if page_no is None else str(page_no)
            missing_class = " toc-missing-page" if page_no is None else ""
            out.append(
                f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)}{missing_class}">'
                f'<a href="#{target}"><span class="toc-entry-title">{html.escape(title)}</span>'
                f'<span class="toc-leader" aria-hidden="true"></span>'
                f'<span class="toc-page-number">{html.escape(page_text)}</span></a></li>'
            )
        else:
            out.append(
                f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)}"><a href="#{target}">{html.escape(title)}</a></li>'
            )
        emitted += 1

    if emitted == 0:
        return ""
    out.append("</ol>")
    return "\n".join(out)


def css_text(settings: Settings, font_face_css: str = "") -> str:
    """Generate the complete print stylesheet driven by Settings."""
    title = settings.title.replace('"', '\\"')
    fs = settings.font_stack
    hyphens = "auto" if settings.hyphenate else "none"
    prose_align = "justify" if settings.justify_prose else "left"
    runner_layout = settings.runner_layout.strip().lower()
    runner_rule_style = settings.runner_rule_style.strip().lower()
    runner_left_font_pt = settings.runner_left_font_pt if settings.runner_left_font_pt is not None else settings.runner_font_pt
    runner_right_font_pt = settings.runner_right_font_pt if settings.runner_right_font_pt is not None else settings.runner_font_pt
    body_top_mm = settings.margin_top_mm + max(0.0, settings.runner_body_clearance_mm)
    work_description_font_pt = max(6.0, settings.body_size_pt + settings.work_description_font_delta_pt)
    verse_font_stack = settings.verse_font_stack.strip() or settings.font_stack
    verse_font_pt = max(6.0, settings.body_size_pt + settings.verse_font_size_delta_pt)

    ligature = settings.ligature_setting.strip().lower()
    if ligature == "none":
        ligature_css = "font-variant-ligatures: no-common-ligatures;"
    elif ligature == "all":
        ligature_css = "font-variant-ligatures: common-ligatures discretionary-ligatures historical-ligatures;"
    elif ligature == "discretionary":
        ligature_css = "font-variant-ligatures: common-ligatures discretionary-ligatures;"
    else:
        ligature_css = "font-variant-ligatures: common-ligatures;"

    rule_css = ""
    if runner_rule_style in {"single", "split"}:
        rule_css = f"border-bottom: {settings.runner_rule_weight_pt}pt solid {settings.runner_rule_color}; padding-bottom: {settings.runner_rule_gap_mm}mm;"
    split_rule_css = rule_css if runner_rule_style == "split" else ""

    # Build running head @page CSS based on layout
    if runner_layout == "right_title_full_rule":
        body_left_header_css = f'''
  @top-left {{
    content: string(collection-title);
    font-family: {fs}; font-size: {runner_left_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
    color: #111; {rule_css} vertical-align: top; text-align: left; padding-top: {settings.runner_title_top_mm}mm;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}
  @top-center {{ content: normal; border: none; padding: 0; }}
  @top-right {{ content: normal; border: none; padding: 0; }}'''
        body_right_header_css = f'''
  @top-left {{ content: normal; border: none; padding: 0; }}
  @top-center {{ content: normal; border: none; padding: 0; }}
  @top-right {{
    content: string(current-work);
    font-family: {fs}; font-size: {runner_right_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {rule_css} vertical-align: top; text-align: right; padding-top: {settings.runner_title_top_mm}mm;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}'''
    elif runner_layout == "centered_single_rule":
        body_left_header_css = f'''
  @top-left {{ content: normal; }}
  @top-center {{
    content: string(current-work);
    font-family: {fs}; font-size: {runner_right_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {rule_css} vertical-align: bottom; text-align: center;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}
  @top-right {{ content: normal; }}'''
        body_right_header_css = body_left_header_css
    elif runner_layout != "alternating":
        body_left_header_css = f'''
  @top-left {{
    content: string(collection-title);
    font-family: {fs}; font-size: {runner_left_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
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
    font-family: {fs}; font-size: {runner_right_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {split_rule_css} vertical-align: bottom;
    white-space: nowrap; hyphens: none; overflow: hidden; text-overflow: clip; line-height: 1;
  }}'''
        body_right_header_css = body_left_header_css
    else:
        body_left_header_css = f'''
  @top-left {{
    content: string(collection-title);
    font-family: {fs}; font-size: {runner_left_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
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
    font-family: {fs}; font-size: {runner_right_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
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
  @bottom-center {{ content: normal; }}
}}
@page nofolio {{
  @top-left {{ content: normal; }}
  @top-center {{ content: normal; }}
  @top-right {{ content: normal; }}
  @bottom-center {{ content: normal; }}
}}
html {{
  font-family: {fs};
  font-size: {settings.body_size_pt}pt;
  line-height: {settings.line_height};
  color: {settings.text_color};
  font-weight: {settings.body_font_weight};
  hyphens: {hyphens};
  {ligature_css}
}}
body {{ margin: 0; }}
.set-collection {{ string-set: collection-title content(); height: 0; overflow: hidden; }}
.set-current-work {{ string-set: current-work content(); display: block; height: 0; overflow: hidden; font-size: 0; line-height: 0; color: transparent; }}
.frontmatter {{ page: front; }}
.half-title-page {{ page: title; }}
.title-page, .source-page {{ page: title; break-before: right; }}
.half-title-page {{ display: flex; align-items: center; justify-content: center; height: 220mm; }}
.half-title-page h1 {{ font-size: {settings.half_title_page_font_pt}pt; font-weight: 400; letter-spacing: .06em; text-transform: uppercase; text-align: center; }}
.title-page {{ text-align: center; padding-top: 82mm; }}
.title-page h1 {{ font-size: {settings.title_page_font_pt}pt; line-height: 1.08; font-weight: 400; letter-spacing: .035em; text-transform: uppercase; }}
.title-page .subtitle {{ margin-top: 12mm; font-size: 12pt; letter-spacing: .08em; text-transform: uppercase; }}
.title-page .title-collection {{ font-size: {settings.title_page_collection_font_pt}pt; line-height: 1.05; font-style: italic; letter-spacing: .04em; text-transform: none; }}
.title-page .title-of {{ margin-top: 4mm; font-size: {settings.title_page_of_font_pt}pt; line-height: 1.0; letter-spacing: .18em; text-transform: lowercase; }}
.title-page .title-author {{ margin-top: 2.5mm; font-size: {settings.title_page_font_pt}pt; line-height: 1.08; font-weight: 400; letter-spacing: .035em; text-transform: uppercase; }}
.source-page {{ padding-top: 92mm; text-align: center; font-size: 10.3pt; }}
.toc-page {{ page: front; break-before: right; }}
.toc-page h1 {{ margin: 0 0 8mm; text-align: center; font-size: {settings.toc_title_font_pt}pt; font-weight: 400; letter-spacing: .04em; text-transform: uppercase; }}
.back-toc-page {{ page: body; break-before: page; }}
.toc-list {{ list-style: none; padding: 0; margin: 0; }}
.toc-list li {{ margin: 0 0 {settings.toc_entry_gap_mm}mm 0; line-height: {settings.toc_line_height}; }}
.toc-list a {{ color: inherit; text-decoration: none; }}
.toc-simple li {{ margin-left: 0 !important; }}
.toc-simple .toc-level-1,
.toc-simple .toc-level-2,
.toc-simple .toc-level-3,
.toc-simple .toc-level-4 {{
  margin-top: 0 !important;
  font-size: {settings.toc_level_1_font_pt}pt;
  letter-spacing: .01em;
  text-transform: none;
}}
.toc-hierarchical .toc-level-1 {{ font-size: {settings.toc_level_1_font_pt}pt; text-transform: uppercase; letter-spacing: .03em; margin-top: 2.8mm !important; }}
.toc-hierarchical .toc-level-2 {{ margin-left: 4mm !important; font-size: {settings.toc_level_2_font_pt}pt; }}
.toc-hierarchical .toc-level-3 {{ margin-left: 8mm !important; font-size: {settings.toc_level_3_font_pt}pt; }}
.toc-hierarchical .toc-level-4 {{ margin-left: 12mm !important; font-size: {settings.toc_level_4_font_pt}pt; }}
.toc-explicit a {{ display: flex; align-items: baseline; width: 100%; }}
.toc-explicit a::after {{ content: none; }}
.toc-explicit .toc-entry-title {{ flex: 0 1 auto; min-width: 0; max-width: calc(100% - 3.4em); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.toc-explicit .toc-leader {{ flex: 1 1 1.5em; min-width: 0; border-bottom: .45pt dotted #444; transform: translateY(-.18em); margin: 0 .45em; }}
.toc-explicit .toc-page-number {{ flex: 0 0 auto; text-align: right; white-space: nowrap; min-width: 2.2em; }}
.toc-explicit .toc-missing-page .toc-entry-title {{ white-space: normal; overflow: visible; text-overflow: clip; }}
.toc-explicit .toc-missing-page .toc-leader {{ border-bottom: 0; }}
.toc-explicit .toc-missing-page .toc-page-number {{ min-width: 0; }}
.toc-leader {{ border-bottom: .45pt dotted #444; transform: translateY(-.18em); }}
.toc-page-number {{ min-width: 2.2em; text-align: right; }}
.main a {{ color: inherit; text-decoration: none; }}
.main {{ page: body; counter-reset: bodyPage 0; break-before: auto; string-set: collection-title "{title}"; }}
.body-page-reset {{ counter-reset: bodyPage 0; height: 0; line-height: 0; font-size: 0; page: body; }}
.epub-doc.starts-major-work {{ break-before: auto; }}
.epub-doc.starts-chapter-opener {{ break-before: auto; }}
.epub-doc.doc-frontmatter {{ page: front; break-before: page; }}
.true-blank {{ page: nofolio; break-before: page; break-after: page; height: 0; }}
h1, h2, h3, h4, h5, h6 {{ font-weight: 400; orphans: 2; widows: 2; hyphens: none; break-after: avoid-page; }}
h1.major-work, h2.major-work, h1.collection-division {{
  page: opener; break-before: page; break-after: page; string-set: current-work content();
  margin: {settings.major_opener_top_margin_mm}mm 0 {settings.major_opener_bottom_margin_mm}mm; text-align: center; font-size: {settings.major_work_font_pt}pt; line-height: 1.08; letter-spacing: .04em; text-transform: uppercase;
}}
h1.backmatter-opener, h2.backmatter-opener, h3.structural-backmatter-opener {{
  page: body;
  break-before: page;
  page-break-before: always;
  break-after: avoid-page;
  page-break-after: avoid;
  margin: 18mm 0 7mm;
  text-align: center;
  font-size: {settings.subdivision_font_pt}pt;
  line-height: 1.12;
  letter-spacing: .045em;
  text-transform: uppercase;
}}
h1.note-section-opener, h2.note-section-opener, h3.note-section-opener {{
  page: body;
  break-before: page;
  page-break-before: always;
  break-after: avoid-page;
  page-break-after: avoid;
  margin: 13mm 0 6mm;
  text-align: center;
  font-size: {settings.h3_font_pt}pt;
  line-height: 1.12;
  letter-spacing: .035em;
  text-transform: uppercase;
}}
.note-section-opener ~ p {{
  font-size: 96%;
  line-height: 1.16;
}}
.note-section-opener ~ ol,
.note-section-opener ~ ul {{
  font-size: 96%;
  line-height: 1.16;
}}
h1.embedded-work-opener, h2.embedded-work-opener {{
  page: body;
  break-before: page;
  page-break-before: always;
  break-after: avoid-page;
  page-break-after: avoid;
  margin: 18mm 0 7mm;
  font-size: {settings.subdivision_font_pt}pt;
  line-height: 1.12;
  letter-spacing: .05em;
  text-transform: uppercase;
}}
h1.frontmatter-opener, h2.frontmatter-opener {{
  break-before: page; page-break-before: always; break-after: avoid-page; page-break-after: avoid;
  margin: 20mm 0 9mm; text-align: center; font-size: {settings.subdivision_font_pt}pt; line-height: 1.12; letter-spacing: .04em; text-transform: uppercase;
}}
.main .epub-doc:first-of-type > h1.frontmatter-opener:first-child,
.main .epub-doc:first-of-type > h2.frontmatter-opener:first-child {{
  break-before: auto;
  page-break-before: auto;
}}
/* Extra breathing room between a work title and its note/description. */
h1.major-work + p.work-description,
h2.major-work + p.work-description,
h1.major-work + p.author-note,
h2.major-work + p.author-note,
h1.subdivision + p.author-note,
h2.subdivision + p.author-note {{
  margin-top: {settings.major_work_description_gap_mm}mm;
}}
.epub-doc.starts-major-work > h1.major-work,
.epub-doc.starts-major-work > h2.major-work,
.epub-doc.starts-major-work > h1.collection-division,
.epub-doc.starts-major-work > h1.frontmatter-opener,
.epub-doc.starts-major-work > h2.frontmatter-opener {{ break-before: page; }}
h1.collection-division {{ font-size: {settings.collection_division_font_pt}pt; letter-spacing: .07em; }}
h1.subdivision, h2.subdivision {{
  break-after: avoid-page; page-break-after: avoid; orphans: 2; widows: 2; margin: {settings.subdivision_margin_top_mm}mm 0 {settings.subdivision_margin_bottom_mm}mm; text-align: center; font-size: {settings.subdivision_font_pt}pt; letter-spacing: .035em; text-transform: uppercase;
}}
h1.embedded-authored-work {{
  break-before: auto;
  page-break-before: auto;
}}
h2.story-work-opener {{
  break-before: page;
  page-break-before: always;
  break-after: avoid-page;
  page-break-after: avoid;
  margin-top: {settings.subdivision_margin_top_mm}mm;
}}
h2.story-work-opener + h3 {{
  break-before: avoid-page;
  page-break-before: avoid;
  break-after: avoid-page;
  page-break-after: avoid;
  margin-top: 0;
}}
h2.story-work-opener + h3 + p {{
  break-before: avoid-page;
  page-break-before: avoid;
}}
h1.backmatter-opener + h2,
h2.backmatter-opener + h2,
h3.structural-backmatter-opener + h2,
h1.backmatter-opener + h3,
h2.backmatter-opener + h3,
h3.structural-backmatter-opener + h3 {{
  break-before: avoid-page;
  page-break-before: avoid;
  break-after: avoid-page;
  page-break-after: avoid;
  margin-top: 0;
}}
h1.backmatter-opener + p,
h2.backmatter-opener + p,
h3.structural-backmatter-opener + p,
h1.backmatter-opener + h2 + p,
h2.backmatter-opener + h2 + p,
h3.structural-backmatter-opener + h2 + p,
h1.backmatter-opener + h3 + p,
h2.backmatter-opener + h3 + p,
h3.structural-backmatter-opener + h3 + p {{
  break-before: avoid-page;
  page-break-before: avoid;
}}
h2.chapter-opener-title,
p.chapter-opener-title {{
  break-before: avoid-page; break-after: avoid-page; page-break-before: avoid; page-break-after: avoid;
  margin: 0 0 {settings.subdivision_margin_bottom_mm}mm;
  text-align: center;
  font-size: {settings.body_size_pt}pt;
  line-height: 1.12;
  letter-spacing: .02em;
  text-transform: none;
}}
.chapter-opener-block {{
  break-inside: avoid-page;
  page-break-inside: avoid;
}}
h2.part-heading {{
  break-before: page; page-break-before: always; break-after: avoid-page; page-break-after: avoid;
  margin: 18mm 0 {settings.part_heading_margin_bottom_mm}mm; font-size: {settings.part_heading_font_pt}pt; letter-spacing: .035em; text-align: center; text-transform: uppercase;
}}
.main .epub-doc:first-of-type > h2.part-heading:first-of-type,
.main .epub-doc:first-of-type > span.set-current-work:first-child + h2.part-heading,
.main .epub-doc:first-of-type h2.part-heading:first-of-type {{
  break-before: auto;
  page-break-before: auto;
}}
h2.part-heading + h2.subdivision {{
  margin-top: 0;
}}
h2.chapter-section-heading {{
  margin: {settings.chapter_section_margin_top_mm}mm 0 {settings.chapter_section_margin_bottom_mm}mm; text-align: center; font-size: {settings.chapter_section_font_pt}pt; letter-spacing: .035em; text-transform: uppercase;
}}
h2.subdivision + h2.chapter-section-heading {{
  margin-top: {settings.chapter_section_margin_top_mm}mm;
}}
.epub-doc.starts-chapter-opener > h2.subdivision:first-child,
.epub-doc.starts-chapter-opener > h2.chapter-section-heading:first-child {{
  break-before: page; page-break-before: always; break-after: avoid-page; page-break-after: avoid;
}}
.epub-doc.follows-embedded-authored-work > h2.subdivision:first-child,
.epub-doc.follows-embedded-authored-work > h2.chapter-section-heading:first-child,
.epub-doc.follows-embedded-authored-work .chapter-opener-block > h2.subdivision:first-child,
.epub-doc.follows-embedded-authored-work .chapter-opener-block > h2.chapter-section-heading:first-child {{
  break-before: auto;
  page-break-before: auto;
  margin-top: 4mm;
}}
.epub-doc.follows-embedded-authored-work > span.set-current-work {{
  display: none;
}}
.epub-doc.starts-embedded-authored-work > span.set-current-work {{
  display: none;
}}
.epub-doc.follows-embedded-authored-work > h1.subdivision:first-child,
.epub-doc.follows-embedded-authored-work > h1.embedded-authored-work,
.epub-doc.starts-embedded-authored-work > h1.subdivision:first-child,
.epub-doc.starts-embedded-authored-work > h1.embedded-authored-work {{
  margin-top: 10mm;
  margin-bottom: 2mm;
  text-align: center;
}}
.epub-doc.starts-embedded-authored-work > h1.embedded-authored-work:first-child {{
  break-before: page;
  page-break-before: always;
}}
.epub-doc.starts-embedded-authored-work {{
  break-before: page;
  page-break-before: always;
}}
.epub-doc.follows-embedded-authored-work > h1.subdivision:first-child + p.author-note,
.epub-doc.follows-embedded-authored-work > h1.embedded-authored-work + p.author-note,
.epub-doc.follows-embedded-authored-work > p.embedded-work-author,
.epub-doc.starts-embedded-authored-work > h1.subdivision:first-child + p.author-note,
.epub-doc.starts-embedded-authored-work > h1.embedded-authored-work + p.author-note,
.epub-doc.starts-embedded-authored-work > p.embedded-work-author {{
  margin-top: 0;
  margin-bottom: 7mm;
  text-align: center;
}}
.epub-doc.follows-embedded-authored-work > h2.subdivision,
.epub-doc.follows-embedded-authored-work > h2.chapter-section-heading,
.epub-doc.starts-embedded-authored-work > h2.subdivision,
.epub-doc.starts-embedded-authored-work > h2.chapter-section-heading {{
  text-align: center;
  margin-top: 0;
  margin-bottom: 7mm;
}}
h2.subdivision + p, h2.chapter-section-heading + p {{ break-before: avoid-page; }}
h1 + p, h2 + p, h3 + p, h4 + p, h5 + p, h6 + p {{ break-before: avoid-page; widows: 5; orphans: 2; }}
h2.act-opening, h2.act-scene-heading, h3.act-scene-heading {{ break-before: page; margin: 22mm 0 7mm; text-align: center; font-size: 14pt; letter-spacing: .06em; text-transform: uppercase; }}
h3 {{ margin: 8mm 0 4mm; text-align: center; font-size: {settings.h3_font_pt}pt; }}
h4, h5, h6, .minor-heading {{ margin: 6mm 0 3mm; text-align: center; font-size: {settings.minor_heading_font_pt}pt; font-style: italic; }}
p {{ margin: 0; text-align: {prose_align}; text-indent: {settings.paragraph_indent_em}em; widows: 2; orphans: 2; }}
h1 + p, h2 + p, h3 + p, h4 + p, .no-indent, blockquote p:first-child, .stage-direction, .cast-list p {{ text-indent: 0; }}
p.work-description {{
  font-size: {work_description_font_pt}pt; font-style: italic; text-indent: 0; margin: 0;
}}
p.work-description.work-description-end {{
  margin-bottom: {settings.work_description_bottom_margin_mm}mm;
}}
p.source-title-page-title {{
  font-style: normal; text-indent: 0; margin: 0;
}}
p.author-note {{
  font-size: {settings.body_size_pt}pt; font-style: italic; text-indent: 0; margin: 0 0 {settings.author_note_bottom_margin_mm}mm 0;
}}
p.epigraph {{
  max-width: 92mm !important;
  margin: 0 auto 3mm !important;
  font-size: {max(6.0, settings.body_size_pt - 0.5)}pt;
  line-height: 1.16;
  text-align: justify;
  text-indent: 0 !important;
  font-style: italic;
  hyphens: none;
}}
p.epigraph + p.epigraph {{
  margin-top: -1mm !important;
}}
.chapter-opener-block p.epigraph {{
  margin-top: 0 !important;
}}
p.epigraph .drop-cap {{
  float: none;
  font-size: inherit;
  line-height: inherit;
  margin: 0;
}}
p.epigraph .epigraph-attribution {{
  display: block;
  margin-top: 1.2mm;
  text-align: right;
  font-style: normal;
  font-size: 88%;
  letter-spacing: .045em;
  text-transform: uppercase;
}}
p.epigraph-attribution {{
  max-width: 92mm !important;
  margin: 0 auto 6mm !important;
  text-align: right;
  text-indent: 0 !important;
  font-style: normal;
  font-size: {max(6.0, settings.body_size_pt - 0.5)}pt;
  letter-spacing: .045em;
  text-transform: uppercase;
}}
.main p.epigraph {{
  max-width: 92mm !important;
  width: auto !important;
  margin-left: auto !important;
  margin-right: auto !important;
}}
.main p.epigraph-attribution {{
  max-width: 92mm !important;
  width: auto !important;
  margin-left: auto !important;
  margin-right: auto !important;
  text-align: right !important;
  text-indent: 0 !important;
}}
p + p {{ margin-top: 0; }}
blockquote {{ margin: 4mm {settings.blockquote_side_margin_mm}mm; font-size: {settings.blockquote_font_percent}%; }}
hr {{ border: 0; border-top: .45pt solid #888; margin: 9mm auto; width: 35%; }}
.verse-block {{
  margin: {settings.verse_block_margin_top_mm}mm auto {settings.verse_block_margin_bottom_mm}mm; max-width: {settings.verse_max_width_mm}mm; font-family: {verse_font_stack}; font-size: {verse_font_pt}pt; line-height: {settings.verse_line_height}; text-align: left !important; text-indent: 0 !important; hyphens: none;
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
body, main, section, article, div {{ max-width: none; }}
[style] {{ max-width: none !important; width: auto !important; margin-left: initial !important; margin-right: initial !important; }}
.main p, .main blockquote, .main ul, .main ol, .main li {{
  max-width: none !important;
  width: auto !important;
  min-width: 0 !important;
}}
.main .calibre, .main .calibre1, .main .calibre2, .main .calibre3,
.main .block, .main .text, .main .body, .main .chapter {{
  max-width: none !important;
  width: auto !important;
}}
.main [class*="center"], .main [class*="right"], .main [class*="left"] {{
  max-width: none !important;
}}
/* Drop caps */
.drop-cap {{
  float: left;
  font-size: 3em;
  line-height: 1.0;
  margin: 0.02em 0.12em 0 0;
  font-weight: 400;
  text-indent: 0;
}}
/* Contain the drop-cap float so it doesn't leak into subsequent paragraphs. */
.drop-cap-paragraph::after {{
  content: "";
  display: table;
  clear: both;
}}
/* Small caps */
.small-caps {{
  font-variant: small-caps;
  letter-spacing: 0.03em;
  text-transform: lowercase;
}}
/* Footnotes: malformed inline note clusters are stabilized as endnote-style blocks. */
.footnotes {{
  page: body;
  font-size: 9pt;
  line-height: 1.25;
  margin: 4mm 0 5mm;
}}
.footnotes .footnote {{
  font-size: 9pt;
  line-height: 1.25;
  text-indent: 0;
  margin: 0 0 1.5mm 0;
  text-align: left;
  widows: 2;
  orphans: 2;
}}
::footnote-marker {{
  content: counter(footnote);
  font-size: 67%;
  vertical-align: super;
  line-height: 0;
}}
.fn-marker {{
  font-size: 67%;
  line-height: 0;
  vertical-align: super;
  margin-right: 0.3em;
}}
"""
    if font_face_css.strip():
        return font_face_css.strip() + "\n\n" + base_css
    return base_css


def compose_html(
    settings: Settings,
    fragments: list[str],
    toc: list[TocEntry],
    toc_page_numbers: Optional[dict[str, int]] = None,
    font_face_css: str = "",
) -> tuple[str, str]:
    """Assemble the complete HTML document and its accompanying CSS."""
    frontmatter_fragments: list[str] = []
    body_fragments: list[str] = []
    for frag in fragments:
        if re.search(r'<section[^>]+class="[^"]*\bdoc-frontmatter\b', frag):
            frontmatter_fragments.append(frag)
        else:
            body_fragments.append(frag)
    frontmatter_body = "\n".join(frontmatter_fragments)
    body = "\n".join(body_fragments)
    available_target_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', frontmatter_body + "\n" + body))
    toc_html = build_toc(toc, settings, toc_page_numbers, available_target_ids=available_target_ids)
    back_toc_mode = clean_text(settings.back_toc_mode).strip().lower()
    if back_toc_mode not in {"simple", "hierarchical"}:
        back_toc_mode = "off"
    back_toc_html = ""
    if back_toc_mode != "off":
        back_toc_settings = dataclasses.replace(settings, toc_mode=back_toc_mode)
        back_toc_html = build_toc(toc, back_toc_settings, toc_page_numbers, available_target_ids=available_target_ids)
    subtitle_html = (
        f'<div class="subtitle">{html.escape(settings.title_page_subtitle)}</div>'
        if clean_text(settings.title_page_subtitle)
        else ""
    )
    half_title_html = (
        f'<section class="frontmatter half-title-page"><h1>{html.escape(settings.title)}</h1></section>'
        if settings.include_half_title_page
        else ""
    )
    title_text = clean_text(settings.title)
    title_match = re.match(
        r"^\s*(Complete|Collected)\s+Works\s+of\s+(.+?)\s*$", title_text, re.I
    )
    reverse_title_match = re.match(
        r"^\s*(.+?)\s+(Collected|Complete)\s+Works\s*$", title_text, re.I
    )
    if title_match:
        title_page_body = (
            f'<div class="title-collection">{html.escape(title_match.group(1).title() + " Works")}</div>'
            f'<div class="title-of">of</div>'
            f'<div class="title-author">{html.escape(title_match.group(2))}</div>'
        )
    elif reverse_title_match:
        title_page_body = (
            f'<div class="title-author">{html.escape(reverse_title_match.group(1))}</div>'
            f'<div class="title-collection">{html.escape(reverse_title_match.group(2).title() + " Works")}</div>'
        )
    else:
        title_page_body = f'<h1>{html.escape(settings.title)}</h1>'
    title_page_html = (
        f'<section class="frontmatter title-page">{title_page_body}{subtitle_html}</section>'
        if settings.include_title_page
        else ""
    )
    source_html = (
        f'<section class="frontmatter source-page"><p class="no-indent">{html.escape(settings.source_note_text)}</p></section>'
        if settings.include_source_note and clean_text(settings.source_note_text)
        else ""
    )
    toc_section_html = (
        f'<section class="frontmatter toc-page"><h1>Contents</h1>{toc_html}</section>'
        if clean_text(toc_html)
        else ""
    )
    back_toc_section_html = (
        f'<section class="toc-page back-toc-page"><h1>Contents</h1>{back_toc_html}</section>'
        if clean_text(back_toc_html)
        else ""
    )
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(settings.title)}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
{half_title_html}
{title_page_html}
{source_html}
{toc_section_html}
{frontmatter_body}
<main class="main">
{body}
{back_toc_section_html}
</main>
</body>
</html>"""
    return doc, css_text(settings, font_face_css=font_face_css)

