# Local Deluxe EPUB to Print PDF Pipeline

This project converts an EPUB into a single-page A4 print-oriented PDF for a deluxe physical book interior. It is designed for large public-domain collected works, not quick ebook export.

It is implemented as a modular Python package (`pipeline/`) with a DAG-based execution engine, content-addressed caching, and a plugin system.

**Key features:**

- A4 single-page PDF output
- Half title, title page, source page, and generated table of contents
- Roman front-matter pagination and Arabic main pagination
- Recto starts for title/TOC/main/major works where CSS paged media supports them
- True `@page :blank` suppression of runners/folios on generated blank pages
- Separate `@page body:left` and `@page body:right` running-head logic
- Collection title on verso, current major work/division on recto
- Conservative promotional/local mini-TOC removal
- Image/caption classification with functional-image preservation
- Poetry block conversion from `<br>` lineation and short-line sequences
- Verse hanging indents for runover lines
- Drama/cast-list detection and styling
- Delphi-style collected-works apparatus styling: subtitles stay roman, editorial descriptions become smaller italic text
- Compact in-work heading stacks such as `PART I` / `CHAPTER I` / `INTRODUCTION`
- Typographic cleanup: non-breaking-space cleanup, ellipses, dashes, double spaces
- Note-reference superscripting
- PDF optimization with pikepdf
- PyMuPDF preflight: page size, font inventory, dark pages, blank-page artifacts, line-spill heuristics, header/body clearance, narrow-column detection, work description style checks
- Vector runner-rule drawing (post-render)
- Second-pass TOC page-number resolution
- Auto-fix system: deterministic CSS/config adjustments with re-render passes
- Optional AI assistance (OpenAI **or** DeepSeek): structure planning, image classification, visual QA, and text QA with regex rule-suggestion generation
- **Plugin system**: Python-based extension hooks for custom cleaners, classifiers, QA checks, and post-processors
- **Rule-pack system**: YAML files extending the 13 built-in regex patterns
- **DAG-based execution**: cached, dependency-ordered processing stages
- **Content-addressed caching**: SHA-256 keyed intermediate results avoid redundant work
- `--strict` mode that fails if delivery-blocking QA warnings remain
- Machine-readable `qa_verdict.json` and `build_summary.json` in per-run artifact folders

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

WeasyPrint may require system dependencies depending on your OS. Install WeasyPrint according to its official platform instructions if `pip install` alone is not enough.

## Package structure

The project is implemented as the `pipeline/` Python package plus a thin backward-compatible wrapper script:

| Path | Purpose |
|---|---|
| `pipeline/` | Modular package — CLI, config, DAG executor, cache, cleaners, CSS, render, QA, AI, plugins, rule packs |
| `deluxe_epub_to_pdf.py` | Thin wrapper around `pipeline._cli` / `pipeline._pipeline` (original single-script entry point) |
| `rules/` | YAML rule-pack files extending built-in regex patterns |
| `plugins/` _(optional)_ | Python plugin modules for custom cleaners, classifiers, QA checks, etc. |

Run via the wrapper **or** directly as a package:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "sample.pdf" --sample-pages 50
python -m pipeline "book.epub" --out "sample.pdf" --sample-pages 50
```

## DAG-based execution & caching

The pipeline uses a directed-acyclic-graph (DAG) executor with per-stage cache keys (SHA-256 content-addressed). Stages that detect no input changes are served from cache, making iterative config tweaks much faster.

## Plugin system

Python modules placed in a `plugins/` directory are auto-discovered. Each module can register hooks:

- `register_cleaners()` — custom HTML cleaners
- `register_classifiers()` — custom spine-doc classifiers
- `register_regex_patterns()` — extend built-in regex patterns
- `register_qa_checks()` — custom PDF QA checks
- `register_post_processors()` — custom PDF post-processing

## Rule-pack system

YAML files in `rules/` (configured via `rule_packs` setting) extend the 13 built-in regex patterns at runtime — no Python code required.

## AI providers: OpenAI & DeepSeek

The pipeline supports two AI providers:

| Provider | Use cases |
|---|---|
| **OpenAI** (default) | Structure planning, image classification, visual QA |
| **DeepSeek** | Structure planning, text QA with regex rule-suggestion generation |

Set the provider with `--ai-provider deepseek` and configure `$env:DEEPSEEK_API_KEY`.

## Recommended workflow

Start with a 50-page sample:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "sample.pdf" --sample-pages 50 --use-openai --openai-qa --debug-html
```

Review:

- `output/sample.pdf`
- `artifacts/sample/qa_report.txt`
- `artifacts/sample/qa_verdict.json`
- `artifacts/sample/openai_visual_qa.txt` or `deepseek_text_qa.txt` if used
- rendered page images in `artifacts/sample/qa/`
- generated HTML/CSS in `artifacts/sample/build/` when `--debug-html` is used

Generated files are intentionally grouped so cleanup is simple:

```powershell
Remove-Item -Recurse -Force output, artifacts
```

