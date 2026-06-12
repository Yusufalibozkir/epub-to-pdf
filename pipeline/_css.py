"""
CSS generation and HTML composition for the print PDF.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Optional

from pipeline import _constants as C
from pipeline._models import Settings, TocEntry
from pipeline._utils import clean_display_title, clean_text, normalized_title_key


def build_toc(
    toc: list[TocEntry],
    settings: Optional[Settings] = None,
    page_numbers: Optional[dict[str, int]] = None,
) -> str:
    """Build a practical print TOC, aggressively suppressing duplicate ebook headings."""
    if not toc:
        return '<p class="no-indent toc-empty">No reliable table of contents could be inferred from the EPUB structure.</p>'

    book_key = normalized_title_key(settings.title if settings else "")
    explicit_numbers = page_numbers is not None
    list_class = "toc-list toc-explicit" if explicit_numbers else "toc-list"
    out = [f'<ol class="{list_class}">']
    seen_keys: set[str] = set()
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

        if key == book_key:
            continue

        if key in seen_keys:
            continue
        seen_keys.add(key)

        level = min(max(e.level, 1), 4)
        target = html.escape(e.target_id)
        if explicit_numbers:
            page_no = page_numbers.get(e.target_id) if page_numbers else None
            if page_no is not None:
                # Explicit resolved page number — use grid layout
                page_text = str(page_no)
                out.append(
                    f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)}">'
                    f'<a href="#{target}"><span class="toc-entry-title">{html.escape(title)}</span>'
                    f'<span class="toc-leader" aria-hidden="true"></span>'
                    f'<span class="toc-page-number">{html.escape(page_text)}</span></a></li>'
                )
            else:
                # Fallback: no resolved page number — use CSS target-counter
                out.append(
                    f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)} toc-fallback">'
                    f'<a href="#{target}">{html.escape(title)}</a></li>'
                )
        else:
            out.append(
                f'<li class="toc-level-{level} toc-kind-{html.escape(e.kind)}"><a href="#{target}">{html.escape(title)}</a></li>'
            )
        emitted += 1

    if emitted == 0:
        return '<p class="no-indent toc-empty">No reliable table of contents could be inferred from the EPUB structure.</p>'
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
    body_top_mm = settings.margin_top_mm + max(0.0, settings.runner_body_clearance_mm)
    work_description_font_pt = max(6.0, settings.body_size_pt + settings.work_description_font_delta_pt)

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
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_collection_transform};
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
    font-family: {fs}; font-size: {settings.runner_font_pt}pt; letter-spacing: {settings.runner_letter_spacing_em}em; text-transform: {settings.runner_work_transform};
    color: #111; {rule_css} vertical-align: top; text-align: right; padding-top: {settings.runner_title_top_mm}mm;
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
.toc-page h1 {{ margin: 0 0 14mm; text-align: center; font-size: {settings.toc_title_font_pt}pt; font-weight: 400; letter-spacing: .08em; text-transform: uppercase; }}
.toc-list {{ list-style: none; padding: 0; margin: 0; }}
.toc-list li {{ margin: 0 0 {settings.toc_entry_gap_mm}mm 0; line-height: {settings.toc_line_height}; }}
.toc-list a {{ color: inherit; text-decoration: none; }}
.toc-list a::after {{ content: leader('.') target-counter(attr(href), page); }}
.toc-explicit a {{ display: table; width: 100%; }}
.toc-explicit a::after {{ content: none; }}
.toc-explicit .toc-entry-title {{ display: table-cell; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 0; width: 100%; }}
.toc-explicit .toc-entry-title::after {{ content: '____________________________________________________'; letter-spacing: .12em; color: #444; overflow: hidden; display: inline; white-space: nowrap; margin-left: .6em; }}
.toc-explicit .toc-page-number {{ display: table-cell; text-align: right; white-space: nowrap; padding-left: .7em; min-width: 2.2em; }}
/* Fallback for unresolved TOC entries: use CSS target-counter instead */
.toc-fallback a {{ color: inherit; text-decoration: none; }}
.toc-fallback a::after {{ content: leader('.') target-counter(attr(href), page); }}
.toc-leader {{ border-bottom: .45pt dotted #444; transform: translateY(-.18em); }}
.toc-page-number {{ min-width: 2.2em; text-align: right; }}
.toc-level-1 {{ font-size: {settings.toc_level_1_font_pt}pt; text-transform: uppercase; letter-spacing: .035em; margin-top: 4.8mm !important; }}
.toc-level-2 {{ margin-left: 8mm !important; font-size: {settings.toc_level_2_font_pt}pt; }}
.toc-level-3 {{ margin-left: 14mm !important; font-size: {settings.toc_level_3_font_pt}pt; }}
.toc-level-4 {{ margin-left: 19mm !important; font-size: {settings.toc_level_4_font_pt}pt; }}
.main a {{ color: inherit; text-decoration: none; }}
.main {{ page: body; counter-reset: bodyPage 0; break-before: auto; string-set: collection-title "{title}"; }}
.body-page-reset {{ counter-reset: bodyPage 0; height: 0; line-height: 0; font-size: 0; page: body; }}
.epub-doc.starts-major-work {{ break-before: auto; }}
.epub-doc.starts-chapter-opener {{ break-before: auto; }}
.true-blank {{ page: nofolio; break-before: page; break-after: page; height: 0; }}
h1, h2, h3, h4, h5, h6 {{ font-weight: 400; orphans: 2; widows: 2; hyphens: none; break-after: avoid-page; }}
h1.major-work, h2.major-work, h1.collection-division, h1.backmatter-opener {{
  page: opener; break-before: page; break-after: page; string-set: current-work content();
  margin: {settings.major_opener_top_margin_mm}mm 0 {settings.major_opener_bottom_margin_mm}mm; text-align: center; font-size: {settings.major_work_font_pt}pt; line-height: 1.08; letter-spacing: .04em; text-transform: uppercase;
}}
.epub-doc.starts-major-work > h1.major-work,
.epub-doc.starts-major-work > h2.major-work,
.epub-doc.starts-major-work > h1.collection-division,
.epub-doc.starts-major-work > h1.backmatter-opener {{ break-before: page; }}
h1.collection-division {{ font-size: {settings.collection_division_font_pt}pt; letter-spacing: .07em; }}
h2.subdivision {{
  break-after: avoid-page; page-break-after: avoid; orphans: 2; widows: 2; margin: {settings.subdivision_margin_top_mm}mm 0 {settings.subdivision_margin_bottom_mm}mm; text-align: center; font-size: {settings.subdivision_font_pt}pt; letter-spacing: .035em; text-transform: uppercase;
}}
h2.part-heading {{
  margin: 0 0 {settings.part_heading_margin_bottom_mm}mm; font-size: {settings.part_heading_font_pt}pt; letter-spacing: .035em;
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
h2.subdivision + p, h2.chapter-section-heading + p {{ break-before: avoid-page; }}
h1 + p, h2 + p, h3 + p, h4 + p, h5 + p, h6 + p {{ break-before: avoid-page; widows: 5; orphans: 2; }}
h2.act-opening, h2.act-scene-heading, h3.act-scene-heading {{ break-before: page; margin: 22mm 0 7mm; text-align: center; font-size: 14pt; letter-spacing: .06em; text-transform: uppercase; }}
h3 {{ margin: 8mm 0 4mm; text-align: center; font-size: {settings.h3_font_pt}pt; }}
h4, h5, h6, .minor-heading {{ margin: 6mm 0 3mm; text-align: center; font-size: {settings.minor_heading_font_pt}pt; font-style: italic; }}
p {{ margin: 0; text-align: {prose_align}; text-indent: {settings.paragraph_indent_em}em; widows: 2; orphans: 2; }}
h1 + p, h2 + p, h3 + p, h4 + p, .no-indent, blockquote p:first-child, .stage-direction, .cast-list p {{ text-indent: 0; }}
p.work-description {{
  font-size: {work_description_font_pt}pt; font-style: italic; text-indent: 0; margin: 0 0 {settings.work_description_bottom_margin_mm}mm 0;
}}
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
  line-height: 0.85;
  margin: 0.08em 0.12em 0 0;
  font-weight: 400;
  text-indent: 0;
}}
/* Small caps */
.small-caps {{
  font-variant: small-caps;
  letter-spacing: 0.03em;
  text-transform: lowercase;
}}
/* Footnotes: restructured inline footnote bodies rendered as page-bottom notes */
.footnotes {{
  page: body;
  font-size: 9pt;
  line-height: 1.25;
}}
.footnotes .footnote {{
  float: footnote;
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
    toc_html = build_toc(toc, settings, toc_page_numbers)
    body = "\n".join(fragments)
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
    title_match = re.match(
        r"^\s*(Complete|Collected)\s+Works\s+of\s+(.+?)\s*$", clean_text(settings.title), re.I
    )
    if title_match:
        title_page_body = (
            f'<div class="title-collection">{html.escape(title_match.group(1).title() + " Works")}</div>'
            f'<div class="title-of">of</div>'
            f'<div class="title-author">{html.escape(title_match.group(2))}</div>'
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
<section class="frontmatter toc-page"><h1>Contents</h1>{toc_html}</section>
<main class="main">
{body}
</main>
</body>
</html>"""
    return doc, css_text(settings, font_face_css=font_face_css)
