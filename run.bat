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
  "Write-Host '  python deluxe_epub_to_pdf.py \"book.epub\" --out sample.pdf --sample-pages 50 --debug-html' -ForegroundColor Green;" ^
  "Write-Host ''"

exit /b 0
