# User Guide: Local Deluxe EPUB-to-Print-PDF Pipeline

## 1. What this tool does

This project converts an EPUB file into an A4, single-page, print-oriented PDF interior suitable for a deluxe physical book edition. It is intended for large public-domain books and collected editions where a simple ebook export is not good enough.

The pipeline tries to automate the production rules in the deluxe EPUB-to-PDF prompt:

- A4 print PDF output, not screen-oriented ebook export.
- Half title, full title page, source page, and generated table of contents.
- Roman numeral pagination for front matter and Arabic pagination for main matter.
- Recto starts for important book divisions where CSS paged media supports them.
- Running heads on normal text pages only.
- Collection title on verso pages and current major work/division on recto pages.
- Suppression of runners and folios on title/display/blank pages.
- Removal of obvious promotional/catalogue material and repeated local mini-contents blocks.
- Heuristic preservation/removal of EPUB images according to whether they are functional/authorial or publisher-added plates.
- Basic poetry, verse, drama, cast-list, and note-reference normalization.
- PDF optimization and QA reporting.
- Optional OpenAI-assisted structure planning, image classification, and visual page review.

The tool is strict by design. It is not meant to silently produce a file and pretend everything is perfect. It creates reports and rendered QA images so you can inspect the result before printing or binding.

## 2. Important limitation

This pipeline can automate a large part of the work, but it cannot guarantee perfect human typesetting judgment for every malformed EPUB. Some decisions are genuinely editorial:

- whether an image is an authorial diagram or a decorative publisher plate;
- whether short broken lines are poetry, song, dialogue, or broken OCR/prose;
- whether a heading is a major work title or a subtitle;
- whether a cast list has been normalized elegantly enough;
- whether the page feels commercially balanced.

For that reason, a sample-first workflow is strongly recommended.

---

## 3. Package contents

The package contains these main files:

| File | Purpose |
|---|---|
| `deluxe_epub_to_pdf.py` | Main conversion script. |
| `requirements.txt` | Python dependencies. |
| `README.md` | Short project overview. |
| `USAGE_WINDOWS.md` | Quick Windows command examples. |
| `deluxe_config.example.yaml` | Editable configuration template. |
| `REQUIREMENTS_MATRIX.md` | Mapping between the prompt requirements and implementation status. |
| `MANUAL_QA_CHECKLIST.md` | Human visual QA checklist before printing. |
| `install_windows.ps1` | Simple Windows install helper. |
| `run_sample_example.ps1` | Example sample-build command. |
| `USER_GUIDE.md` | This guide. |

---

## 4. Installation on Windows

Open PowerShell in the project folder.

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

If WeasyPrint fails to install or fails at runtime, install WeasyPrint according to its official platform instructions for your operating system. WeasyPrint sometimes needs system libraries depending on the Windows/Python environment.

---

## 5. Installation on macOS or Linux

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If WeasyPrint fails, install the required system dependencies for your OS, then rerun `pip install -r requirements.txt`.

---

## 6. The recommended workflow

Do not start with a full 2,000-page book build unless you already trust the settings for that exact type of EPUB.

Recommended sequence:

1. Generate or edit a config file.
2. Build a 30–50 page sample.
3. Inspect the sample PDF and `qa/` page renders.
4. Check `qa_report.txt`, `qa_verdict.json`, and, if used, `openai_visual_qa.txt`.
5. Adjust config values such as font size, line height, margins, runner spacing, or image policy.
6. Rebuild the sample.
7. Only then build the full PDF.
8. Run full-book QA and inspect representative pages.

This mirrors professional book production: sample → correction → full build → final QA.

---

## 7. Quick start: first sample build

From inside the project folder:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "sample.pdf" --sample-pages 50 --debug-html
```

This creates a first 50-page sample and keeps the generated HTML/CSS build folder for inspection.

The important outputs are:

| Output | Meaning |
|---|---|
| `sample.pdf` | The PDF sample. |
| `qa_report.txt` | Human-readable QA report. |
| `qa_verdict.json` | Machine-readable QA result. |
| `build_summary.json` | Settings and summary of the build. |
| `qa/` | Rendered page images for visual inspection. |
| `_build_sample/` or similar | Generated HTML/CSS when `--debug-html` is used. |

---

## 8. Quick start with OpenAI assistance

Set your OpenAI API key in PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-your-key-here"
```

