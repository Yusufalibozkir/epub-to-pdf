# Deluxe EPUB → PDF Pipeline — Complete Logic Reference

> Comprehensive reference for LLMs describing every module, rule, regex,
> classification heuristic, cleanup transformation, CSS rule, and QA check
> in the pipeline. All source files are in `pipeline/`.

---

## 0. Architecture Overview

```
deluxe_epub_to_pdf.py  (thin entry point)
  └─ pipeline._cli: parse_args()
  └─ pipeline._pipeline: build_pipeline()
       └─ apply_rule_packs()           — load external regex extensions
       └─ apply_plugin_regex_patterns() — load plugin regex extensions
       └─ build_once() per pass:
            └─ PipelineDAG (12 stages, see §1)
                 ├─ resolve_title
                 ├─ read_assets
                 ├─ scan_classify          — heuristic + AI + plugin
                 ├─ ai_plan                — optional AI structure planning
                 ├─ prepare_fonts
                 ├─ clean_documents        — per-doc cleaners (≈20 steps)
                 ├─ compose_render         — HTML + CSS → WeasyPrint PDF
                 ├─ resolve_toc            — re-render with page numbers
                 ├─ post_process           — vector rules, subset, optimize
                 ├─ run_qa                 — preflight QA checks
                 ├─ ai_text_qa             — optional DeepSeek text QA
                 └─ ai_visual_qa           — optional OpenAI vision QA
       └─ auto_fix_settings() — apply QA-driven CSS fixes, re-run if needed
```

**Config override chain**: built-in defaults → YAML/JSON config file → CLI flags.

**Extensibility**: rule packs (`rules/` YAML files), plugins (`plugins/` Python files).

---

## 1. Pipeline DAG — `pipeline/_dag.py`

A 12-stage directed acyclic graph executor. Each stage has:
- `name` — unique identifier
- `depends_on` — list of prerequisite stage names
- `runner` — callable `(ctx: PipelineContext) -> dict`
- `cache_key` — optional callable returning a hash string
- `description` — human-readable label

**Execution**: Kahn topological sort → sequential execution → progress to stderr.

**Caching**: Content-addressed via `PipelineCache` (pickle/JSON under
`artifacts/<book>/.pipeline_cache/<namespace>/<2chars>/<full_hash>`).
Each cacheable stage has a `_ck_*` function that computes a combined SHA-256 hash
of its inputs. If unchanged, the stage loads from cache and skips execution.

**Context**: `PipelineContext.data` is a shared dict that accumulates outputs
across stages. Key outputs: `book`, `items`, `docs`, `src_map`, `fragments`,
`toc`, `html_doc`, `css`, `verdict`, `qa_json`, `qa_txt`.

---

## 2. Entry Point & CLI — `pipeline/_cli.py`

**Positional**: EPUB file path (or `--batch DIR`).

**Key flags**:
| Flag | Purpose |
|---|---|
| `--config FILE` | YAML/JSON style config |
| `--out PDF` | Output PDF path |
| `--output-dir DIR` | Output folder (batch mode) |
| `--artifacts-dir DIR` | QA/debug output folder |
| `--title`, `--author` | Override metadata |
| `--volume-mode` | `auto`, `single`, `collection` |
| `--sample-pages N` | Build first-N-page sample |
| `--full-without-sample` | Skip sample-first warning |
| `--use-openai` | AI structure planning |
| `--openai-image-check` | AI image classification |
| `--openai-qa` | AI visual QA on rendered pages |
| `--ai-provider` | `openai`, `deepseek`, `none` |
| `--max-auto-fix-passes N` | Auto-fix re-renders |
| `--batch DIR` | Folder batch conversion |
| `--section "Title"` | Render only one classified logical section for testing |
| `--no-cache`, `--strict`, `--debug-html` | Dev/QA flags |

---

## 3. Config Loading — `pipeline/_config.py`

Three-layer override: `Settings()` defaults → YAML/JSON → CLI flags.

`load_config()` reads YAML/JSON and coerces values into `dataclasses.fields(Settings)`.
Unknown keys → warning.
`apply_cli_overrides()` maps ~30 CLI flags to their `Settings` counterparts.
`resolve_toc_mode()` prompts interactively when mode is `auto`.

`--section TEXT` is intentionally **not** a PDF-page crop. The pipeline still
scans/classifies the whole EPUB, then resolves a contiguous slice beginning at
the first matching classified `division`, `major_work`, or `backmatter` title.
Only that slice is cleaned/rendered, with generated title page + subset-scoped
TOC still included by default.

---

## 4. Data Models — `pipeline/_models.py`

### `Settings` (~80 fields)
Book identity, trim size, type system (font stack, sizes, line height),
margins, running heads (font, layout, rule style), paragraphs, headings
(major work, subdivision, chapter section, minor), TOC, poetry/verse,
drama/cast, image policy, cleanup behavior, front matter pages.
See `deluxe_config.example.yaml` for a complete annotated list.

### `SpineDoc`
One EPUB spine item. Fields: `index`, `item_id`, `name`, `href`, `raw` (bytes),
`headings` (list), `text_sample`, `text_length`, `kind` (see §6), `remove` (bool),
`major_title`, `current_division`, `contains_poetry/drama/images`, `confidence`, `notes`.

