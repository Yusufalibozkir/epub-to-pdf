#!/usr/bin/env python3
"""
Deluxe EPUB -> print-ready A4 PDF pipeline.

This is a thin wrapper around the pipeline/ package for backward compatibility.
Usage is identical to the original single-script version:

    python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --out sample.pdf --sample-pages 50

You can also run the package directly:

    python -m pipeline book.epub --sample-pages 50
"""
from pipeline._cli import parse_args
from pipeline._pipeline import build_pipeline

if __name__ == "__main__":
    build_pipeline(parse_args())