Run a sample with OpenAI structure planning and visual QA:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "sample_ai.pdf" --sample-pages 50 --use-openai --openai-qa --debug-html
```

This mode lets OpenAI help classify EPUB sections and review rendered page images.

Use this first before enabling image-by-image OpenAI checks.

---

## 9. Full build after sample approval

Once the sample looks good:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "full_print.pdf" --full-without-sample --use-openai --openai-qa --strict
```

`--full-without-sample` tells the script you intentionally want a full build.

`--strict` tells the script to fail if delivery-blocking QA warnings remain.

---

## 10. Configuration files

The pipeline has built-in defaults. You do not need a config file for every run, but using one is strongly recommended.

Generate a full editable config:

```powershell
python deluxe_epub_to_pdf.py --write-default-config my_style.yaml
```

Then edit `my_style.yaml` in a text editor.

Use the config:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50
```

### Override order

Settings are applied in this order:

```text
built-in defaults → config file → explicit command-line flags
```

That means command-line flags always win.

Example:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --body-size 12 --line-height 1.28 --out "sample.pdf" --sample-pages 50
```

Even if `my_style.yaml` says `body_size_pt: 11.6`, the command above uses `12` because `--body-size 12` is explicit.

---

## 11. Configuration reference

Below are the main config keys.

### Book identity

```yaml
title: ""
trim_size: "A4"
```

| Key | Meaning |
|---|---|
| `title` | Clean title used in front matter and runners. Leave empty to infer from EPUB metadata. |
| `trim_size` | Baseline is `A4`. Other trim sizes need manual QA and may require CSS changes. |

### Core typography

```yaml
body_size_pt: 11.6
line_height: 1.23
font_stack: '"EB Garamond", "Cormorant Garamond", Garamond, Georgia, serif'
text_color: "#111"
body_font_weight: "400"
hyphenate: true
justify_prose: true
```

| Key | Meaning |
|---|---|
| `body_size_pt` | Main body type size in points. Increase for readability; decrease for huge collected works. |
| `line_height` | Body line spacing multiplier. Larger values add air; smaller values save pages. |
| `font_stack` | CSS font-family stack. The first installed font available on your system will be used. |
| `text_color` | Main text color. `#111` is dark but not artificially bold. |
| `body_font_weight` | Usually keep `400`. Use higher values carefully. |
| `hyphenate` | Enables CSS hyphenation if supported. Helps justified text. |
| `justify_prose` | Fully justifies prose. Turn off only for unusual layouts. |

Suggested starting ranges:

| Use case | Body size | Line height |
|---|---:|---:|
| Very large collected works | 10.8–11.4 pt | 1.18–1.23 |
| Standard readable A4 classics | 11.5–12.2 pt | 1.22–1.30 |
| Spacious deluxe edition | 12.3–13.0 pt | 1.28–1.38 |

### Margins and live area

```yaml
margin_top_mm: 28.0
margin_side_mm: 22.0
margin_bottom_mm: 25.0
front_margin_top_mm: 25.0
front_margin_bottom_mm: 24.0
```

| Key | Meaning |
|---|---|
| `margin_top_mm` | Top text-area margin for main pages. Increase if runner/body clearance is tight. |
| `margin_side_mm` | Equal left/right margin. Baseline uses equal margins, not mirrored gutter margins. |
| `margin_bottom_mm` | Bottom margin. Affects folio space and page density. |
| `front_margin_top_mm` | Front-matter top margin. |
| `front_margin_bottom_mm` | Front-matter bottom margin. |

### Running heads and folios

```yaml
runner_font_pt: 9.4
runner_letter_spacing_em: 0.04
runner_rule_gap_mm: 3.0
runner_rule_weight_pt: 0.45
runner_rule_color: "#777"
folio_font_pt: 10.0
front_folio_font_pt: 9.3
```

