"""
Command-line argument parsing for the EPUB-to-PDF pipeline.
"""
from __future__ import annotations

import argparse
import textwrap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Convert an EPUB into a deluxe A4 print-oriented PDF with structural cleanup, "
        "configurable style settings, OpenAI-assisted options, and hard QA gates.",
        epilog=textwrap.dedent(
            """
            Examples:
              python deluxe_epub_to_pdf.py --write-default-config my_style.yaml
              python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --out sample.pdf --sample-pages 50 --use-openai --openai-qa --debug-html
              python deluxe_epub_to_pdf.py book.epub --config my_style.yaml --out full.pdf --full-without-sample --use-openai --strict
              python -m pipeline book.epub --body-size 12 --line-height 1.28 --sample-pages 50

            By default, PDFs are written to output/ and run artifacts to artifacts/<pdf-name>/.
            """
        ),
    )
    p.add_argument("epub", nargs="?", help="Input EPUB file")
    p.add_argument("--out", default="print_ready.pdf", help="Output PDF filename/path. Bare filenames are written under --output-dir.")
    p.add_argument("--output-dir", default="output", help="Folder for PDFs when --out is a bare filename")
    p.add_argument("--artifacts-dir", default="artifacts", help="Folder for QA reports, QA renders, and debug builds")
    p.add_argument("--title", default=None, help="Optional clean-title override. Omit to use EPUB metadata automatically.")
    p.add_argument("--config", default=None, help="Optional YAML or JSON config overriding style/settings")
    p.add_argument("--write-default-config", default=None, help="Write a complete editable default YAML config and exit")
    p.add_argument("--sample-pages", type=int, default=0, help="Create a first-N-pages sample PDF for review")
    p.add_argument("--full-without-sample", action="store_true", help="Bypass sample-first warning and build full PDF intentionally")
    p.add_argument("--no-sample-requirement", action="store_true", help="Disable sample-first warning in settings")

    # Style overrides
    p.add_argument("--body-size", type=float, default=None, help="Body font size in pt; config key: body_size_pt")
    p.add_argument("--line-height", type=float, default=None, help="Body line-height multiplier; config key: line_height")
    p.add_argument("--font-stack", "--font-family", dest="font_stack", default=None, help="CSS font-family stack; config key: font_stack")
    p.add_argument("--font-dir", default=None, help="Folder containing configured local font files; config key: font_dir")
    p.add_argument("--embedded-font-family", default=None, help="CSS @font-face family name for local font files")
    p.add_argument("--embedded-font-regular", default=None, help="Regular/upright local font filename inside --font-dir")
    p.add_argument("--embedded-font-italic", default=None, help="Italic local font filename inside --font-dir")
    p.add_argument("--embedded-font-weight", default=None, help="CSS font-weight or range for embedded font files, e.g. 400 or '400 800'")
    p.add_argument("--no-embed-font-files", action="store_true", help="Disable local @font-face embedding and rely on installed system fonts")
    p.add_argument("--margin-top", type=float, default=None, help="Top margin in mm; config key: margin_top_mm")
    p.add_argument("--margin-side", type=float, default=None, help="Equal left/right margin in mm; config key: margin_side_mm")
    p.add_argument("--margin-bottom", type=float, default=None, help="Bottom margin in mm; config key: margin_bottom_mm")
    p.add_argument("--runner-font", type=float, default=None, help="Running-head font size in pt; config key: runner_font_pt")
    p.add_argument("--folio-font", type=float, default=None, help="Page-number/folio font size in pt; config key: folio_font_pt")
    p.add_argument("--runner-rule-gap", type=float, default=None, help="Gap between running-head text and rule in mm; config key: runner_rule_gap_mm")
    p.add_argument("--runner-body-clearance", type=float, default=None, help="Extra gap between runner rule area and body text in mm; config key: runner_body_clearance_mm")
    p.add_argument("--runner-rule-y", type=float, default=None, help="Full-width vector runner rule position from top trim in mm; config key: runner_rule_y_mm")
    p.add_argument("--runner-title-top", type=float, default=None, help="Running-head title offset from top trim in mm; config key: runner_title_top_mm")
    p.add_argument("--runner-layout", default=None, choices=["right_title_full_rule", "centered_single_rule", "dual_full_rule", "alternating"], help="Running-head layout")
    p.add_argument("--runner-rule-style", default=None, choices=["full_width", "single", "split", "none"], help="Runner rule rendering style; config key: runner_rule_style")
    p.add_argument("--runner-collection-transform", default=None, help="CSS text-transform for collection title runner")
    p.add_argument("--runner-work-transform", default=None, help="CSS text-transform for current-work runner")
    p.add_argument("--paragraph-indent", type=float, default=None, help="Paragraph first-line indent in em; config key: paragraph_indent_em")
    p.add_argument("--subdivision-margin-top", type=float, default=None, help="Chapter/subdivision heading top margin in mm")
    p.add_argument("--subdivision-margin-bottom", type=float, default=None, help="Chapter/subdivision heading bottom margin in mm")
    p.add_argument("--verse-line-height", type=float, default=None, help="Verse line-height multiplier; config key: verse_line_height")
    p.add_argument("--verse-max-width", type=float, default=None, help="Verse block max width in mm; config key: verse_max_width_mm")

    p.add_argument("--keep-all-images", action="store_true", help="Keep all EPUB images; overrides image_policy")
    p.add_argument("--remove-all-images", action="store_true", help="Remove all images; overrides image_policy")
    p.add_argument("--no-smart-punctuation", action="store_true", help="Disable conservative punctuation cleanup")
    p.add_argument("--no-drop-caps", action="store_true", help="Disable decorative drop caps at chapter starts")
    p.add_argument("--no-small-caps", action="store_true", help="Disable automatic small-caps normalization for abbreviations")
    p.add_argument("--ligature-setting", default=None, choices=["common", "none", "all", "discretionary"], help="CSS font-variant-ligatures setting")
    p.add_argument("--footnote-handling", default=None, choices=["auto", "endnotes-only", "disabled"], help="How to handle inline footnote bodies in malformed EPUBs")
    p.add_argument("--no-optimize", action="store_true", help="Skip pikepdf optimization")
    p.add_argument("--no-qa-render", action="store_true", help="Do not render PNG QA pages")
    p.add_argument("--debug-html", action="store_true", help="Keep generated HTML/CSS build folder")
    p.add_argument("--no-cache", action="store_true", help="Disable content-addressed caching of intermediate results")
    p.add_argument("--strict", action="store_true", help="Exit non-zero when QA blockers are detected")
    p.add_argument("--max-auto-fix-passes", type=int, default=1, help="Maximum rerenders after local/OpenAI QA-driven safe CSS/config fixes")

    # AI options
    p.add_argument("--ai-provider", choices=["openai", "deepseek", "none"], default="openai", help="Provider for text/structure AI tasks")
    p.add_argument("--use-openai", action="store_true", help="Use the selected --ai-provider for whole-book structure planning")
    p.add_argument("--openai-model", default="gpt-5.4-mini", help="OpenAI model for structure/image/visual QA")
    p.add_argument("--deepseek-model", default="deepseek-chat", help="DeepSeek model for structure/text QA")
    p.add_argument("--openai-image-check", action="store_true", help="Use the selected --ai-provider to classify each image/caption block")
    p.add_argument("--openai-qa", action="store_true", help="Send rendered QA pages to OpenAI vision for visual review")
    p.add_argument("--openai-qa-pages", type=int, default=10, help="Maximum rendered pages sent to OpenAI visual QA")
    p.add_argument("--ai-qa-pages", type=int, default=50, help="Maximum sequential PDF pages scanned for AI text QA (plus work-opening pages)")
    p.add_argument("--no-text-qa", action="store_true", help="Disable provider text QA, e.g. DeepSeek post-local-QA review")

    args = p.parse_args(argv)
    if args.keep_all_images and args.remove_all_images:
        p.error("Choose only one of --keep-all-images or --remove-all-images")
    if not args.epub and not args.write_default_config:
        p.error("the following argument is required: epub, unless using --write-default-config")
    return args
