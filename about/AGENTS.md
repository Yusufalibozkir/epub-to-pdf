# AGENTS.md — Deluxe EPUB to Print PDF Pipeline

## Project overview

Converts EPUBs into A4 print-oriented PDFs for deluxe physical book interiors. See [README.md](README.md) for full feature list. See [USER_GUIDE.md](USER_GUIDE.md) for usage and [MANUAL_QA_CHECKLIST.md](MANUAL_QA_CHECKLIST.md) for QA procedures.

## Quick commands

```powershell
# Install (Windows)
.\install_windows.ps1

# Generate default config
python deluxe_epub_to_pdf.py --write-default-config my_style.yaml

# Sample run (50 pages)
python deluxe_epub_to_pdf.py "book.epub" --out "sample.pdf" --sample-pages 50

# Full build with config + AI + strict mode
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "full.pdf" --full-without-sample --use-openai --openai-qa --strict

# DeepSeek text QA
python deluxe_epub_to_pdf.py "book.epub" --ai-provider deepseek --use-openai --out "sample.pdf" --sample-pages 50 --debug-html

# Clean outputs
Remove-Item -Recurse -Force output, artifacts
```

See [CLI_OPTIONS_REFERENCE.txt](CLI_OPTIONS_REFERENCE.txt) for all ~60 CLI flags.

## Architecture

```
pipeline/
├── __main__.py      # python -m pipeline entry point
├── _models.py       # All dataclasses (Settings ~70 fields, SpineDoc, TocEntry, BuildLog, QAVerdict)
├── _config.py       # YAML/JSON config loading, CLI override merging
├── _cli.py          # Argument parsing (~60 flags)
├── _pipeline.py     # Orchestrator: DAG definition, stage runners, auto-fix loop, main entry
├── _dag.py          # Lightweight DAG executor (Kahn's algorithm, cache-aware)
├── _cache.py        # Content-addressed caching (SHA-256, pickle/JSON/text)
├── _cleaners.py     # ~20 HTML cleanup passes (promo removal, poetry/drama detection, etc.)
├── _classify.py     # EPUB scanning + heuristic spine-doc classification
├── _css.py          # CSS generation (~500 lines template) + HTML assembly
├── _render.py       # WeasyPrint render, pikepdf optimize, PyMuPDF QA preflight, vector drawing
├── _ai.py           # OpenAI/DeepSeek integration (structure, images, visual QA, text QA)
├── _plugins.py      # Plugin discovery + 5 hook types
├── _rule_packs.py   # YAML rule-pack loading, regex compilation/validation
├── _fonts.py        # Font embedding (@font-face CSS generation)
├── _constants.py    # 13 compiled regex patterns, dimension constants, runtime pattern mutation
└── _utils.py        # Text cleanup, slugging, HTML parsing utilities
```

### Execution model: DAG with content-addressed caching

Every processing phase is a `Stage(name, depends_on, runner, cache_key)`. The DAG executor (`_dag.py`) topologically sorts stages and skips any whose SHA-256 cache key matches cached results. Stages execute in this order:

```
resolve_title → read_assets → scan_classify → ai_plan (optional)
                                                    ↓
                                          prepare_fonts → clean_documents
                                                              ↓
                                                        compose_render
                                                              ↓
                                                        resolve_toc (2nd pass)
                                                              ↓
                                                        post_process (vector rules, subset)
                                                              ↓
                                                        run_qa → ai_text_qa (opt) → ai_visual_qa (opt)
```

### Three-layer configuration override

```
built-in defaults (Settings dataclass) → YAML/JSON config → CLI flags
```

All configuration flows through the `Settings` dataclass. Never add magic constants — put them in `Settings` with a sensible default.

## Conventions

### Imports
- Every module uses `from __future__ import annotations` (PEP 563 deferred evaluation).
- Follow this pattern for new modules.
- All package imports use the `pipeline.` prefix: `from pipeline._models import Settings`.

### Naming
- **Modules**: underscore-prefixed private modules in `pipeline/` (e.g., `_css.py`).
- **Functions**: `snake_case` throughout.
- **Classes**: `PascalCase`.
- **Regex constants**: `UPPER_SNAKE_CASE` in `_constants.py`.
- Dataclasses are preferred over plain dicts for structured data.

### Logging
- Always use `BuildLog.warn()`, `BuildLog.fail()`, `BuildLog.removed()` — never `print()` for diagnostics.
- The `BuildLog` instance is passed through the `PipelineContext` to every stage.