### `TocEntry`
`level`, `title`, `target_id`, `kind` (division/work/backmatter/frontmatter/chapter/notes).

### `BuildLog`
Audit trail: removed blocks/documents, kept/removed images, warnings, failures,
AI decisions, counters (poetry blocks, cast sections, local TOCs, typographic fixes).

### `QAVerdict`
Page count, non-A4 pages, line spills, header collisions, narrow columns,
orphan pages, widows/orphans, stranded headings, font embedding warnings,
image filename artifacts, AI text/visual flags.

---

## 5. Constants / Pattern Registry — `pipeline/_constants.py`

All 14 compiled regex patterns are registered both as module-level variables
AND in `_PATTERN_DICT` (for runtime extension via rule packs).

**Runtime pattern API**: `update_pattern(name, pattern)`, `get_pattern(name)`.

**Rule-pack key mapping** (`RULE_PACK_KEYS`): maps config key names like
`promo_patterns` → `PROMO_PATTERNS` global variable name.

**Pattern list** (full details in §17):
`PROMO_PATTERNS`, `BACKMATTER_PATTERNS`, `FRONTMATTER_PATTERNS`,
`LOCAL_TOC_HEADINGS`, `COLLECTION_DIVISIONS`, `MAJOR_WORK_HINTS`,
`CHAPTER_HEADINGS`, `CAST_HEADINGS`, `ACT_SCENE_HEADINGS`,
`PLATE_CAPTION_PATTERNS`, `FUNCTIONAL_IMAGE_CLUES`,
`FUNCTIONAL_IMAGE_SRC_CLUES`, `PUBLISHER_IMAGE_SRC_CLUES`,
`LOCAL_CONTENTS_LINE_RE`, `POETRY_CLASS_RE`, `ROMAN_RE`.

Also: `SMART_QUOTES_MAP` (em-dash, ellipsis), dimensional constants
(`A4_WIDTH_PT`, `A4_HEIGHT_PT`, `PT_PER_MM`).

---

## 6. EPUB Reading & Document Classification — `pipeline/_classify.py`

### Reading
`read_epub(path)` → `(book, list_of_spine_items)` using `ebooklib`.
Skips `nav` spine entries.

### Title/Author Inference
`infer_title_with_source()` — reads DC metadata, falls back to filename stem.
`normalize_author_name()` — handles `"Last, Given Graf"` → `"Given Last"` patterns.
`infer_author_with_source()` — similar fallback chain.

### Text Probe
`extract_probe(raw)` extracts per-document:
- Up to 40 headings (`H1: ...`, `H2: ...` format)
- Text sample (9K limit)
- Total text length
- **Poetry detection**: CSS class match on `POETRY_CLASS_RE`, or
  `_text_looks_like_poetry()` (≥2 `<br>`-rich blocks of <1600 chars each,
  or ≥6 short non-dot-ending lines in first 200 paragraphs)
- **Drama detection**: any heading matching `CAST_HEADINGS`
- **Image detection**: any `<img>` tag

### Heuristic Classification (`heuristic_classify_doc`)

**Priority order** (first match wins):

| # | Check | → kind | Condition |
|---|---|---|---|
| 1 | `_looks_like_source_contents_document()` | `local_toc` / remove | Heading matches principal contents / series contents / illustrations / TOC, and text < 12K chars |
| 2 | `_looks_like_delphi_books_apparatus()` | `promo` / remove | Heading = "The Books", index ≤ 20, length < 1800, has images or keywords |
| 3 | `_looks_like_publisher_apparatus()` | `promo` / remove | Heading = catalog/catalogue, or ≥2 strong hits from ~15 patterns on combined text, length < 20K |
| 4 | `_looks_like_gutenberg_header_wrapper()` | `promo` / remove | Contains "The Project Gutenberg eBook of" + "START OF THE PROJECT GUTENBERG EBOOK", length < 5K |
| 5 | PG license | `promo` / remove | "full project gutenberg license" + length < 8K; or longer doc where ALL headings are Gutenberg |
| 6 | Length < 60, no images | `unknown` | — |
| 7 | `PROMO_PATTERNS` match + length < 3500 | `promo` / remove | Confidence 0.78 |
| 8 | `LOCAL_TOC_HEADINGS` match + < 800 words | `local_toc` / remove | Confidence 0.80 |
| 9 | `FRONTMATTER_PATTERNS` match | `frontmatter` | — |
| 10 | `BACKMATTER_PATTERNS` match | `backmatter` | — |
| 11 | `COLLECTION_DIVISIONS` match | `division` | — |
| 12 | `CAST_HEADINGS` match or `contains_drama` | `play` | — |
| 13 | `contains_poetry` | `poetry` | — |
| 14 | H1 with non-chapter heading < 100 chars | `major_work` | — |
| 15 | Early doc with intro/preface keywords | `frontmatter` | Index < 5 |
| 16 | Fallback | `chapter` | — |

**Post-classification**:
- `_mark_early_orphan_frontmatter_media()` — removes short (<350 chars) image/caption
  fragments after "The Books" apparatus pages.
- `_demote_late_frontmatter_like_sections()` — turns `frontmatter` → `chapter`
  if a real work has already been seen.

### AI Classification Override
When `--use-openai` is set, `apply_openai_book_plan()` sends batches of up to 24
documents (headings, text samples, flags) to the AI provider with a JSON schema
(`SECTION_SCHEMA`). AI results override heuristic classification when confidence ≥ 0.86.

