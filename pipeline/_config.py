"""
Config loading, CLI override application, and default config generation.

Three-layer override precedence:
  1. Built-in Settings defaults
  2. YAML/JSON config file (--config)
  3. Explicit CLI flags
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Optional

from pipeline._models import Settings
from pipeline._utils import clean_text, slugify


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def coerce_setting_value(field: dataclasses.Field, value: Any) -> Any:
    """Lightly coerce YAML/JSON values into the dataclass field type."""
    default = field.default
    try:
        if isinstance(default, bool):
            return coerce_bool(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, str):
            return str(value)
    except Exception:
        return value
    return value


def load_config(path: Optional[str], settings: Settings) -> Settings:
    """Load YAML or JSON config. Missing keys keep the built-in defaults."""
    if not path:
        return settings
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.exists():
        raise SystemExit(f"Config file not found: {cfg_path}")
    if cfg_path.suffix.lower() == ".json":
        data = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        try:
            import yaml
        except Exception as exc:
            raise SystemExit("YAML config requires PyYAML: pip install pyyaml\n" + str(exc))
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit("Config file must contain a top-level mapping/object of setting names to values.")
    known = {f.name: f for f in dataclasses.fields(Settings)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        print("WARNING: ignoring unknown config keys: " + ", ".join(unknown), file=sys.stderr)
    for name, field_obj in known.items():
        if name in data:
            setattr(settings, name, coerce_setting_value(field_obj, data[name]))
    return settings


def apply_cli_overrides(settings: Settings, args: Any) -> Settings:
    """Apply only explicitly supplied CLI style overrides after config loading."""
    if args.title is not None:
        settings.title = args.title
    if getattr(args, "author", None) is not None:
        settings.author = args.author
    cli_to_setting = {
        "body_size": "body_size_pt",
        "line_height": "line_height",
        "font_stack": "font_stack",
        "font_dir": "font_dir",
        "embedded_font_family": "embedded_font_family",
        "embedded_font_regular": "embedded_font_regular",
        "embedded_font_italic": "embedded_font_italic",
        "embedded_font_weight": "embedded_font_weight",
        "margin_top": "margin_top_mm",
        "margin_side": "margin_side_mm",
        "margin_bottom": "margin_bottom_mm",
        "runner_font": "runner_font_pt",
        "runner_left_font": "runner_left_font_pt",
        "runner_right_font": "runner_right_font_pt",
        "folio_font": "folio_font_pt",
        "runner_rule_gap": "runner_rule_gap_mm",
        "runner_body_clearance": "runner_body_clearance_mm",
        "runner_rule_y": "runner_rule_y_mm",
        "runner_title_top": "runner_title_top_mm",
        "runner_layout": "runner_layout",
        "runner_rule_style": "runner_rule_style",
        "runner_collection_transform": "runner_collection_transform",
        "runner_work_transform": "runner_work_transform",
        "verse_line_height": "verse_line_height",
        "verse_max_width": "verse_max_width_mm",
        "paragraph_indent": "paragraph_indent_em",
        "subdivision_margin_top": "subdivision_margin_top_mm",
        "subdivision_margin_bottom": "subdivision_margin_bottom_mm",
        "toc_mode": "toc_mode",
        "back_toc_mode": "back_toc_mode",
        "volume_mode": "volume_mode",
    }
    for cli_name, setting_name in cli_to_setting.items():
        value = getattr(args, cli_name, None)
        if value is not None:
            setattr(settings, setting_name, value)
    if args.strict:
        settings.strict = True
    if args.no_sample_requirement:
        settings.no_sample_requirement = True
    if args.no_smart_punctuation:
        settings.smart_punctuation = False
    if args.no_drop_caps:
        settings.drop_caps = False
    if args.no_small_caps:
        settings.small_caps = False
    if args.ligature_setting is not None:
        settings.ligature_setting = args.ligature_setting
    if args.footnote_handling is not None:
        settings.footnote_handling = args.footnote_handling
    if args.no_embed_font_files:
        settings.embed_font_files = False
    if args.keep_all_images:
        settings.image_policy = "keep-all"
    elif args.remove_all_images:
        settings.image_policy = "remove-all"
    return settings


def resolve_toc_mode(settings: Settings, prompt_if_auto: bool = True) -> str:
    """Resolve the effective TOC mode, prompting once in an interactive shell if needed."""
    mode = clean_text(settings.toc_mode).strip().lower() or "auto"
    if mode in {"simple", "hierarchical"}:
        settings.toc_mode = mode
        return mode
    if not prompt_if_auto or not sys.stdin.isatty():
        settings.toc_mode = "simple"
        return settings.toc_mode
    try:
        answer = input("TOC mode? [s]imple / [h]ierarchical [Enter=simple]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer.startswith("h"):
        settings.toc_mode = "hierarchical"
    else:
        settings.toc_mode = "simple"
    return settings.toc_mode


def write_default_config(path: str) -> None:
    target = Path(path).expanduser().resolve()
    data = dataclasses.asdict(Settings())
    lines = [
        "# Deluxe EPUB-to-print-PDF pipeline config",
        "# Missing keys keep the built-in defaults. You can delete anything you do not want to override.",
        "# Override order: built-in defaults -> this config file -> explicit CLI flags.",
        "",
    ]
    for key, value in data.items():
        if isinstance(value, str):
            encoded = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            encoded = "true" if value else "false"
        else:
            encoded = str(value)
        lines.append(f"{key}: {encoded}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote default config: {target}")


def resolve_output_pdf(args: Any) -> Path:
    out_arg = Path(args.out).expanduser()
    if out_arg.is_absolute() or out_arg.parent != Path("."):
        return out_arg.resolve()
    return (Path(args.output_dir).expanduser().resolve() / out_arg.name)


def resolve_artifact_dir(args: Any, out_pdf: Path) -> Path:
    artifact_root = Path(args.artifacts_dir).expanduser().resolve()
    return artifact_root / slugify(out_pdf.stem, "run")
