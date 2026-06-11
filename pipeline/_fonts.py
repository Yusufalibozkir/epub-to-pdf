"""
Font and image asset handling for the PDF build.
"""
from __future__ import annotations

import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional

from pipeline._models import BuildLog, Settings
from pipeline._utils import clean_text, resolve_local_path


def prepare_embedded_fonts(build_dir: Path, settings: Settings, log: BuildLog) -> str:
    """Copy configured font files to the build directory and return @font-face CSS.

    Returns an empty string if font embedding is disabled or font files are missing.
    """
    if not settings.embed_font_files:
        return ""
    font_dir = resolve_local_path(settings.font_dir)
    regular_name = clean_text(settings.embedded_font_regular)
    italic_name = clean_text(settings.embedded_font_italic)
    family = clean_text(settings.embedded_font_family) or "EB Garamond"
    weight = clean_text(settings.embedded_font_weight) or "400"
    if not font_dir.exists():
        if family.lower() in settings.font_stack.lower():
            log.warn(f"Font embedding enabled, but font_dir was not found: {font_dir}")
        return ""

    font_targets: list[tuple[str, str]] = []
    for filename, style in [(regular_name, "normal"), (italic_name, "italic")]:
        if not filename:
            continue
        source = (font_dir / filename).resolve()
        if not source.exists():
            log.warn(f"Configured font file was not found: {source}")
            continue
        target_dir = build_dir / "fonts"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name)
        target = target_dir / safe_name
        shutil.copyfile(source, target)
        font_targets.append((f"fonts/{urllib.parse.quote(target.name)}", style))

    if not font_targets:
        if family.lower() in settings.font_stack.lower():
            log.warn(f"No configured font files were embedded for {family}; PDF may fall back to system fonts.")
        return ""

    css_blocks = []
    escaped_family = family.replace('"', '\\"')
    for rel_url, style in font_targets:
        css_blocks.append(
            f'''@font-face {{\n  font-family: "{escaped_family}";\n  src: url("{rel_url}") format("truetype");\n  font-style: {style};\n  font-weight: {weight};\n  font-display: block;\n}}'''
        )
    return "\n".join(css_blocks)