### Plugin Classification
`run_plugin_classifiers()` — calls any registered plugin classifier functions.

---

## 7. Document Cleaning Pipeline — `pipeline/_cleaners.py`

Each spine document passes through **~20 sequential cleaners** in `clean_document()`.
Order matters — earlier cleaners fix problems later ones depend on.

### Stage A: Attribute & Tag Cleanup
1. **`strip_bad_attributes()`** — whitelist: `href,src,alt,title,id,class,colspan,rowspan,width,height`.
   Keeps class only if matching `poem|poetry|verse|stanza|cast|character|...`.
2. **`unwrap_useless_inline_tags()`** — unwraps `<font>` and empty attribute-less `<span>`.

### Stage B: Promotional Content Removal
3. **`remove_promotional_blocks()`** — three passes:
   - PG header block (`id=pg-header` + "project gutenberg" in text)
   - PG start/end markers (`\*+\s*START/END OF THE PROJECT GUTENBERG EBOOK\b.*\*+`)
   - Block-level promo (sections/divs/asides/navs matching `PROMO_PATTERNS` +
     structural clues: promo class, strong phrases, links, length < 2800)
   - Inline promo (p/li matching same, length < 360)

### Stage C: Source Apparatus Removal
4. **`remove_leading_source_apparatus_until_body_start()`** — iterates leading
   children of `<body>`, removes source-production nodes (`SOURCE_PRODUCTION_RE`),
   source-contents nodes (`_is_source_contents_node`), title/imprint nodes
   (`_is_title_or_imprint_node`), and empty nodes. Stops at first real body-start node
   (chapter heading or substantial prose ≥12 words).

   **`SOURCE_PRODUCTION_RE`**: matches `transcriber's notes`, `produced by`,
   `e-text prepared by`, `project gutenberg`, `typographical errors`, etc.

   **`SOURCE_IMPRINT_RE`**: matches `translated from/by`, `copyright`,
   `all rights reserved`, `publisher/london/new york/boston`, `by Count/Graf Leo`, etc.

### Stage D: Source Contents / Mini-TOC Removal
5. **`remove_source_contents_apparatus()`** — if first heading in body matches
   `SOURCE_CONTENTS_HEADINGS_RE` (`contents`, `table of contents`, `principal contents`,
   `series contents`, `list of illustrations`, etc.), removes entire body content.

6. **`remove_local_mini_tocs()`** — two passes:
   - Pass 1: heading + `<table>/<nav>/<ol>/<ul>` follower with roman-numeral links
     (≥3 links, ≥50% roman) or plain contents table (numbered lines 65%+ short).
   - Pass 2: heading + any follower with ≥2 links or compact text.

7. **`remove_compact_local_contents_blocks()`** — detects nav/ol/ul/div blocks
   that look like compact TOCs (≥3 links, or ≥6 lines with ≥70% short + ≥4 nav lines).

### Stage E: Split Source Title Page Removal
8. **`remove_split_source_title_page_before_body()`** — detects title+imprint
   fragments split into tiny headings before real text. Requires:
   - ≥4 significant children
   - ≥3 tiny headings (≤8 words)
   - Title-word overlap with book title (≥2 words matching)
   - Imprint signal from `SOURCE_IMPRINT_RE`
   - A boundary heading (`FRONTMATTER_PATTERNS` match or titled section)

### Stage F: Image Handling
9. **`rewrite_images()`** — per-image processing:
   - Clears generic alt text (matching `GENERIC_IMAGE_FILENAME_RE`)
   - Resolves src through `src_map` (EPUB → build dir)
   - Calls `should_keep_image()` for keep/remove decision
   - Removes rejected images and their parent containers if short (<700 chars)

10. **`should_keep_image()`** — decision logic:
    - `image_policy = "keep-all"` → keep
    - `image_policy = "remove-all"` → remove
    - **Decorative/placeholder detection**: generic filename + standalone + small
      dims (width ≤ 280, height ≤ 140) → remove
    - **Functional clues** (`FUNCTIONAL_IMAGE_CLUES` or `FUNCTIONAL_IMAGE_SRC_CLUES`) → keep
    - **Plate clues** (`PLATE_CAPTION_PATTERNS` or `PUBLISHER_IMAGE_SRC_CLUES`)
      without functional clues → remove
    - **AI fallback**: if AI client configured, ask AI; if confidence ≥ 0.70, use AI decision
    - **Zero-context orphan**: no alt, no caption, no surrounding text, alone in parent → remove

11. **`remove_orphan_image_captions()`** — removes caption-like text orphaned
    after image removal. Detects: position captions (`(left/right/above/below)`),
    plate caption keywords, short caption shape (≤14 words, no sentence punctuation,
    no dialogue signals).

### Stage G: Empty Layout Shell Removal
12. **`remove_empty_layout_shells()`** — removes empty `<pre>`/`<code>`,
    `<svg>` without linked images, empty `<figure>`/`<div>`/`<section>`,
    and shells containing only plate caption text.

### Stage H: Duplicate Title Line Removal
13. **`remove_duplicate_current_work_title_line()`** — removes a first-child
    paragraph whose normalized text matches the current work title (≤10 words).

