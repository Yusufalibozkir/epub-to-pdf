python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
Write-Host "Installed. Activate with: .\.venv\Scripts\Activate.ps1"
Write-Host "Then run: python deluxe_epub_to_pdf.py book.epub --out sample.pdf --sample-pages 50"
Write-Host "    or: python -m pipeline book.epub --out sample.pdf --sample-pages 50"