| Key | Meaning |
|---|---|
| `runner_font_pt` | Running-head text size. |
| `runner_letter_spacing_em` | Letter spacing for running heads. |
| `runner_rule_gap_mm` | Clearance around the runner rule. Increase if the rule looks like an overline. |
| `runner_rule_weight_pt` | Thickness of the runner rule. |
| `runner_rule_color` | Runner rule color. |
| `folio_font_pt` | Page number size in main matter. |
| `front_folio_font_pt` | Page number size in front matter. |

If the header line is too close to the body text, first try:

```yaml
margin_top_mm: 31.0
runner_rule_gap_mm: 3.8
```

### Paragraphs and block quotations

```yaml
paragraph_indent_em: 1.25
blockquote_side_margin_mm: 10.0
blockquote_font_percent: 96.0
```

| Key | Meaning |
|---|---|
| `paragraph_indent_em` | First-line indent for ordinary prose paragraphs. |
| `blockquote_side_margin_mm` | Left/right margin for block quotations. |
| `blockquote_font_percent` | Relative size of blockquote text. |

### Major openings and headings

```yaml
major_opener_top_margin_mm: 55.0
major_opener_bottom_margin_mm: 15.0
major_work_font_pt: 23.5
collection_division_font_pt: 25.0
subdivision_font_pt: 14.8
h3_font_pt: 13.0
minor_heading_font_pt: 11.4
```

| Key | Meaning |
|---|---|
| `major_opener_top_margin_mm` | Top whitespace before major work titles. |
| `major_opener_bottom_margin_mm` | Space after major work titles. |
| `major_work_font_pt` | Font size for major work openers. |
| `collection_division_font_pt` | Font size for parent divisions such as “The Novels.” |
| `subdivision_font_pt` | Font size for h2/subdivision headings. |
| `h3_font_pt` | Font size for h3 headings. |
| `minor_heading_font_pt` | Font size for minor headings. |

### Table of contents

```yaml
toc_title_font_pt: 19.0
toc_level_1_font_pt: 11.4
toc_level_2_font_pt: 10.5
toc_level_3_font_pt: 10.0
toc_level_4_font_pt: 9.7
toc_line_height: 1.11
toc_entry_gap_mm: 2.9
```

| Key | Meaning |
|---|---|
| `toc_title_font_pt` | “Contents” heading size. |
| `toc_level_1_font_pt` | First-level TOC entry size. |
| `toc_level_2_font_pt` | Second-level TOC entry size. |
| `toc_level_3_font_pt` | Third-level TOC entry size. |
| `toc_level_4_font_pt` | Fourth-level TOC entry size. |
| `toc_line_height` | TOC line-height. Reduce if TOC looks too loose. |
| `toc_entry_gap_mm` | Vertical gap between TOC entries. |

If the TOC looks ugly or too loose, try:

```yaml
toc_level_1_font_pt: 10.8
toc_level_2_font_pt: 10.0
toc_line_height: 1.04
toc_entry_gap_mm: 2.1
```

### Poetry and verse

```yaml
verse_max_width_mm: 128.0
verse_line_height: 1.15
verse_hanging_indent_em: 1.4
verse_block_margin_top_mm: 4.0
verse_block_margin_bottom_mm: 5.0
```

| Key | Meaning |
|---|---|
| `verse_max_width_mm` | Maximum width of verse blocks. Wider values help long narrative verse. |
| `verse_line_height` | Verse line spacing. Keep tighter than prose when verse looks stretched. |
| `verse_hanging_indent_em` | Indent for runover/wrapped verse lines. |
| `verse_block_margin_top_mm` | Space above verse blocks. |
| `verse_block_margin_bottom_mm` | Space below verse blocks. |

For long narrative verse, try:

```yaml
verse_max_width_mm: 138.0
verse_line_height: 1.08
```

For short lyric poetry, try:

```yaml
verse_max_width_mm: 112.0
verse_line_height: 1.16
```

### Drama and cast lists

```yaml
cast_max_width_mm: 132.0
cast_line_height: 1.16
```