### Stage I: Poetry / Verse Normalization
14. **`normalize_preformatted_verse()`** — converts `<pre>` blocks into
    `<div class="verse-block">` with `<span class="verse-line">` children.
    Guards against code (searches for `def|class|function|var|let|const|import|#include`).
    Merges adjacent converted blocks.

15. **`normalize_poetry()`** — three sub-steps:
    - Converts `<p>`/`<div>` with ≥2 `<br>` and text < 2200 chars into verse blocks
    - Adds `verse-block` class to tags matching `POETRY_CLASS_RE` CSS class
    - Detects runs of ≥4 short (2-76 chars) non-dot-ending paragraphs as poetry sequences

### Stage J: Cast / Drama Normalization
16. **`normalize_cast_and_drama()`**:
    - Marks `CAST_HEADINGS`-matching headings as `cast-heading` + `formal-opener`
    - Marks following siblings as `cast-list` (until next heading)
    - Detects entry lines: `^[A-Z][A-Z .'-]{2,35}([—–-]|,|\s{2,})` or all-caps → `cast-entry`
    - Marks `ACT_SCENE_HEADINGS`-matching headings as `act-scene-heading`
    - Marks `[...]`/`(...)` paragraphs with length < 240 as `stage-direction`

### Stage K: Footnote Normalization
17. **`normalize_notes_refs()`** — converts footnote links (`<a href="#note1">[1]</a>`)
    to `<sup class="note-ref">1</sup>`.

18. **`normalize_inline_footnotes()`** — detects consecutive `[1]`/`1.` numbered
    paragraphs, wraps them in `<section class="footnotes">`. Uses:
    - `bracketed_note_re`: `^\[(\d+)\](?:\.)?\s+`
    - `bare_note_re`: `^(\d+)[.)]\s+` (only when surrounding HTML has note semantics)
    - Groups consecutive candidates into clusters (≥2), wraps each cluster.
    - Skips if `footnote_handling = "disabled"` or `"endnotes-only"` (for real endnote sections).

### Stage L: Typographic Cleanup
19. **`simple_typographic_cleanup()`** — conservative fixes:
    - Non-breaking space → space
    - Collapse whitespace runs
    - `...` → `…`
    - ` -- ` → ` — `
    - `word--word` → `word—word` (only when word chars adjacent)
    - Double space after period → single
    - Skips `<pre>`/`<code>`/`<kbd>`/`<samp>` parents.

### Stage M: Small Caps Normalization
20. **`normalize_small_caps()`** (if `settings.small_caps`) — detects
    all-caps short words/phrases in body text (not headings, not first words)
    and wraps them in `<span class="small-caps">`.

### Stage N: Drop Caps
21. **`normalize_drop_caps()`** (if `settings.drop_caps`) — first paragraph
    after major headings gets a floated initial letter (`<span class="drop-cap">`).
    Wraps paragraph in `<div class="drop-cap-paragraph">`.

### Stage O: Heading Promotion
22. **`promote_paragraph_headings()`** — promotes disguised headings in
    `<p>`/`<div>` tags to proper `<h2>` or `<p class="chapter-opener-title">`.
    Three detection modes:
    - **Chapter headings**: `CHAPTER_HEADINGS` match → `<h2 class="subdivision">`
      (with `part-heading` if starts with "part")
    - **Section labels**: uppercase, ≤5 words, follows a heading, not dialogue
      → `<h2 class="chapter-section-heading">`
    - **Chapter opener titles**: follows a chapter heading, title-like (2-14 words,
      not dialogue, title-cased) → `<p class="chapter-opener-title">`

### Stage P: Chapter Opener Grouping
23. **`group_chapter_opener_titles()`** — wraps chapter heading + following title
    line + first body paragraph in `<div class="chapter-opener-block">` to prevent
    stranded opener pages.

### Stage Q: Running-Head / Work State Management
24. **`add_set_current_work_marker()`** — inserts `<span class="set-current-work">`
    markers that the CSS `string-set` property uses for running heads.
25. **`add_set_collection_marker()`** — `<span class="set-collection">` for verso
    running head (collection title).

### Stage R: Heading Classification (`normalize_headings`)
This is the central heading classifier. Each heading tag gets a CSS class
based on its text content and the document's `kind`:

| Condition | CSS Class | TOC Entry | Effect |
|---|---|---|---|
| `COLLECTION_DIVISIONS` match | `collection-division formal-opener` | level 1, kind=division | Standalone opener page |
| `BACKMATTER_PATTERNS` match | `backmatter-opener formal-opener` | level 1-2, kind=backmatter | Page break, no blank page |
| Attached notes in collection | `note-section-opener structural-backmatter-opener` | level 2-3, kind=notes | Smaller heading |
| H1, non-chapter, non-frontmatter | `major-work formal-opener` | level 1, kind=work | Standalone opener page |
| Collection section heading | `subdivision story-work-opener` | level 2-3, kind=frontmatter | Page break, body page |
| `FRONTMATTER_PATTERNS` match | `frontmatter-opener formal-opener` | level 1-2, kind=frontmatter | Roman-folio frontmatter |
| Inline story opener (h2) | `subdivision story-work-opener` | level 2-3, kind=work | Embedded work start |
| Chapter-ish, level 2 | `subdivision` | level 2-3, kind=chapter | Body page, avoid breaks |
| Chapter-section heading | `chapter-section-heading` | — | Minor section label |
| Level 2, other | `subdivision` | level 2-3, kind=work | Subdivision |
| Level 3-6, other | `minor-heading` | — | Italic minor heading |
| Starts with "part" | + `part-heading` | — | Full-page break |

