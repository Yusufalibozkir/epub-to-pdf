@echo off
REM Double-click this file to open a PowerShell terminal with the
REM virtual environment ready. You type the pipeline commands.

cd /d "%~dp0"

powershell.exe -NoExit -ExecutionPolicy Bypass -Command ^
  "$host.UI.RawUI.WindowTitle = 'Deluxe EPUB Pipeline';" ^
  "if (-not (Test-Path '.venv')) { Write-Host 'Creating venv...' -ForegroundColor Cyan; python -m venv .venv };" ^
  "$va = '.venv\Scripts\Activate.ps1';" ^
  "if (-not (Test-Path $va)) { Write-Host 'ERROR: venv not found' -ForegroundColor Red; exit };" ^
  "if (-not (Test-Path '.venv\installed.flag')) { & $va; python -m pip install --upgrade pip setuptools wheel -q; pip install -r requirements.txt -q; if ($LASTEXITCODE -eq 0) { New-Item '.venv\installed.flag' -Force | Out-Null } };" ^
  "& $va;" ^
  "Write-Host '';" ^
  "Write-Host '============================================' -ForegroundColor Cyan;" ^
  "Write-Host '  Deluxe EPUB -> PDF Pipeline' -ForegroundColor Cyan;" ^
  "Write-Host '  Venv activated! Type your command:' -ForegroundColor Cyan;" ^
  "Write-Host '============================================' -ForegroundColor Cyan;" ^
  "Write-Host '';" ^
  "Write-Host 'EXAMPLES (paste one):' -ForegroundColor Yellow;" ^
  "Write-Host '';" ^
  "Write-Host '  # Quick sample (first 50 pages)' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --sample-pages 50' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Full PDF (bypasses sample-first warning)' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Override title/author from CLI' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --title \"Anna Karenina\" --author \"Leo Tolstoy\"' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Choose AI provider for structure planning' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --use-openai --ai-provider openai --openai-model \"gpt-5.4-mini\"' -ForegroundColor Green;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --use-openai --ai-provider deepseek --deepseek-model \"deepseek-chat\"' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # AI image check + visual QA (needs OPENAI_API_KEY)' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --openai-image-check --openai-qa' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # TOC style: simple (flat) vs hierarchical (nested)' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --toc-mode simple --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --toc-mode hierarchical --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Volume mode: affects running-head logic' -ForegroundColor Cyan;" ^
  "Write-Host '  #   auto (default) — detects from book title:' -ForegroundColor Magenta;" ^
  "Write-Host '  #     \"Complete Works of X\" / \"Collected Stories of Y\" → collection' -ForegroundColor Magenta;" ^
  "Write-Host '  #     plain title like \"Anna Karenina\" → single' -ForegroundColor Magenta;" ^
  "Write-Host '  #   collection — suppresses chapter/part in runner, uses author as fallback' -ForegroundColor Magenta;" ^
  "Write-Host '  #   single — uses current work/chapter title in runner' -ForegroundColor Magenta;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --volume-mode collection --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --volume-mode single --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Batch: convert all EPUBs in a folder' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py --batch books/ --config my_style.yaml --out output/ --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py --batch books/ --recursive --skip-existing --on-error continue' -ForegroundColor Green;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py --batch books/ --batch-title-source filename --full-without-sample' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Notebook / interactive: first write a config' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py --write-default-config my_style.yaml' -ForegroundColor Green;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --use-openai --openai-qa --debug-html' -ForegroundColor Green;" ^
  "Write-Host '';" ^
  "Write-Host '  # Quick dev sample with debug HTML kept' -ForegroundColor Cyan;" ^
  "Write-Host '  python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --sample-pages 50 --debug-html' -ForegroundColor Green;" ^
  "Write-Host ''"

exit /b 0