### Regex patterns are mutable at runtime
The 13 built-in patterns in `_constants.py` can be extended by rule packs or plugins at runtime. Use `_constants.update_pattern()` / `_constants.get_pattern()` — never hard-code pattern alternatives. The registry `_PATTERN_DICT` maps YAML key names to compiled regex objects. See [README.md#rule-pack-system](README.md) and `_rule_packs.py`.

### CSS generation
The entire CSS stylesheet is generated programmatically from `Settings` in `css_text()` (`_css.py`). When adding new layout features, add settings fields to `Settings` first, then reference them in the CSS template.

### QA is integrated, not optional
`_render.py:preflight_pdf()` runs PyMuPDF analysis on every rendered page: A4 compliance, font inventory, dark pages, blank-page artifacts, line-spill heuristics, header/body clearance, narrow-column detection, work description style checks, widow/orphan detection, stranded heading detection. Always verify QA passes after CSS or layout changes.

### Auto-fix is conservative
Only safe numeric/bool settings are auto-tuned (clearance, margins, font sizes). Semantic issues (image choice, poetry classification, work descriptions) are flagged for human review, never guessed. See `_pipeline.py:auto_fix_settings()`.

## Plugin system

Five hook types, registered by calling functions in a `plugins/*.py` file:

| Hook | Register with | Signature |
|------|--------------|-----------|
| Cleaner | `register_cleaner(fn)` | `fn(soup, settings, log)` |
| Classifier | `register_classifier(fn)` | `fn(doc: SpineDoc)` |
| Regex patterns | `register_regex_patterns(dict)` | `dict[str, list[str]]` |
| QA check | `register_qa_check(fn)` | `fn(verdict, page, page_no, settings)` |
| Post-processor | `register_post_processor(fn)` | `fn(pdf_path, settings, log)` |

Plugins are auto-discovered from `plugins/*.py` (excludes `_`-prefixed and `__init__`). See [README.md#plugin-system](README.md) and `_plugins.py`.

## DeepSeek QA → rule packs workflow

1. DeepSeek text QA generates `deepseek_rule_suggestions.review.yaml` — **NEVER auto-loaded** (safety gate — AI can suggest patterns that delete real literature).
2. Human reviews the suggestions.
3. Approved patterns are manually copied into `rules/generic_epub.yaml` or registered via a plugin calling `register_regex_patterns()`.
4. See `/memories/repo/plugin-system.md` for details.

## Font embedding

The pipeline embeds EB Garamond from `fonts/` by default. If you need different fonts, update `Settings.embedded_font_family`, `embedded_font_regular`, `embedded_font_italic`, and `embedded_font_weight`. Font files are copied to the build directory and referenced via `@font-face` in CSS. Never assume system fonts are available — always embed.

## Heading widow/orphan CSS rule

All `h1-h6` get `break-after: avoid-page`. `h1 + p` through `h6 + p` get `break-before: avoid-page; widows: 5; orphans: 2`. Combined effect: WeasyPrint pushes heading+text to next page when fewer than 5 body lines fit below the heading on the current page. When modifying heading CSS in `_css.py`, preserve these rules. See `/memories/heading-widow-rule.md`.

## Key dependencies

- **WeasyPrint** — HTML+CSS → PDF (CSS Paged Media, footnotes, running heads)
- **PyMuPDF (fitz)** — PDF inspection, QA preflight, vector drawing (post-render runner rules)
- **pikepdf** — PDF optimization (compression, linearization)
- **BeautifulSoup4 + lxml** — HTML parsing/cleanup
- **EbookLib** — EPUB reading
- **openai** — OpenAI API (also used for DeepSeek via `base_url`)
- **PyYAML** — Config and rule-pack parsing
- **Pillow** — Image analysis for dark-page detection

See [requirements.txt](requirements.txt) for exact versions.

## There is no test suite

QA is performed by the built-in `preflight_pdf()` checks and manual review of `qa_report.txt`, `qa_verdict.json`, and rendered page images in `qa/`. Always run a full build and check QA output before declaring a change complete. See [MANUAL_QA_CHECKLIST.md](MANUAL_QA_CHECKLIST.md).

## Git workflow note

The repo includes `artifacts/` directories with build outputs and QA reports. These are intentional — they provide reference outputs for comparison. Don't delete them unless explicitly asked.