Each heading gets a unique `id` via `unique_id()`. Source-contents headings are
removed entirely.

### Stage S: Synthetic Openers
26. **`add_synthetic_opener_if_needed()`** — inserts `<h1 class="major-work">`
    when AI-assigned `major_title` is not already present in the HTML.
27. **`ensure_frontmatter_opener()`** — inserts `<h2 class="frontmatter-opener">`
    for frontmatter documents missing a heading.

### Stage T: Work Descriptions
28. **`mark_major_work_descriptions()`** — styles editorial blurbs after
    major-work/openers as `.work-description` (italic, smaller font).
    Detection (`_looks_like_work_description`):
    - 18-260 words
    - Editorial vocabulary (`published`, `written`, `novel`, `story`, `translated`, etc.)
      + title mention OR bibliographic marker (year, author name)
    - Not authorial notes, not starting with quote/dash, not chapter headings
    - Continuation paragraphs detected via `_looks_like_work_description_continuation()`
    - Boundary: `WORK_DESCRIPTION_BOUNDARY_RE` (chapter/book/part/act/scene + numeral)
    - Authorial notes (`_looks_like_authorial_note`) → `.author-note`
    - Subtitles (≤8 words) → `.work-subtitle`

29. **`mark_standalone_work_description_fragment()`** — catches short editorial
    notes in separate EPUB files (≤4 paragraphs, no h1/h2/img/table).

30. **`mark_standalone_prefatory_apparatus_fragment()`** — catches split source
    title/imprint pages (3-12 paragraphs, first line ≥62% uppercase, apparatus signal).

### Stage U: Source Opener Suppression
31. **`suppress_redundant_standalone_source_opener()`** — removes heading matching
    book title if it's the first element (duplicate of generated title page).
    Also removes following "by Author" lines, blank separators, and subtitle elements.

### Stage V: Embedded Work Promoter
32. **`promote_embedded_standalone_work_opener()`** — promotes in-flow headings
    matching book title to `major-work` when preceded by intro text and followed
    by work-opening signals (`by Author`, chapter headings, italic runs).

### Stage W: Opener Separators
33. **`insert_major_opener_separators()`** — inserts `.true-blank` divs (no-folio
    blank pages) before/after major-work and collection-division headings.

### Stage X: Demotions
34. **`demote_inline_story_openers()`** — converts `major-work` → `story-work-opener`
    when followed by roman numeral + prose (sequential stories in a collection).
35. **`demote_structural_backmatter_openers()`** — converts appendix headings
    to `backmatter-opener` (prevents standalone blank pages).
36. **`demote_embedded_frontmatter_after_body()`** — ensures frontmatter-like
    headings after the body start don't get frontmatter treatment.

### Stage Y: Epigraphs
37. **`mark_epigraphs()`** — marks italic chapter-opening quotations as
    `.epigraph`, attribution lines as `.epigraph-attribution`.

### Stage Z: Runner Logic
38. **`runner_display_text()`** — determines running-head text:
    - If collection-like volume + structural heading → empty (no runner)
    - If meaningful work title (not structural, not duplicate of book title) → use it
    - Fallback to author → book title
39. **`shorten_runner_title()`** — truncates to 42 chars:
    - Strips subtitles after `—`, `:`, `;`
    - Strips treatise prefixes (`On the`, `A Treatise of`, `The Book of`, etc.)
    - Strips "and other stories" suffix
    - Strips long parenthetical notes
    - Truncates with word boundary, removes weak trailing words

---

## 8. CSS Generation — `pipeline/_css.py`

### Page Model
| @page | Purpose | Folio | Header | Rule |
|---|---|---|---|---|
| `(default)` | First pages | none | none | none |
| `:blank` | Intentional blanks | none | none | none |
| `title` | Title/half-title/source pages | none | none | none |
| `front` | Frontmatter | Roman lowercase | none | none |
| `body` | Main body | Arabic (`bodyPage`) | Running heads | CSS or vector |
| `opener` | Major work/division openers | none | none | none |
| `nofolio` | True-blank separators | none | none | none |

### Running Head Layouts
4 layouts (`runner_layout`):
- `right_title_full_rule` — collection title @top-left, current work @top-right, full-width rule
- `centered_single_rule` — current work centered, single rule below
- `dual_full_rule` — left + right with split rules (CSS-only)
- `alternating` — verso: left only, recto: right only

Rule styles (`runner_rule_style`): `full_width` (vector-drawn), `single` (CSS `border-bottom`),
`split` (two separate CSS rules), `none`.

### Key CSS Classes → Print Behavior

