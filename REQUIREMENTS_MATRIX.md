# Requirements Matrix Against the Deluxe EPUB-to-PDF Prompt

Legend:

- **Implemented**: deterministic code exists.
- **Implemented + QA**: deterministic code exists plus PDF preflight/reporting.
- **AI-assisted**: optional OpenAI mode improves judgment-heavy cases.
- **Heuristic**: code handles common cases but cannot guarantee all malformed EPUBs.
- **Manual QA required**: rendered PDF pages must still be inspected.

| Prompt requirement | Implementation status | Where |
|---|---|---|
| A4 trim, single-page PDF | Implemented + QA | `css_text()`, `preflight_pdf()` |
| Deluxe scholarly/classics baseline | Implemented | `Settings`, `css_text()` |
| EB Garamond/Garamond-style body | Implemented, user must have font installed or provide stack | `font_stack` setting |
| Equal left/right margins | Implemented | `margin_side_mm` |
| Dark readable body text | Implemented by CSS; visual QA required | `html { color: #111 }`, QA renders |
| Front/body/back structure | Heuristic + AI-assisted | `scan_spine_items()`, `heuristic_classify_doc()`, `apply_openai_book_plan()` |
| Half title, full title, source page, TOC | Implemented | `compose_html()` |
| Roman front matter, Arabic body | Implemented | `@page front`, `.main { counter-reset: page 1 }` |
| Suppress folios/runners on title pages | Implemented | `@page title` |
| Recto starts for title/TOC/main/major works | Implemented via CSS paged media | `break-before: right` |
| Intentional blank pages truly blank | Implemented + QA | `@page :blank`, `preflight_pdf()` blank-artifact check |
| Running heads only on normal text pages | Implemented | `@page body`, `@page opener`, `@page title` |
| Verso collection title / recto current work | Implemented | `@page body:left`, `@page body:right`, CSS `string-set` |
| Do not use chapter titles as runners | Heuristic + AI-assisted | heading classification filters |
| Running-head rule not by background gradients | Implemented | CSS border stroke only |
| Header/rule clearance QA | Implemented + auto-fix | `analyze_header_clearance()`, `auto_fix_settings()` |
| Folio suppression on blanks/title/display pages | Implemented for blank/title/openers partly | CSS page types; manual QA for unusual display pages |
| Major work openers formal and recto | Implemented | `h1.major-work`, synthetic openers |
| Ordinary chapters not forced to new pages | Implemented | h2/h3 rules do not use recto starts except act openings |
| Heading orphan prevention | Implemented partly | `break-after: avoid` |
| Paragraphs remain breakable | Implemented | no `break-inside: avoid` on ordinary `p` |
| Promotional/catalogue removal | Heuristic + AI-assisted | `PROMO_PATTERNS`, `remove_promotional_blocks()`, OpenAI doc removal |
| Local mini-TOC removal | Heuristic + QA | `remove_local_mini_tocs()` |
| Image preservation/removal distinction | Heuristic + AI-assisted | `should_keep_image()`, `ai_image_decision()` |
| Remove orphan plate captions | Heuristic | `remove_orphan_image_captions()` |
| Preserve maps/diagrams/runes/facsimiles/image-text | Heuristic + AI-assisted | `FUNCTIONAL_IMAGE_CLUES`, OpenAI image check |
| Poetry detection | Heuristic + AI-assisted | `extract_probe()`, `normalize_poetry()` |
| Preserve `<br>` verse lineation | Implemented | `split_br_verse_block()` |
| Group short-line verse sequences | Heuristic | `normalize_poetry()` |
| Verse ragged-right, not justified | Implemented | `.verse-block`, `.verse-line` |
| Runover hanging indents | Implemented in CSS | `.verse-line { text-indent: -1.4em; padding-left: 1.4em }` |
| Avoid forcing every short poem to new page | Implemented | poetry blocks do not force page breaks |
| Long narrative verse wider measure | Implemented basic | `.verse-block max-width: 128mm` |
| Poetry TOC hierarchy | Heuristic + AI-assisted | TOC policy and structure planning |
| Verse drama preservation | Partial heuristic | cast/stage/act handling; manual QA required |
| Cast list / dramatis personae detection | Heuristic | `normalize_cast_and_drama()` |
| Cast entry normalization | Heuristic | `normalize_cast_entries()` |
| Act/Scene transition after cast list | Implemented partially | `.act-opening`, `break-before: page` |
| Typographic punctuation cleanup | Implemented conservative | `simple_typographic_cleanup()` |
| Note markers as superscripts | Implemented heuristic | `normalize_notes_refs()` |
| TOC with page numbers | Implemented via CSS | `target-counter(attr(href), page)` |
| Hierarchical TOC | Implemented basic + AI-assisted | `TocEntry`, `build_toc()` |
| Global CSS/layout normalization | Implemented | `strip_bad_attributes()`, CSS `[style]` normalization |
| PDF optimization/compression | Implemented | `optimize_pdf()` |
| Font inventory | QA implemented; full embedding proof depends on PDF internals | `preflight_pdf()` |
| A4 page-size preflight | Implemented | `preflight_pdf()` |
| Dark/black artifact preflight | Implemented on rendered samples | `render_selected_pages()`, Pillow average |
| Broken word/single-letter spill detection | Heuristic QA | `looks_like_bad_spill()` |
| Narrow column detection | Heuristic QA | `analyze_narrow_columns()` |
| Blank page artifact detection | Heuristic QA | `preflight_pdf()` |
| Representative page renders | Implemented | `render_selected_pages()` |
| OpenAI visual QA | Implemented optional | `openai_visual_qa()` |
| Sample-first workflow | Implemented warning + `--sample-pages` | `build_pipeline()` |
| Strict delivery gate | Implemented | `--strict`, `QAVerdict.has_blockers` |
| Final summary | Implemented | `build_summary.json`, `qa_report.txt` |

## Still requiring human review

The script is intentionally strict, but no local automation can guarantee all of these without reviewing rendered pages:

1. whether a specific image is truly authorial or a publisher plate when context is ambiguous;
2. whether a malformed EPUB's short-line passages are poetry or broken OCR/prose;
3. whether a section heading is a major work title or merely a subtitle in badly tagged EPUBs;
4. subtle rivers, loose lines, and aesthetic justification quality;
5. very complex dramatic/verse-drama layouts;
6. malformed scholarly apparatus, line-numbered philology, timelines, pronunciation guides, or tables.

The project therefore treats QA renders, `qa_report.txt`, `qa_verdict.json`, and optional `openai_visual_qa.txt` as part of the production process, not as afterthoughts.
