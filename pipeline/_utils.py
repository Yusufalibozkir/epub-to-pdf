"""
Utility functions used throughout the pipeline.
"""
from __future__ import annotations

import html
import json
import os
import posixpath
import re
import shutil
import urllib.parse
import warnings
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning
from ebooklib import ITEM_IMAGE, epub


# --------------------------------------------------------------------------------------
# Text utilities
# --------------------------------------------------------------------------------------


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str, fallback: str = "section") -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[''']", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or fallback


def unique_id(base: str, used: set[str]) -> str:
    base = slugify(base)
    ident = base
    n = 2
    while ident in used:
        ident = f"{base}-{n}"
        n += 1
    used.add(ident)
    return ident


def normalized_title_key(value: str | None) -> str:
    """Normalize headings for duplicate detection without destroying display text."""
    text = clean_text(value or "").lower()
    text = re.sub(r"\[\*+\]", "", text)
    text = re.sub(r"\b(deluxe print interior|a novel|a story|a tale|a play)\b", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def visible_word_count(value: str | None) -> int:
    return len(re.findall(r"\b[\w''-]+\b", clean_text(value or "")))


def clean_display_title(value: str | None) -> str:
    """Remove EPUB/navigation artifacts that should not appear in print headings."""
    text = clean_text(value or "")
    text = re.sub(r"\[\*+\]", "", text)
    return clean_text(text)


# --------------------------------------------------------------------------------------
# Path / file utilities
# --------------------------------------------------------------------------------------


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    cwd_path = (Path.cwd() / path).resolve()
    if cwd_path.exists():
        return cwd_path
    return (Path(__file__).resolve().parent.parent / path).resolve()


def resolve_local_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    cwd_path = (Path.cwd() / path).resolve()
    if cwd_path.exists():
        return cwd_path
    return (Path(__file__).resolve().parent.parent / path).resolve()


# --------------------------------------------------------------------------------------
# EPUB item / HTML utilities
# --------------------------------------------------------------------------------------


def normalize_src(src: str, doc_name: str) -> str:
    src = urllib.parse.unquote((src or "").split("#", 1)[0])
    if not src:
        return ""
    base = posixpath.dirname(doc_name)
    return posixpath.normpath(posixpath.join(base, src))


def strip_tag(tag: Tag) -> None:
    try:
        tag.decompose()
    except Exception:
        try:
            tag.extract()
        except Exception:
            pass


def item_bytes(item) -> bytes:
    data = item.get_content()
    if isinstance(data, str):
        return data.encode("utf-8", errors="ignore")
    return data


def parse_html(raw: bytes) -> BeautifulSoup:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        return BeautifulSoup(raw, "lxml")


def remove_comments_scripts_styles(soup: BeautifulSoup) -> None:
    for c in soup.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    for tag in soup.find_all(["script", "style", "iframe", "object", "embed"]):
        strip_tag(tag)


def add_classes(tag: Tag, classes: list[str]) -> list[str]:
    existing = tag.get("class", [])
    if isinstance(existing, str):
        existing = existing.split()
    return list(dict.fromkeys(existing + classes))


def first_significant_tag(body: Tag | BeautifulSoup) -> Optional[Tag]:
    for child in body.children:
        if isinstance(child, NavigableString):
            if clean_text(str(child)):
                return None
            continue
        if isinstance(child, Tag):
            if clean_text(child.get_text(" ")) or child.find(["img", "svg", "table"]):
                return child
    return None


def copy_assets(book, build_dir: Path, log) -> dict[str, str]:
    """Extract EPUB images to the build directory and return a src-name mapping."""
    assets = build_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for item in book.get_items():
        if item.get_type() != ITEM_IMAGE:
            continue
        name = item.get_name()
        suffix = Path(name).suffix.lower() or ".img"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).name)
        if not safe.lower().endswith(suffix):
            safe += suffix
        target = assets / safe
        n = 2
        while target.exists():
            target = assets / f"{Path(safe).stem}_{n}{suffix}"
            n += 1
        try:
            target.write_bytes(item_bytes(item))
        except Exception as exc:
            log.warn(f"Could not extract image {name}: {exc}")
            continue
        rel = f"assets/{target.name}"
        mapping[name] = rel
        mapping[Path(name).name] = rel
        mapping[posixpath.normpath(name)] = rel
    return mapping