| Class | Page | break-before | break-after | Notes |
|---|---|---|---|---|
| `.major-work`, `.collection-division` | `opener` | page | page | Standalone centered title page |
| `.backmatter-opener` | `body` | page | avoid | No standalone blank page |
| `.frontmatter-opener` | `front`/`body` | page | avoid | Roman folios for early frontmatter |
| `.subdivision` | `body` | — | avoid | Chapter titles |
| `.story-work-opener` | `body` | page | avoid | Embedded works in collections |
| `.part-heading` | `body` | page | avoid | Part divisions |
| `.chapter-section-heading` | `body` | — | avoid | Minor section labels |
| `.true-blank` | `nofolio` | page | page | Zero-height blank page |
| `.verse-block` | `body` | — | — | Poetry, hanging indent |
| `.cast-heading` | `opener` | page | — | Drama cast list |
| `.footnotes` | `body` | — | — | Endnote section |
| `.epigraph` | `body` | — | — | Italic, centered, no indent |
| `.drop-cap` | `body` | — | — | Floated initial letter (3em) |
| `.work-description` | `body` | — | — | Smaller italic editorial blurbs |

### Heading Widow/Orphan Rule
All `h1-h6`: `break-after: avoid-page`.
Adjacent paragraph (`h1 + p` through `h6 + p`): `break-before: avoid-page; widows: 5; orphans: 2`.
Effect: heading+text block moves to next page if <5 lines of following paragraph fit.

### TOC Building
`usable_toc_entries()` — filters:
- Duplicate titles (normalized key dedup)
- Book-title matches
- Source-TOC headings (`SOURCE_TOC_TITLE_RE`: `principal contents`, `series contents`, `list of illustrations`)
- Orphan entries (only 1 remaining)
- Unavailable target IDs
- In `simple` mode: only level-1 entries

`build_toc()` — generates `<ol class="toc-list toc-{simple/hierarchical}">`:
- Optionally with explicit page numbers and dotted leaders
- CSS classes: `.toc-level-1` through `.toc-level-4`, `.toc-kind-{kind}`
- Hierarchical: indented levels; Simple: flat, all same indent

### HTML Composition (`compose_html()`)
Assembles final document in order:
1. Half-title page (optional)
2. Title page (optional — auto-detects "Complete Works of X" pattern)
3. Source note page (optional)
4. Generated TOC page
5. Frontmatter sections (`.doc-frontmatter` → `page: front`)
6. `<main class="main">` body sections (`.body-page-reset` for counter reset)

---

## 9. PDF Rendering & Post-Processing — `pipeline/_render.py`

### Rendering
`render_pdf()` — WeasyPrint HTML → PDF (with CSS stylesheet).
`write_build()` — saves `book.html` + `style.css` to build directory.

### TOC Page Number Resolution
`resolve_toc_page_numbers()` — opens first-pass PDF with PyMuPDF, scans for
TOC link destinations, computes Arabic page numbers relative to body folio 1
(`find_first_body_page_index`). Re-renders PDF with explicit numbers if resolved.

### Vector Runner Rule Drawing
`draw_vector_runner_rules()` — only for `runner_layout=right_title_full_rule` +
`runner_rule_style=full_width`. Uses PyMuPDF to draw stroke-only vector lines
on pages with running heads. Lines are positioned at `runner_rule_y_mm` from top trim.

### Post-Processing
- `subset_pdf()` — truncates to N pages for sample mode
- `optimize_pdf()` — pikepdf compression + linearization

### Preflight QA (`preflight_pdf()`)
Runs on every rendered PDF. Checks:

| Check | Method | → stored in |
|---|---|---|
| Page size vs configured trim | Compare `page.rect` to expected | `verdict.non_a4_pages` |
| Font inventory | `doc.get_page_fonts()` per page | `verdict.fonts_seen` |
| Font embedding | Check expected family name in fonts | `verdict.font_embedding_warnings` |
| Image inventory | `doc.get_page_images()` per page | `verdict.images_seen` |
| **Line spills** | Single isolated lowercase letters | `verdict.possible_line_spills` |
| **Header clearance** | First body text Y vs runner rule Y + clearance | `verdict.possible_header_collisions` |
| **Narrow columns** | Median block width < 300pt (non-drama/TOC) | `verdict.possible_narrow_columns` |
| **Orphan pages** | <4 meaningful lines past page 10 | `verdict.possible_orphan_pages` |
| **Widows/orphans** | Short first line (widow) or last line (orphan) | `verdict.possible_widow_lines` |
| **Stranded headings** | Page ends with CHAPTER/PART/... heading | `verdict.possible_widow_lines` |
| **Image filename artifacts** | Raw `img_001.jpg` text visible | `verdict.visible_image_filename_artifacts` |
| **Work description style** | Editorial blurbs not italic/smaller | `verdict.work_description_style_warnings` |
| **Blank page artifacts** | No text but drawings/images present | `verdict.possible_blank_page_artifacts` |
| **Empty content pages** | Title-only pages past body start | `verdict.empty_content_pages` |
| **Dark/blank pages** | No text = blank; all text same color = dark | `verdict.dark_pages` |

---

## 10. AI Integration — `pipeline/_ai.py`

### Providers
- **OpenAI**: `responses.create()` with JSON schema for structured output
- **DeepSeek**: `chat.completions.create()` + `extract_json()` for output parsing
- Environment variables: `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`

### Book Structure Planning
`apply_openai_book_plan()` — sends batches of ≤24 spine documents (headings,
text samples, flags) to AI. Returns JSON matching `SECTION_SCHEMA`:
kind, remove_document, major_title, current_division, contains_poetry,
contains_drama_or_cast, confidence, notes. Overrides heuristic classification
when confidence ≥ 0.86.