Then build the full PDF:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "full_print.pdf" --full-without-sample --use-openai --openai-qa --strict
```


## Configuration file

The pipeline supports YAML or JSON config files. Missing keys keep the built-in defaults. Override order:

```text
built-in defaults → config file → explicit command-line flags
```

Generate a complete editable config:

```powershell
python deluxe_epub_to_pdf.py --write-default-config my_style.yaml
```

Use it:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50
```

With a bare `--out` filename, the PDF goes to `output/` and run diagnostics go to `artifacts/<pdf-name>/`. Use `--output-dir` and `--artifacts-dir` to change those folders.

Book titles are read from EPUB metadata automatically. Use `--title "Clean Title"` only when the EPUB metadata is wrong or you want a deliberate display override.

Quick command-line overrides still work and take priority over the config:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --body-size 12 --line-height 1.28 --sample-pages 50
```

Common config keys include `body_size_pt`, `line_height`, `font_stack`, `margin_top_mm`, `margin_side_mm`, `margin_bottom_mm`, `runner_font_pt`, `folio_font_pt`, `runner_rule_y_mm`, `runner_title_top_mm`, `runner_body_clearance_mm`, `paragraph_indent_em`, `work_description_font_delta_pt`, `work_description_bottom_margin_mm`, `part_heading_font_pt`, `chapter_section_font_pt`, `verse_line_height`, `verse_max_width_mm`, `verse_font_size_delta_pt`, and `image_policy`. See `deluxe_config.example.yaml` for the full list.

The default typography is EB Garamond. The project embeds local font files from `fonts/` by default, controlled by `embed_font_files`, `font_dir`, `embedded_font_family`, `embedded_font_regular`, `embedded_font_italic`, and `embedded_font_weight`. This prevents WeasyPrint from silently falling back to Times New Roman or a system Garamond when EB Garamond is not installed.

## OpenAI API integration

Set the key in PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-your-key"
```

Useful modes:

```powershell
--use-openai
```

Uses a whole-book structure plan to classify spine documents as front matter, division, major work, chapter, poetry, play, back matter, promo, or local TOC.

```powershell
--openai-image-check
```

Asks OpenAI whether each image/caption block should be preserved or removed. This can cost more on image-heavy EPUBs.

```powershell
--openai-qa
```

Renders selected PDF pages as images and asks OpenAI for visual QA comments.

When OpenAI visual QA flags safe, layout-tunable problems, the script can adjust settings and rerender automatically. This is capped by `--max-auto-fix-passes` defaulting to `1`; each additional pass may rerun OpenAI QA. Safe automatic fixes include runner/body clearance, body justification/line-height, chapter-title spacing, and TOC spacing. Semantic issues such as image choice or poetry/drama classification are reported for review instead of guessed.

DeepSeek can be used for text/structure QA after local QA:

```powershell
$env:DEEPSEEK_API_KEY="sk-your-deepseek-key"
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --ai-provider deepseek --out "sample.pdf" --sample-pages 50 --debug-html
```

With `--ai-provider deepseek`, the script runs the normal local QA first, then writes `deepseek_text_qa.txt`. Safe text-QA findings can trigger the same bounded rerender loop controlled by `--max-auto-fix-passes`, for example body line-height, justification, chapter-title spacing, TOC spacing, or runner clearance. If DeepSeek suggests regex cleanup rules, they are written to `deepseek_rule_suggestions.review.yaml` for human review; they are not loaded automatically.

Local QA also checks rendered PDF font names and point sizes for detected editorial work descriptions. DeepSeek can review those local warnings and extracted text patterns, but it cannot directly see italics unless local QA reports the style problem.

Reviewed regex rule packs live in `rules/` and are configured with `rule_pack_dir` and `rule_packs`. The default `rules/generic_epub.yaml` extends the built-in conservative cleanup regexes.

## Cost-control advice

For large books, first run:

```powershell
--use-openai --openai-qa --sample-pages 50
```

Only add `--openai-image-check` if image handling is important or the EPUB has many plates, diagrams, maps, facsimiles, inscriptions, etc.

## Strict mode

`--strict` exits with an error if the PDF has delivery-blocking QA warnings such as possible header collisions, black/dark page artifacts, non-A4 pages, possible blank-page artifacts, or single-letter line spills.

This is intentional. Your prompt treats those as hard failures, so the script should not quietly pretend the PDF is print-ready.

## Important limitation

This is as comprehensive as a local automated pipeline can reasonably be, but not every malformed EPUB can be perfected without manual review. The script enforces rules and produces QA evidence; it does not replace final human visual inspection for deluxe binding.

## Running-head style

The default header follows the reference-style classic page head: verso/left pages show the collection title, recto/right pages show the current major work, and a single full-width hairline rule is drawn across the text block as a safe vector stroke. Configure it with:

```yaml
runner_layout: "right_title_full_rule"
runner_rule_style: "full_width"
runner_collection_transform: "none"
runner_work_transform: "uppercase"
runner_title_top_mm: 8.5
runner_rule_y_mm: 17.0
runner_body_clearance_mm: 6.0
runner_rule_color: "#222"
```

Other supported runner layouts:

```yaml
runner_layout: "centered_single_rule"
runner_layout: "dual_full_rule"
runner_layout: "alternating"
```
