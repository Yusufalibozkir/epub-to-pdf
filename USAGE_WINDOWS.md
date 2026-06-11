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