### Image Classification
`ai_image_decision()` — sends image src + context (up to 3000 chars) to AI.
Returns `IMAGE_SCHEMA`: keep (bool), reason, confidence. Used when
`confidence ≥ 0.70`.

### Visual QA
`openai_visual_qa()` — renders up to `--openai-qa-pages` pages as JPEG, sends
to GPT vision with a detailed QA prompt (~20 checks across typography, headings,
runners, folios, TOC, images, poetry/drama, artifacts). Parses response for
FAIL/ISSUE keywords and categorizes findings (header collisions, justification,
chapter placement, TOC, publisher apparatus, folios, images, poetry/drama, blanks).

### Text QA
`ai_text_qa()` — extracts text from up to `--ai-qa-pages` sequential pages +
extra work-start pages via PyMuPDF, sends to AI for structural review.
AI can suggest regex rule additions (written as `.review.yaml` files for human review).

---

## 11. Auto-Fix Engine — `pipeline/_pipeline.py`

`auto_fix_settings()` reads QA verdict flags and applies safe CSS adjustments:

| QA Flag | Auto-Fix |
|---|---|
| Header collisions | Increase `runner_body_clearance_mm` by 2mm (max 14mm) |
| Line spills | Increase `line_height` by 0.02 (max 1.35) |
| Narrow columns | Increase `margin_side_mm` by 1mm (max 26mm) |
| Orphan pages | Decrease `margin_bottom_mm` by 1mm (min 16mm) |
| Body text too close to bottom | Increase `margin_bottom_mm` by 1mm (max 28mm) |
| Visual QA: justification issues | Enable `justify_prose` |
| Visual QA: header collision | Increase `runner_body_clearance_mm` by 2mm |
| AI text QA: promo residue | Increase `image_policy` strictness (log only) |

After applying fixes, re-renders up to `--max-auto-fix-passes` times.

---

## 12. Rule Pack System — `pipeline/_rule_packs.py`

**Purpose**: Extend built-in regex patterns with additional terms from reviewed YAML files.

**Flow**:
1. `rule_pack_names(settings)` — parses comma/semicolon-separated names from `settings.rule_packs`
2. Each name → `<rule_pack_dir>/<name>.yaml`
3. YAML keys match `RULE_PACK_KEYS` (e.g. `promo_patterns`, `chapter_headings`)
4. `compile_extended_pattern()` — combines existing pattern with additions via `(?:existing)|(?:addition1|addition2|...)`
5. `C.update_pattern()` — updates both module-level variable and `_PATTERN_DICT`

**AI suggestions**: `extract_review_rule_suggestions()` parses AI QA report YAML blocks;
`write_review_rule_suggestions()` writes `.review.yaml` files for human approval.

---

## 13. Plugin System — `pipeline/_plugins.py`

**Discovery**: `discover_plugins()` scans `plugins/` directory for `*.py` files
(except `__init__`). Each plugin module can define hooks via decorators:

| Decorator | Signature | Called when |
|---|---|---|
| `@register_cleaner` | `fn(soup, settings, log)` | Per-doc cleaning phase |
| `@register_classifier` | `fn(doc: SpineDoc)` | Spine item classification |
| `@register_regex_patterns` | Returns `dict[str, list[str]]` | Pipeline startup |
| `@register_qa_check` | `fn(verdict, page, page_no, settings)` | QA preflight (per page) |
| `@register_post_processor` | `fn(pdf_path, settings, log)` | After PDF render |

---

## 14. Batch System — `pipeline/_batch.py`

`run_batch()` processes every EPUB matching `--batch-glob` in `--batch DIR`.
- Each book gets a unique output name (slugified, deduplicated)
- Title source: EPUB metadata (default) or filename stem (`--batch-title-source`)
- `--skip-existing` skips books with existing output
- `--on-error`: `continue` or `stop`
- Writes a JSON batch report to `artifacts/`

---

## 15. Font Handling — `pipeline/_fonts.py`

`prepare_embedded_fonts()`:
- Copies configured font files from `font_dir` to `build_dir/fonts/`
- Generates `@font-face` CSS blocks for regular and italic variants
- Sanitizes filenames with `re.sub(r"[^A-Za-z0-9_.-]+", "_", ...)`
- Falls back gracefully if font files are missing

---

## 16. Caching — `pipeline/_cache.py`

`PipelineCache` — content-addressed, directory-backed cache using SHA-256 hashes:
- `hash_bytes()`, `hash_text()`, `hash_file()`, `hash_object()`, `hash_combined()`
- Storage: `<artifact_dir>/.pipeline_cache/<namespace>/<first-2-chars>/<full-hash>`
- Serialization: pickle for arbitrary objects, UTF-8 text, JSON
- `invalidate()` / `invalidate_all()` for cache clearing

---

## 17. Complete Regex Registry

All patterns defined in `_constants.py`:

| Variable | Pattern Summary | File Usage |
|---|---|---|
| `PROMO_PATTERNS` | `delphi classics\|also available\|subscribe\|newsletter\|kindle\|ebook\|isbn\|www\.\|https?://\|goodreads\|...` | `_classify.py`, `_cleaners.py` |
| `BACKMATTER_PATTERNS` | `^(notes\|endnotes\|appendix\|bibliography\|glossary\|index\|biography\|chronology\|...)$` | `_classify.py`, `_cleaners.py` |
| `FRONTMATTER_PATTERNS` | `^(preface\|foreword\|introduction\|prologue\|author'?s note\|...)$` | `_classify.py`, `_cleaners.py` |
| `LOCAL_TOC_HEADINGS` | `^(contents\|table of contents\|chapter list\|illustrations\|...)$` | `_classify.py`, `_cleaners.py` |
| `COLLECTION_DIVISIONS` | `^(the )?(novels\|short stories\|plays\|poems\|essays\|biography\|...)$` | `_classify.py`, `_cleaners.py` |
| `MAJOR_WORK_HINTS` | `^(book\|part\|volume)\s+[ivxlcdm0-9]+$` or `^(novel\|play\|poem\|...)\b` | `_classify.py` |
| `CHAPTER_HEADINGS` | `^(chapter\|part\|scene\|act\|section\|book\|canto\|letter\|...)\b` or bare roman/digits | `_classify.py`, `_cleaners.py` |
| `CAST_HEADINGS` | `^(dramatis personae\|characters\|cast of characters\|...)$` | `_classify.py`, `_cleaners.py` |
| `ACT_SCENE_HEADINGS` | `^(act\|scene)\b` | `_cleaners.py` |
| `PLATE_CAPTION_PATTERNS` | `delphi classics\|frontispiece\|portrait of\|author's birthplace/grave/...\|painted by\|translated by\|...` | `_cleaners.py` |
| `FUNCTIONAL_IMAGE_CLUES` | `map\|diagram\|chart\|table\|figure\|rune\|inscription\|manuscript\|drawing\|plan\|musical notation\|score\|...` | `_cleaners.py` |
| `FUNCTIONAL_IMAGE_SRC_CLUES` | `(map\|diagram\|chart\|figure\|...\|genealogy)` in filename | `_cleaners.py` |
| `PUBLISHER_IMAGE_SRC_CLUES` | `(cover\|frontispiece\|portrait\|photo\|author\|birthplace\|...\|delphi\|logo)` in filename | `_cleaners.py` |
| `LOCAL_CONTENTS_LINE_RE` | `^(contents\|chapter\|letter\|act\|book\|roman\|digit\|date)\b` | `_cleaners.py` |
| `POETRY_CLASS_RE` | `poem\|poetry\|stanza\|verse\|line\|canto\|song\|epigram\|sonnet\|ode` | `_classify.py`, `_cleaners.py` |
| `ROMAN_RE` | `^[ivxlcdm]+$` | `_constants.py`, `_render.py` |

Plus local regexes in `_cleaners.py`:
- `SOURCE_PRODUCTION_RE` — PG transcriber/producer/header markers
- `SOURCE_IMPRINT_RE` — publisher/translation/copyright lines
- `SOURCE_CONTENTS_HEADINGS_RE` — contents/illustrations headings
- `GENERIC_IMAGE_FILENAME_RE` — `^(?:img\|image\|pic\|fig\|figure)[_\- ]?\d+\.(?:jpe?g\|png\|gif\|webp)$`
- `STRUCTURAL_RUNNER_LABEL_RE` — `^(?:chapter\|part\|book\|volume\|section\|act\|scene)\s+(numeral)`
- `ATTACHED_NOTE_HEADING_RE` — `^(?:notes\|endnotes\|footnotes)\.?$`
- `WORK_DESCRIPTION_BOUNDARY_RE` — marks end of editorial description runs
- `APPENDIX_HEADING_RE` — `^(?:appendices\|appendix(?:\s+[ivxlcdm0-9]+)?\.?)`

---

## 18. Quick Module Index

| File | Key Responsibility |
|---|---|
| `_cli.py` | Argument parsing, ~50 CLI flags |
| `_config.py` | YAML/JSON config loading, 3-layer override |
| `_models.py` | `Settings`, `SpineDoc`, `TocEntry`, `BuildLog`, `QAVerdict`, JSON schemas |
| `_constants.py` | All 16 compiled regex patterns, dimensional constants, rule-pack key map |
| `_classify.py` | EPUB reading, title/author inference, text probing, 16-step heuristic + AI classification |
| `_cleaners.py` | ~40 sequential HTML cleaners (promo removal, TOC removal, poetry/drama/cast, footnotes, heading classification, work descriptions, epigraphs, drop caps, small caps, typography) |
| `_css.py` | CSS generation (8 @page rules, ~300 CSS rules), TOC building, HTML composition |
| `_render.py` | PDF rendering (WeasyPrint), vector rule drawing, TOC page resolution, 15+ QA preflight checks |
| `_pipeline.py` | DAG stage definitions, per-doc cleaning loop, auto-fix engine, build orchestrator |
| `_dag.py` | DAG executor with topological sort, caching, progress reporting |
| `_ai.py` | OpenAI/DeepSeek integration for structure planning, image classification, visual/text QA |
| `_rule_packs.py` | YAML rule pack loader, pattern compiler, AI suggestion extractor |
| `_plugins.py` | Plugin discovery, 5 hook types (cleaner, classifier, regex, QA, post-processor) |
| `_fonts.py` | Font file copy + `@font-face` CSS generation |
| `_cache.py` | Content-addressed SHA-256 caching layer |
| `_batch.py` | Folder-based batch EPUB conversion |
| `_utils.py` | `clean_text`, `normalized_title_key`, `visible_word_count`, `slugify`, HTML parsing helpers |
