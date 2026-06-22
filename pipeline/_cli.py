"""
Command-line argument parsing for the EPUB-to-PDF pipeline.
"""
from __future__ import annotations

import argparse
import sys
import textwrap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        usage=(
            "%(prog)s EPUB [options]\n"
            "       %(prog)s --batch DIR [options]\n"
            "       %(prog)s --write-default-config FILE\n"
            "       %(prog)s --help"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Convert an EPUB into a print-ready PDF with structural cleanup, "
            "configurable typography, optional AI assistance, and QA checks."
        ),
        epilog=textwrap.dedent(
            """
            Common PowerShell examples:
              .venv\\Scripts\\python.exe deluxe_epub_to_pdf.py --write-default-config my_style.yaml
              .venv\\Scripts\\python.exe deluxe_epub_to_pdf.py book.epub --config my_style.yaml --sample-pages 50
              .venv\\Scripts\\python.exe deluxe_epub_to_pdf.py book.epub --config my_style.yaml --out full.pdf --full-without-sample
              .venv\\Scripts\\python.exe deluxe_epub_to_pdf.py --batch books --config my_style.yaml --out output --batch-title-source filename --full-without-sample
              .venv\\Scripts\\python.exe deluxe_epub_to_pdf.py book.epub --config my_style.yaml --use-openai --openai-qa --debug-html

            Typical workflow:
              1. Create or edit a config file with --write-default-config.
              2. Render a short sample with --sample-pages.
              3. Render the full book with --full-without-sample after review.

            By default, PDFs are written to output/ and run artifacts to artifacts/<pdf-name>/.
            """
        ),
    )

    io = p.add_argument_group("Input and output")
    io.add_argument("epub", nargs="?", help="Input EPUB file")
    io.add_argument("--out", default="print_ready.pdf", metavar="PDF", help="Output PDF filename/path. In batch mode, a folder-like --out value can be used as the output folder.")
    io.add_argument("--output-dir", default="output", metavar="DIR", help="Explicit folder for PDFs in batch mode, or the folder used for a bare single-book --out filename.")
    io.add_argument("--artifacts-dir", default="artifacts", metavar="DIR", help="Folder for QA reports, QA renders, and debug builds.")
    io.add_argument("--title", default=None, metavar="TEXT", help="Clean-title override. Omit to use EPUB metadata.")
    io.add_argument("--author", default=None, metavar="TEXT", help="Author override for running heads and title-page fallback. Omit to use EPUB metadata.")
    io.add_argument("--volume-mode", choices=["auto", "single", "collection"], default=None, help="Book-structure override: auto detects, single forces standalone-book runner logic, collection forces collected/complete-works runner logic.")
    io.add_argument("--config", default=None, metavar="FILE", help="YAML or JSON config overriding style/settings.")
    io.add_argument("--write-default-config", default=None, metavar="FILE", help="Write a complete editable default YAML config and exit.")

    workflow = p.add_argument_group("Workflow")
    workflow.add_argument("--sample-pages", type=int, default=0, metavar="N", help="Create a first-N-pages sample PDF for review.")
    workflow.add_argument("--full-without-sample", action="store_true", help="Bypass sample-first warning and build the full PDF intentionally.")
    workflow.add_argument("--no-sample-requirement", action="store_true", help="Disable sample-first warning in settings.")
    workflow.add_argument("--section", default=None, metavar="TEXT", help="Render only the named logical section (for example a division or major work title) after full-book scanning/classification, for faster testing.")

    batch = p.add_argument_group("Batch conversion")
    batch.add_argument("--batch", default=None, metavar="DIR", help="Convert all EPUB files in a folder.")
    batch.add_argument("--recursive", action="store_true", help="Search subfolders when using --batch.")
    batch.add_argument("--batch-glob", default="*.epub", metavar="PATTERN", help="File pattern for --batch discovery. Default: *.epub.")
    batch.add_argument("--skip-existing", action="store_true", help="Skip a book when the target PDF already exists.")
    batch.add_argument("--on-error", choices=["continue", "stop"], default="continue", help="Batch behavior after a book fails.")
    batch.add_argument("--batch-title-source", choices=["metadata", "filename"], default="metadata", help="Batch title source. Use EPUB metadata or the filename stem.")

    typography = p.add_argument_group("Typography overrides")
    typography.add_argument("--body-size", type=float, default=None, metavar="PT", help="Body font size in pt; config key: body_size_pt.")
    typography.add_argument("--line-height", type=float, default=None, metavar="N", help="Body line-height multiplier; config key: line_height.")
    typography.add_argument("--font-stack", "--font-family", dest="font_stack", default=None, metavar="CSS", help="CSS font-family stack; config key: font_stack.")
    typography.add_argument("--font-dir", default=None, metavar="DIR", help="Folder containing configured local font files; config key: font_dir.")
    typography.add_argument("--embedded-font-family", default=None, metavar="NAME", help="CSS @font-face family name for local font files.")
    typography.add_argument("--embedded-font-regular", default=None, metavar="FILE", help="Regular/upright local font filename inside --font-dir.")
    typography.add_argument("--embedded-font-italic", default=None, metavar="FILE", help="Italic local font filename inside --font-dir.")
    typography.add_argument("--embedded-font-weight", default=None, metavar="CSS", help="CSS font-weight or range for embedded font files, e.g. 400 or '400 800'.")
    typography.add_argument("--no-embed-font-files", action="store_true", help="Disable local @font-face embedding and rely on installed system fonts.")
    typography.add_argument("--margin-top", type=float, default=None, metavar="MM", help="Top margin in mm; config key: margin_top_mm.")
    typography.add_argument("--margin-side", type=float, default=None, metavar="MM", help="Equal left/right margin in mm; config key: margin_side_mm.")
    typography.add_argument("--margin-bottom", type=float, default=None, metavar="MM", help="Bottom margin in mm; config key: margin_bottom_mm.")
    typography.add_argument("--paragraph-indent", type=float, default=None, metavar="EM", help="Paragraph first-line indent in em; config key: paragraph_indent_em.")
    typography.add_argument("--subdivision-margin-top", type=float, default=None, metavar="MM", help="Chapter/subdivision heading top margin in mm.")
    typography.add_argument("--subdivision-margin-bottom", type=float, default=None, metavar="MM", help="Chapter/subdivision heading bottom margin in mm.")
    typography.add_argument("--verse-line-height", type=float, default=None, metavar="N", help="Verse line-height multiplier; config key: verse_line_height.")
    typography.add_argument("--verse-max-width", type=float, default=None, metavar="MM", help="Verse block max width in mm; config key: verse_max_width_mm.")

    toc = p.add_argument_group("Table of contents")
    toc.add_argument("--toc-mode", choices=["auto", "simple", "hierarchical"], default=None, help="TOC mode: auto prompts in an interactive shell, simple keeps only top-level entries, hierarchical preserves nesting.")
    toc.add_argument("--back-toc-mode", choices=["off", "simple", "hierarchical"], default=None, help="Optional back-of-book TOC mode. off disables it, simple keeps only top-level entries, hierarchical preserves nesting.")

    running_heads = p.add_argument_group("Running heads and folios")
    running_heads.add_argument("--runner-font", type=float, default=None, metavar="PT", help="Shared running-head font size in pt. Side-specific runner fonts fall back to this when unset.")
    running_heads.add_argument("--runner-left-font", type=float, default=None, metavar="PT", help="Verso running-head font size in pt; falls back to --runner-font when unset.")
    running_heads.add_argument("--runner-right-font", type=float, default=None, metavar="PT", help="Recto running-head font size in pt; falls back to --runner-font when unset.")
    running_heads.add_argument("--folio-font", type=float, default=None, metavar="PT", help="Page-number/folio font size in pt; config key: folio_font_pt.")
    running_heads.add_argument("--runner-rule-gap", type=float, default=None, metavar="MM", help="Gap between running-head text and rule in mm; config key: runner_rule_gap_mm.")
    running_heads.add_argument("--runner-body-clearance", type=float, default=None, metavar="MM", help="Extra gap between runner rule area and body text in mm; config key: runner_body_clearance_mm.")
    running_heads.add_argument("--runner-rule-y", type=float, default=None, metavar="MM", help="Full-width vector runner rule position from top trim in mm; config key: runner_rule_y_mm.")
    running_heads.add_argument("--runner-title-top", type=float, default=None, metavar="MM", help="Running-head title offset from top trim in mm; config key: runner_title_top_mm.")
    running_heads.add_argument("--runner-layout", default=None, choices=["right_title_full_rule", "centered_single_rule", "dual_full_rule", "alternating"], help="Running-head layout.")
    running_heads.add_argument("--runner-rule-style", default=None, choices=["full_width", "single", "split", "none"], help="Runner rule rendering style; config key: runner_rule_style.")
    running_heads.add_argument("--runner-collection-transform", default=None, metavar="CSS", help="CSS text-transform for the verso collection-title runner.")
    running_heads.add_argument("--runner-work-transform", default=None, metavar="CSS", help="CSS text-transform for the recto current-work runner.")

    cleanup = p.add_argument_group("Cleanup and content handling")
    cleanup.add_argument("--keep-all-images", action="store_true", help="Keep all EPUB images; overrides image_policy.")
    cleanup.add_argument("--remove-all-images", action="store_true", help="Remove all images; overrides image_policy.")
    cleanup.add_argument("--no-smart-punctuation", action="store_true", help="Disable conservative punctuation cleanup.")
    cleanup.add_argument("--no-drop-caps", action="store_true", help="Disable decorative drop caps at chapter starts.")
    cleanup.add_argument("--no-small-caps", action="store_true", help="Disable automatic small-caps normalization for abbreviations.")
    cleanup.add_argument("--ligature-setting", default=None, choices=["common", "none", "all", "discretionary"], help="CSS font-variant-ligatures setting.")
    cleanup.add_argument("--footnote-handling", default=None, choices=["auto", "endnotes-only", "disabled"], help="How to handle inline footnote bodies in malformed EPUBs.")

    qa = p.add_argument_group("QA, debug, and performance")
    qa.add_argument("--no-optimize", action="store_true", help="Skip pikepdf optimization.")
    qa.add_argument("--no-qa-render", action="store_true", help="Do not render PNG QA pages.")
    qa.add_argument("--debug-html", action="store_true", help="Keep generated HTML/CSS build folder.")
    qa.add_argument("--no-cache", action="store_true", help="Disable content-addressed caching of intermediate results.")
    qa.add_argument("--strict", action="store_true", help="Exit non-zero when QA blockers are detected.")
    qa.add_argument("--max-auto-fix-passes", type=int, default=1, metavar="N", help="Maximum rerenders after local/AI QA-driven safe CSS/config fixes.")

    ai = p.add_argument_group("Optional AI assistance")
    ai.add_argument("--ai-provider", choices=["openai", "deepseek", "none"], default="openai", help="Provider for text/structure AI tasks.")
    ai.add_argument("--use-openai", action="store_true", help="Use the selected --ai-provider for whole-book structure planning.")
    ai.add_argument("--openai-model", default="gpt-5.4-mini", metavar="MODEL", help="OpenAI model for structure/image/visual QA.")
    ai.add_argument("--deepseek-model", default="deepseek-chat", metavar="MODEL", help="DeepSeek model for structure/text QA.")
    ai.add_argument("--openai-image-check", action="store_true", help="Use the selected --ai-provider to classify each image/caption block.")
    ai.add_argument("--openai-qa", action="store_true", help="Send rendered QA pages to OpenAI vision for visual review.")
    ai.add_argument("--openai-qa-pages", type=int, default=10, metavar="N", help="Maximum rendered pages sent to OpenAI visual QA.")
    ai.add_argument("--ai-qa-pages", type=int, default=50, metavar="N", help="Maximum sequential PDF pages scanned for AI text QA, plus work-opening pages.")
    ai.add_argument("--no-text-qa", action="store_true", help="Disable provider text QA, e.g. DeepSeek post-local-QA review.")

    args = p.parse_args(raw_argv)
    args.out_was_explicit = any(arg == "--out" or arg.startswith("--out=") for arg in raw_argv)
    args.output_dir_was_explicit = any(arg == "--output-dir" or arg.startswith("--output-dir=") for arg in raw_argv)
    if args.keep_all_images and args.remove_all_images:
        p.error("Choose only one of --keep-all-images or --remove-all-images")
    if args.batch and args.epub:
        p.error("Use either a single EPUB argument or --batch DIR, not both")
    if args.batch and args.title is not None:
        p.error("--title cannot be used with --batch because each EPUB needs its own title")
    if args.batch and args.out_was_explicit and args.output_dir_was_explicit:
        p.error("Use either --out or --output-dir in batch mode, not both")
    if not args.epub and not args.batch and not args.write_default_config:
        p.error("the following argument is required: epub or --batch DIR, unless using --write-default-config")
    return args