| Key | Meaning |
|---|---|
| `cast_max_width_mm` | Width of cast/character-list blocks. |
| `cast_line_height` | Line spacing for cast lists. |

### Image behavior

```yaml
image_policy: "functional"
```

Available values:

| Value | Meaning |
|---|---|
| `functional` | Keep images likely to be authorial/functional; remove obvious publisher plates/promotional images. Recommended default. |
| `keep-all` | Keep every EPUB image. Useful if the book is illustrated or image-dependent. |
| `remove-all` | Remove all images. Useful for text-only editions where all images are known to be decorative plates. |

Command-line shortcuts:

```powershell
--keep-all-images
--remove-all-images
```

These override the config file.

### Cleanup and behavior

```yaml
smart_punctuation: true
strict: false
no_sample_requirement: false
```

| Key | Meaning |
|---|---|
| `smart_punctuation` | Enables conservative cleanup of spaces, ellipses, and dashes. |
| `strict` | Config-level strictness flag. The CLI `--strict` is more direct. |
| `no_sample_requirement` | Suppresses sample-first warning. |

---

## 12. Command-line reference

### Required positional argument

```powershell
python deluxe_epub_to_pdf.py "book.epub"
```

The EPUB path is required unless you are only writing a default config.

### Output path

```powershell
--out "print_ready.pdf"
```

Sets the output PDF path. Default is `print_ready.pdf`.

### Optional title override

```powershell
--title "Complete Works of Nikolai Gogol"
```

Overrides the EPUB metadata title for front matter and running heads. Omit this in normal use; the script reads the book title from the EPUB automatically.

### Config file

```powershell
--config my_style.yaml
```

Loads YAML or JSON settings.

### Generate default config

```powershell
--write-default-config my_style.yaml
```

Writes a full editable config and exits.

### Sample build

```powershell
--sample-pages 50
```

Creates a PDF containing only the first N pages after rendering. Use this for review.

### Full build confirmation

```powershell
--full-without-sample
```

Suppresses the sample-first warning and confirms you want the full book.

### Style overrides

```powershell
--body-size 12
--line-height 1.28
--font-stack '"EB Garamond", Garamond, Georgia, serif'
--margin-top 30
--margin-side 22
--margin-bottom 25
--runner-font 9.8
--folio-font 10.5
--runner-rule-gap 3.6
--paragraph-indent 1.25
--verse-line-height 1.12
--verse-max-width 132
```

These override both built-in defaults and the config file.

### Image options

```powershell
--keep-all-images
--remove-all-images
```

Use only one of them.

### Cleanup and output options

```powershell
--no-smart-punctuation
--no-optimize
--no-qa-render
--debug-html
```

| Flag | Meaning |
|---|---|
| `--no-smart-punctuation` | Disables conservative punctuation cleanup. |
| `--no-optimize` | Skips pikepdf optimization. Use only for debugging. |
| `--no-qa-render` | Does not render PNG QA pages. Faster, but less safe. |
| `--debug-html` | Keeps generated HTML/CSS build folder. Very useful for troubleshooting. |

### Strictness and auto-fix

```powershell
--strict
--max-auto-fix-passes 1
```

| Flag | Meaning |
|---|---|
| `--strict` | Exits with an error if delivery-blocking QA warnings remain. |
| `--max-auto-fix-passes` | Number of deterministic regeneration attempts after fixable warnings, such as header collisions. |

### OpenAI options

```powershell
--use-openai
--openai-model gpt-5.4-mini
--openai-image-check
--openai-qa
--openai-qa-pages 10
```

| Flag | Meaning |
|---|---|
| `--use-openai` | Uses OpenAI for whole-book structure planning. Recommended for complex EPUBs. |
| `--openai-model` | Model name used for OpenAI calls. |
| `--openai-image-check` | Uses OpenAI to classify each image/caption block. More expensive on image-heavy EPUBs. |
| `--openai-qa` | Sends rendered QA pages to OpenAI for visual review. |
| `--openai-qa-pages` | Maximum number of rendered pages sent to OpenAI visual QA. |

---

## 13. Understanding the output files

After a run, you should usually see:

| File/folder | Purpose |
|---|---|
| `your_output.pdf` | Final or sample PDF. |
| `qa_report.txt` | Main human-readable QA report. Read this first. |
| `qa_verdict.json` | Structured QA results. Useful for automation. |
| `build_summary.json` | Build settings, page count, warnings, and auto-fixes. |
| `openai_visual_qa.txt` | OpenAI visual QA report, only if `--openai-qa` was used. |
| `qa/` | Rendered page images from the PDF. Inspect these visually. |
| `_build_<output_name>/` | Generated HTML/CSS/assets, only kept when `--debug-html` is used. |

### `qa_report.txt`

This summarizes:

- page count;
- page-size status;
- font inventory;
- image count;
- removed documents;
- removed blocks;
- removed local mini-TOCs;
- detected poetry blocks;
- detected cast sections;
- kept/removed images;
- possible header collisions;
- possible broken-word/single-letter line spills;
- possible narrow columns;
- blank-page artifacts;
- TOC warnings;
- OpenAI decisions, when used.

### `qa_verdict.json`

This contains machine-readable fields such as:

- `page_count`
- `non_a4_pages`
- `possible_line_spills`
- `dark_pages`
- `possible_blank_page_artifacts`
- `possible_header_collisions`
- `possible_narrow_columns`
- `toc_page_number_warnings`
- `fonts_seen`
- `images_seen`
- `qa_renders`

If `possible_header_collisions`, `possible_line_spills`, `dark_pages`, or `possible_blank_page_artifacts` are non-empty, inspect carefully.

### `build_summary.json`

This records the final settings actually used. It is useful when you generate a good sample and want to preserve the exact configuration.

---

## 14. Manual QA checklist

Before printing or binding, inspect at least:

1. Half title page.
2. Full title page.
3. Source/copyright page.
4. First TOC page.
5. Last TOC page.
6. First Arabic-numbered page of the main text.
7. Several normal text pages with running heads.
8. A later page from each major work or division.
9. Poetry pages, if present.
10. Cast-list/dramatis-personae pages, if present.
11. Pages with maps, diagrams, facsimiles, runes, inscriptions, or image-text.
12. Final 3–5 pages.

Reject and rebuild if you see:

- running-head rule touching or visually crowding the first body line;
- page numbers or running heads on blank/title/display pages;
- TOC without page numbers;
- repeated local mini-contents inside works;
- publisher catalogue/promotional matter;
- orphan captions from removed image plates;
- single-letter or broken-word line spills;
- narrow ebook-like columns;
- poetry justified as prose;
- cast lists collapsed awkwardly into Act I or Scene I;
- black/gray page artifacts;
- missing authorial maps, diagrams, inscriptions, or image-texts.

---

## 15. Recommended workflows by book type

### Large collected prose works

Use the default style or slightly compact settings:

```yaml
body_size_pt: 11.2
line_height: 1.20
margin_side_mm: 21.0
paragraph_indent_em: 1.2
```

Run:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --use-openai --openai-qa --debug-html
```

### Poetry-heavy books

Use tighter verse leading and inspect poetry pages carefully:

```yaml
body_size_pt: 11.6
line_height: 1.23
verse_line_height: 1.10
verse_max_width_mm: 128.0
verse_hanging_indent_em: 1.4
```

Run with OpenAI:

```powershell
python deluxe_epub_to_pdf.py "poetry.epub" --config poetry_style.yaml --out "sample.pdf" --sample-pages 80 --use-openai --openai-qa --debug-html
```

### Books with plays/drama

Use OpenAI structure planning and inspect cast pages:

```powershell
python deluxe_epub_to_pdf.py "plays.epub" --out "sample.pdf" --sample-pages 80 --use-openai --openai-qa --debug-html
```

Check that character lists do not collapse into Act I or Scene I.

### Illustrated or image-dependent books

Start conservatively with all images kept:

```powershell
python deluxe_epub_to_pdf.py "illustrated.epub" --out "sample.pdf" --sample-pages 80 --keep-all-images --use-openai --openai-qa --debug-html
```

Then switch to functional image policy only after you understand the book:

```yaml
image_policy: "functional"
```

For difficult cases, add:

```powershell
--openai-image-check
```

### Text-only edition with decorative plates

If you know all images are decorative or publisher-added:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --out "sample.pdf" --sample-pages 50 --remove-all-images --use-openai --openai-qa
```

