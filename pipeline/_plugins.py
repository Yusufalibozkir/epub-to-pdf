"""
Plugin system for the EPUB-to-PDF pipeline.

Provides a registration API for extending the pipeline with:
  - Custom regex patterns (via YAML rule packs)
  - Python-based custom cleaners (callable hooks)
  - Python-based custom classifiers
  - Python-based custom QA checks

Plugin discovery:
  1. YAML rule packs in the configured ``rule_pack_dir`` (existing system, extended).
  2. Python modules discovered in a ``plugins/`` directory (new).
  3. AI-generated rule suggestions in ``.review.yaml`` files (existing, unchanged).

Each Python plugin module can define any of these hooks:

    register_cleaners() -> list[Callable[[BeautifulSoup, Settings, BuildLog], None]]
        Called during the HTML cleanup phase for each document.

    register_classifiers() -> list[Callable[[SpineDoc], None]]
        Called during spine-item classification.

    register_regex_patterns() -> dict[str, list[str]]
        Returns a dict mapping rule-pack key names to lists of regex patterns.
        Keys must match those in RULE_PACK_KEYS.

    register_qa_checks() -> list[Callable[[QAVerdict, Page, int, Settings], None]]
        Called during the QA preflight phase for each page.

    register_post_processors() -> list[Callable[[pdf_path, Settings, BuildLog], None]]
        Called after PDF rendering but before optimization.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline import _constants as C
from pipeline._models import BuildLog, Settings
from pipeline._utils import clean_text, resolve_project_path


# ======================================================================================
# Plugin registries
# ======================================================================================

_cleaner_plugins: list[Callable] = []
_classifier_plugins: list[Callable] = []
_regex_plugins: dict[str, list[str]] = {}
_qa_plugins: list[Callable] = []
_post_plugins: list[Callable] = []


def register_cleaner(fn: Callable) -> None:
    """Register a custom HTML cleaner function.

    Signature: fn(soup: BeautifulSoup, settings: Settings, log: BuildLog) -> None
    """
    if fn not in _cleaner_plugins:
        _cleaner_plugins.append(fn)


def register_classifier(fn: Callable) -> None:
    """Register a custom spine-doc classifier.

    Signature: fn(doc: SpineDoc) -> None
    """
    if fn not in _classifier_plugins:
        _classifier_plugins.append(fn)


def register_regex_patterns(patterns: dict[str, list[str]]) -> None:
    """Register custom regex patterns to extend built-in patterns.

    Keys must match RULE_PACK_KEYS. Each value is a list of regex strings.
    """
    for key, values in patterns.items():
        if key not in C.RULE_PACK_KEYS:
            continue
        existing = _regex_plugins.setdefault(key, [])
        for v in values:
            v = str(v).strip()
            if v and v not in existing:
                try:
                    re.compile(v, re.I)
                    existing.append(v)
                except re.error:
                    pass


def register_qa_check(fn: Callable) -> None:
    """Register a custom QA check.

    Signature: fn(verdict: QAVerdict, page, page_no: int, settings: Settings) -> None
    """
    if fn not in _qa_plugins:
        _qa_plugins.append(fn)


def register_post_processor(fn: Callable) -> None:
    """Register a custom PDF post-processor.

    Signature: fn(pdf_path: Path, settings: Settings, log: BuildLog) -> None
    """
    if fn not in _post_plugins:
        _post_plugins.append(fn)


# ======================================================================================
# Plugin discovery and loading
# ======================================================================================


def discover_plugins(plugin_dir: Path, log: Optional[BuildLog] = None) -> list[str]:
    """Discover and load Python plugin modules from a directory.

    Scans ``plugin_dir`` for ``*.py`` files (except ``__init__``) and
    imports each one, triggering any ``register_*()`` calls at module level.

    Returns a list of loaded plugin names.
    """
    if not plugin_dir.exists():
        return []

    loaded: list[str] = []
    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            _load_plugin_module(py_file)
            loaded.append(py_file.stem)
            if log:
                log.warn(f"[Plugins] Loaded: {py_file.name}")
        except Exception as exc:
            if log:
                log.warn(f"[Plugins] Failed to load {py_file.name}: {exc}")

    return loaded


def _load_plugin_module(path: Path) -> None:
    """Import a single plugin .py file."""
    module_name = f"_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load plugin: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)


# ======================================================================================
# Apply plugins to the pipeline
# ======================================================================================


def apply_plugin_regex_patterns(settings: Settings, log: Optional[BuildLog] = None) -> None:
    """Merge plugin-registered regex patterns into the built-in constants.

    Also loads YAML rule packs from the configured directory (existing system).
    """
    # 1) YAML rule packs (original system)
    _apply_yaml_rule_packs(settings, log)

    # 2) Python plugin patterns
    if _regex_plugins:
        for key, values in _regex_plugins.items():
            global_name = C.RULE_PACK_KEYS.get(key)
            if global_name is None:
                continue
            existing = C._PATTERN_DICT.get(global_name)
            if existing is None:
                continue
            from pipeline._rule_packs import compile_extended_pattern

            new_pattern = compile_extended_pattern(
                existing, values, Path("<plugin>"), key
            )
            C.update_pattern(global_name, new_pattern)
            if log:
                log.warn(
                    f"[Plugins] Extended {global_name} with {len(values)} plugin pattern(s)."
                )


def run_plugin_cleaners(soup, settings: Settings, log: BuildLog) -> None:
    """Run all registered plugin cleaner functions on a document soup."""
    for fn in _cleaner_plugins:
        try:
            fn(soup, settings, log)
        except Exception as exc:
            log.warn(f"[Plugins] Cleaner {fn.__name__} failed: {exc}")


def run_plugin_classifiers(doc) -> None:
    """Run all registered plugin classifier functions on a spine doc."""
    for fn in _classifier_plugins:
        try:
            fn(doc)
        except Exception:
            pass


def run_plugin_qa_checks(verdict, page, page_no: int, settings: Settings) -> None:
    """Run all registered plugin QA checks on a rendered page."""
    for fn in _qa_plugins:
        try:
            fn(verdict, page, page_no, settings)
        except Exception:
            pass


def run_plugin_post_processors(pdf_path: Path, settings: Settings, log: BuildLog) -> None:
    """Run all registered plugin PDF post-processors."""
    for fn in _post_plugins:
        try:
            fn(pdf_path, settings, log)
        except Exception as exc:
            log.warn(f"[Plugins] Post-processor {fn.__name__} failed: {exc}")


# ======================================================================================
# Internal: YAML rule pack loader (bridged from _rule_packs.py)
# ======================================================================================


def _apply_yaml_rule_packs(settings: Settings, log: Optional[BuildLog] = None) -> None:
    """Load YAML rule packs from the configured directory."""
    names = _yaml_rule_pack_names(settings)
    if not names:
        return
    rule_dir = resolve_project_path(settings.rule_pack_dir)
    if not rule_dir.exists():
        return
    from pipeline._rule_packs import compile_extended_pattern, load_yaml_file

    loaded: list[str] = []
    for name in names:
        path = (rule_dir / name).resolve()
        if not path.exists():
            continue
        data = load_yaml_file(path)
        for key, global_name in C.RULE_PACK_KEYS.items():
            values = data.get(key) or []
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                continue
            if values:
                existing = C._PATTERN_DICT.get(global_name)
                if existing is None:
                    continue
                new_pattern = compile_extended_pattern(existing, values, path, key)
                C.update_pattern(global_name, new_pattern)
        loaded.append(path.name)
    if loaded and log:
        log.warn("[Plugins] Loaded YAML rule packs: " + ", ".join(loaded))


def _yaml_rule_pack_names(settings: Settings) -> list[str]:
    raw = clean_text(settings.rule_packs)
    if not raw:
        return []
    return [name.strip() for name in re.split(r"[,;]", raw) if name.strip()]
