# Windows usage

## Install

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
built-in defaults -> config file -> explicit CLI flags
```

## Sample build with config

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "sample.pdf" --sample-pages 50 --use-openai --openai-qa --debug-html
```

This writes the PDF to `output\sample.pdf` and run files to `artifacts\sample\`.

## Quick one-off style override

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --body-size 12 --line-height 1.28 --font-family '"EB Garamond", Garamond, Georgia, serif' --out "sample.pdf" --sample-pages 50
```

## Full build after sample approval

```powershell
python deluxe_epub_to_pdf.py "book.epub" --config my_style.yaml --out "full_print.pdf" --full-without-sample --use-openai --openai-qa --strict
```

Cleanup after experiments:

```powershell
Remove-Item -Recurse -Force output, artifacts
```