Use this carefully. It will remove maps, diagrams, facsimiles, and inscriptions too.

---

## 16. Troubleshooting

### Problem: WeasyPrint installation fails

Symptoms:

- Import errors involving WeasyPrint.
- Missing DLL/library errors.
- PDF rendering fails immediately.

Fix:

1. Make sure your virtual environment is active.
2. Upgrade pip:

```powershell
python -m pip install --upgrade pip setuptools wheel
```

3. Reinstall:

```powershell
pip install -r requirements.txt
```

4. If it still fails, install WeasyPrint system dependencies for your OS.

### Problem: The font is not actually EB Garamond

The CSS font stack can only use fonts installed on your computer or available to the renderer. If EB Garamond is not installed, WeasyPrint will fall back to the next available font.

Fix:

- Install EB Garamond locally.
- Or change `font_stack` to a font you know is installed.

Example:

```yaml
font_stack: '"EB Garamond", Garamond, Georgia, serif'
```

Check `qa_report.txt` for `Fonts seen`.

### Problem: Running-head rule is too close to body text

Fix by increasing top margin and runner gap:

```yaml
margin_top_mm: 31.0
runner_rule_gap_mm: 3.8
```

Or use CLI overrides:

```powershell
--margin-top 31 --runner-rule-gap 3.8
```

The script also has an auto-fix pass for detected header collisions, controlled by:

```powershell
--max-auto-fix-passes 1
```

### Problem: TOC looks too loose or large

Try smaller TOC values:

```yaml
toc_level_1_font_pt: 10.8
toc_level_2_font_pt: 10.0
toc_line_height: 1.04
toc_entry_gap_mm: 2.1
```

### Problem: Body text looks too small

Increase body size and possibly line height:

```yaml
body_size_pt: 12.0
line_height: 1.27
```

### Problem: Body text looks too loose and page count is too high

Decrease line height first, then font size only if needed:

```yaml
line_height: 1.18
body_size_pt: 11.2
```

### Problem: Poetry looks vertically stretched

Tighten verse line height:

```yaml
verse_line_height: 1.08
```

### Problem: Long verse lines wrap badly

Increase verse width:

```yaml
verse_max_width_mm: 138.0
```

### Problem: Images that should be kept were removed

Use one of these approaches:

```powershell
--keep-all-images
```

or:

```powershell
--openai-image-check
```

For books with maps, diagrams, runes, inscriptions, facsimiles, and image-text, start with `--keep-all-images` and remove later only if necessary.

### Problem: Decorative publisher plates remain

Use:

```powershell
--use-openai --openai-image-check
```

If you know all images are decorative:

```powershell
--remove-all-images
```

### Problem: Strict mode fails

Strict mode fails when the QA system finds delivery-blocking warnings.

Open:

- `qa_report.txt`
- `qa_verdict.json`
- PNGs in `qa/`
- `openai_visual_qa.txt`, if generated

Fix the underlying issue, then rerun.

Do not ignore strict failures for print-ready output.

---

## 17. Cost-control advice for OpenAI mode

Use OpenAI in stages.

Cheapest useful mode:

```powershell
--use-openai --openai-qa --sample-pages 50
```

More expensive mode for image-heavy books:

```powershell
--use-openai --openai-image-check --openai-qa
```

Avoid `--openai-image-check` on huge illustrated EPUBs until you know you need it. It may classify many image/caption blocks.

Control visual QA pages:

```powershell
--openai-qa-pages 6
```

or:

```powershell
--openai-qa-pages 12
```

Use fewer pages for cheap tests and more pages for serious final review.

---

## 18. Suggested production presets

### Default deluxe A4

```yaml
body_size_pt: 11.6
line_height: 1.23
margin_side_mm: 22.0
margin_top_mm: 28.0
margin_bottom_mm: 25.0
runner_font_pt: 9.4
folio_font_pt: 10.0
```

