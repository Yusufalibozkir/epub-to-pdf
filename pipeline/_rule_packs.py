"""
Rule pack system: load external YAML patterns and merge them into the built-in regexes.

The rule pack system allows extending any of the 13 built-in regex patterns
with additional terms from reviewed YAML files. AI-generated suggestions are
written as .review.yaml files that must be manually reviewed before loading.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from pipeline import _constants as C
from pipeline._models import BuildLog, Settings
from pipeline._utils import clean_text, resolve_project_path


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit("YAML support requires PyYAML: pip install pyyaml\n" + str(exc))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Rule pack must contain a top-level mapping: {path}")
    return data


def rule_pack_names(settings: Settings) -> list[str]:
    raw = clean_text(settings.rule_packs)
    if not raw:
        return []
    return [name.strip() for name in re.split(r"[,;]", raw) if name.strip()]


def compile_extended_pattern(existing: re.Pattern, additions: list[str], source: Path, key: str) -> re.Pattern:
    valid: list[str] = []
    for pattern in additions:
        pattern = str(pattern).strip()
        if not pattern:
            continue
        try:
            re.compile(pattern, existing.flags)
        except re.error as exc:
            raise SystemExit(f"Invalid regex in {source} under {key}: {pattern}\n{exc}")
        valid.append(pattern)
    if not valid:
        return existing
    combined = f"(?:{existing.pattern})|(?:{'|'.join(valid)})"
    return re.compile(combined, existing.flags)


def apply_rule_packs(settings: Settings, log: Optional[BuildLog] = None) -> None:
    """Extend built-in regexes with reviewed YAML rule packs.

    This function modifies the pattern objects in C._PATTERN_DICT and the
    corresponding module-level variables in pipeline._constants at runtime.
    """
    names = rule_pack_names(settings)
    if not names:
        return
    rule_dir = resolve_project_path(settings.rule_pack_dir)
    if not rule_dir.exists():
        if log:
            log.warn(f"Rule-pack directory not found: {rule_dir}")
        return
    loaded: list[str] = []
    for name in names:
        path = (rule_dir / name).resolve()
        if not path.exists():
            if log:
                log.warn(f"Rule pack not found: {path}")
            continue
        data = load_yaml_file(path)
        for key, global_name in C.RULE_PACK_KEYS.items():
            values = data.get(key) or []
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                raise SystemExit(f"Rule pack key must be a list or string: {path}::{key}")
            if values:
                existing = C._PATTERN_DICT.get(global_name)
                if existing is None:
                    if log:
                        log.warn(f"Pattern {global_name} not found in registry; skipping")
                    continue
                new_pattern = compile_extended_pattern(existing, values, path, key)
                C.update_pattern(global_name, new_pattern)
        loaded.append(path.name)
    if loaded and log:
        log.warn("Loaded regex rule packs: " + ", ".join(loaded))


def extract_review_rule_suggestions(report: str) -> dict[str, list[str]]:
    """Extract AI-suggested regex rules from an AI QA report."""
    yaml_text = ""
    m = re.search(r"```(?:yaml|yml)\s*(.*?)```", report, re.S | re.I)
    if m:
        yaml_text = m.group(1)
    else:
        start = report.find("rule_suggestions:")
        if start >= 0:
            yaml_text = report[start:]
    if not yaml_text.strip():
        return {}
    try:
        import yaml

        data = yaml.safe_load(yaml_text) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    data = data.get("rule_suggestions", data)
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key in C.RULE_PACK_KEYS:
        values = data.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        cleaned: list[str] = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("pattern", "")
            pattern = clean_text(str(value))
            if not pattern:
                continue
            try:
                re.compile(pattern, re.I)
            except re.error:
                continue
            cleaned.append(pattern)
        if cleaned:
            out[key] = cleaned
    return out


def write_review_rule_suggestions(path: Path, suggestions: dict[str, list[str]], source_report: Path) -> None:
    lines = [
        "# AI-suggested regex rules for human review.",
        "# Do not load this file directly until each pattern has been checked against real EPUB samples.",
        f"# Source report: {source_report.name}",
        "",
    ]
    for key in C.RULE_PACK_KEYS:
        values = suggestions.get(key, [])
        if not values:
            continue
        lines.append(f"{key}:")
        for value in values:
            lines.append("  - " + json.dumps(value, ensure_ascii=False))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
