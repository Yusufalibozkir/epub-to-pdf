# Windows usage

## Quick start — double-click `run.bat`

Double-click `run.bat` in the project folder. It will create the virtual environment,
install dependencies, and open a PowerShell terminal ready for commands.

## Manual install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Create your own editable config

```powershell
python deluxe_epub_to_pdf.py --write-default-config my_style.yaml
```

Edit `my_style.yaml`. Any missing value keeps the built-in deluxe default.

Override order:

```text
built-in defaults → config file → explicit CLI flags
```

For TOCs, `toc_mode: simple` gives a shorter flat contents page, while `toc_mode: hierarchical` preserves nesting. Leave it at `auto` if you want the CLI to ask once in an interactive shell.

For running-head structure, leave `volume_mode: auto` unless the EPUB is misdetected. Use `--volume-mode single` for standalone books, or `--volume-mode collection` for collected/complete works. In batch mode, the same `--volume-mode` value is applied to every EPUB in the folder.

## Run via wrapper script (original) or package (new)

```powershell
# Original entry point
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50

# Package entry point (identical behavior)
python -m pipeline "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50
```

## Sample build with OpenAI

```powershell
$env:OPENAI_API_KEY="sk-your-key-here"
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --use-openai --openai-qa --debug-html
```

This writes the PDF to `output\sample.pdf` and run files to `artifacts\sample\`.

## Fast section preview for debugging

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --section "The Biographies" --out "biographies-preview.pdf" --sample-pages 20 --debug-html
```

`--section` still scans and classifies the whole EPUB first, then renders only the matching logical slice (currently a classified division, major work, or backmatter title). Generated title page and subset-scoped TOC are still included by default.

## Sample build with DeepSeek (text QA + rule suggestions)

```powershell
$env:DEEPSEEK_API_KEY="sk-your-key-here"
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --ai-provider deepseek --use-openai --out "sample.pdf" --sample-pages 50 --max-auto-fix-passes 2 --debug-html
```

## Quick one-off style override

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --body-size 12 --line-height 1.28 --out "sample.pdf" --sample-pages 50
```

## Full build after sample approval

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "full_print.pdf" --full-without-sample --use-openai --openai-qa --strict
```

## Batch convert a folder

First build review samples for every EPUB in a folder:

```powershell
python deluxe_epub_to_pdf.py --batch "books" --config my_style.yaml --out output --batch-title-source filename --sample-pages 50 --skip-existing
```

After approving the samples, build the full PDFs:

```powershell
python deluxe_epub_to_pdf.py --batch "books" --config my_style.yaml --out output --batch-title-source metadata --full-without-sample --skip-existing
```

Useful batch flags:

```text
--out output            Use a folder-like --out value as the batch output folder.
--output-dir output     Explicit batch output folder.
--recursive             Include subfolders.
--batch-glob PATTERN    Match a different file pattern. Default: *.epub.
--skip-existing         Skip PDFs already present in the output folder.
--on-error stop         Stop at the first failed book.
--batch-title-source    Choose metadata or filename for the visible title.
```

Each EPUB becomes one PDF named from the EPUB filename, and a batch report is written to `artifacts\batch_report_YYYYMMDD_HHMMSS.json`. Use `--batch-title-source metadata` to keep the EPUB metadata title, or `--batch-title-source filename` to use the filename stem as the displayed title.

## Build with auto-fix passes

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --max-auto-fix-passes 3 --debug-html
```

## Disable caching (for testing rule-pack/plugin changes)

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --no-cache
```

## Cleanup after experiments

```powershell
Remove-Item -Recurse -Force output, artifacts
```