### Slightly larger readable edition

```yaml
body_size_pt: 12.1
line_height: 1.28
margin_side_mm: 22.0
margin_top_mm: 30.0
margin_bottom_mm: 25.0
runner_font_pt: 9.6
folio_font_pt: 10.5
```

### Compact collected works

```yaml
body_size_pt: 11.0
line_height: 1.18
margin_side_mm: 20.5
margin_top_mm: 28.0
margin_bottom_mm: 24.0
runner_font_pt: 9.0
folio_font_pt: 9.8
```

### Poetry-sensitive edition

```yaml
body_size_pt: 11.6
line_height: 1.23
verse_line_height: 1.08
verse_max_width_mm: 132.0
verse_block_margin_top_mm: 3.0
verse_block_margin_bottom_mm: 4.0
```

### TOC-tight edition

```yaml
toc_level_1_font_pt: 10.8
toc_level_2_font_pt: 10.0
toc_level_3_font_pt: 9.7
toc_line_height: 1.04
toc_entry_gap_mm: 2.1
```

---

## 19. Practical full example

Generate config:

```powershell
python deluxe_epub_to_pdf.py --write-default-config jules_verne_style.yaml
```

Edit the title and style values:

```yaml
title: "Complete Works of Jules Verne"
body_size_pt: 11.4
line_height: 1.21
margin_side_mm: 21.5
runner_rule_gap_mm: 3.5
image_policy: "functional"
```

Build a sample:

```powershell
python deluxe_epub_to_pdf.py "Complete Works of Jules Verne.epub" --config jules_verne_style.yaml --out "jules_verne_sample.pdf" --sample-pages 60 --use-openai --openai-qa --debug-html
```

Inspect:

```text
jules_verne_sample.pdf
qa_report.txt
qa_verdict.json
openai_visual_qa.txt
qa/
_build_jules_verne_sample/
```

If good, build full:

```powershell
python deluxe_epub_to_pdf.py "Complete Works of Jules Verne.epub" --config jules_verne_style.yaml --out "jules_verne_full_print.pdf" --full-without-sample --use-openai --openai-qa --strict
```

---

## 20. How to decide whether the output is acceptable

A build is acceptable only when all of these are true:

- PDF page size is A4.
- Fonts appear correct and embedded/available in the PDF.
- Front matter uses roman pagination.
- Main text starts with Arabic page 1.
- TOC has page numbers.
- Major works open cleanly.
- Ordinary chapters do not create wasteful blank pages.
- Blank pages are truly blank.
- Title/display/opening pages do not show inappropriate runners or folios.
- Running-head rule has clear air above the body text.
- No single-letter or broken-word line spills appear.
- Prose is justified and readable.
- Poetry preserves lineation and is not justified as prose.
- Cast lists are not ugly or collapsed into acts/scenes.
- Promotional catalogue material is removed.
- Repeated local mini-TOCs are removed.
- Authorial/functional images are preserved.
- Publisher-added decorative plates are removed if that is the selected policy.
- QA reports show no delivery blockers.
- Rendered QA images look clean.

---

## 21. A safe habit

For each author/book type, keep a separate config file:

```text
gogol_style.yaml
verne_style.yaml
pushkin_poetry_style.yaml
chekhov_plays_style.yaml
compact_collected_works.yaml
```

When a sample looks good, preserve that config. The `build_summary.json` file records the settings actually used, so you can recover the exact values later.

---

## 22. Minimal command cheat sheet

Generate config:

```powershell
python deluxe_epub_to_pdf.py --write-default-config my_style.yaml
```

Basic sample:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --debug-html
```

AI-assisted sample:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample_ai.pdf" --sample-pages 50 --use-openai --openai-qa --debug-html
```

Full strict build:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "full_print.pdf" --full-without-sample --use-openai --openai-qa --strict
```

Keep all images:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --keep-all-images
```

Remove all images:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --remove-all-images
```

Fix tight header spacing quickly:

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --margin-top 31 --runner-rule-gap 3.8
```
