"""
HTML cleanup, normalization, and structural transformation pipeline.

Each EPUB spine document passes through ~20 sequential normalization functions
inside clean_document(). These handle: attribute stripping, promotional content
removal, local mini-TOC removal, image classification, poetry/drama detection,
heading classification, work description styling, and more.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from pipeline import _constants as C
from pipeline._models import BuildLog, Settings, SpineDoc, TocEntry
from pipeline._utils import (
    add_classes,
    clean_display_title,
    clean_text,
    first_significant_tag,
    normalize_src,
    normalized_title_key,
    parse_html,
    remove_comments_scripts_styles,
    strip_tag,
    unique_id,
    visible_word_count,
)


# ======================================================================================
# ATTRIBUTE & TAG CLEANUP
# ======================================================================================


def strip_bad_attributes(soup: BeautifulSoup) -> None:
    allowed = {"href", "src", "alt", "title", "id", "class", "colspan", "rowspan"}
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
        classes = tag.get("class")
        if classes:
            if isinstance(classes, str):
                classes = classes.split()
            kept = [
                c
                for c in classes
                if re.search(
                    r"poem|poetry|verse|stanza|line|cast|character|stage|note|footnote|endnote|chapter|title",
                    c,
                    re.I,
                )
            ]
            if kept:
                tag["class"] = kept
            elif "class" in tag.attrs:
                del tag.attrs["class"]


def unwrap_useless_inline_tags(soup: BeautifulSoup) -> None:
    for tag in list(soup.find_all(["span", "font"])):
        if tag.name == "font":
            tag.unwrap()
        elif not tag.attrs and not tag.get_text(strip=True):
            # Only unwrap truly empty spans (no text, no attrs).
            # Spans with text likely had semantic styling (italic, small-caps, etc.)
            # that was stripped by strip_bad_attributes(); unwrapping would lose it.
            tag.unwrap()


# ======================================================================================
# PROMOTIONAL CONTENT REMOVAL
# ======================================================================================


def remove_promotional_blocks(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["section", "div", "aside", "nav"])):
        text = clean_text(tag.get_text(" "))
        if not text:
            continue
        if _looks_like_promotional_text(tag, text, block=True):
            log.removed("Removed promotional/publisher block", text)
            strip_tag(tag)
    for tag in list(soup.find_all(["p", "li"])):
        text = clean_text(tag.get_text(" "))
        if _looks_like_promotional_text(tag, text, block=False):
            log.removed("Removed promotional line", text)
            strip_tag(tag)


def _looks_like_promotional_text(tag: Tag, text: str, block: bool = False) -> bool:
    if not C.PROMO_PATTERNS.search(text):
        return False
    lower = text.lower()
    class_text = (
        " ".join(tag.get("class", []))
        if isinstance(tag.get("class", []), list)
        else str(tag.get("class", ""))
    )
    has_links = bool(tag.find_all("a"))
    structural_clue = bool(
        re.search(r"promo|advert|copyright|license|publisher|newsletter|subscribe|catalog|toc|nav", class_text, re.I)
    )
    strong_phrase = bool(
        re.search(
            r"\b(delphi classics|project gutenberg license|gutenberg license|also available|"
            r"other books by|more books by|subscribe|newsletter|visit our website|follow us|"
            r"download our|app store|google play|goodreads|isbn)\b",
            lower,
            re.I,
        )
    )
    if block:
        return len(text) < 2800 and (strong_phrase or structural_clue or has_links)
    return len(text) < 360 and (strong_phrase or structural_clue or has_links)


# ======================================================================================
# LOCAL MINI-TOC REMOVAL
# ======================================================================================


def remove_local_mini_tocs(soup: BeautifulSoup, log: BuildLog) -> None:
    """Remove standalone local mini-TOCs (heading + link list)."""
    for heading in list(soup.find_all(re.compile(r"^h[1-6]$"))):
        htext = clean_text(heading.get_text(" "))
        if not C.LOCAL_TOC_HEADINGS.match(htext):
            continue
        followers: list[Any] = []
        cur = heading.next_sibling
        link_count = 0
        total_text = ""
        while cur is not None:
            nxt = cur.next_sibling
            if isinstance(cur, NavigableString):
                if not clean_text(str(cur)):
                    followers.append(cur)
                else:
                    break
            elif isinstance(cur, Tag):
                if re.match(r"h[1-6]", cur.name or ""):
                    break
                links = cur.find_all("a")
                text = clean_text(cur.get_text(" "))
                if cur.name in {"nav", "ol", "ul"} or len(links) >= 2 or (cur.name in {"p", "div"} and len(text) < 120 and links):
                    followers.append(cur)
                    link_count += len(links)
                    total_text += " " + text
                elif len(total_text) < 1000 and len(links) > 0:
                    followers.append(cur)
                    link_count += len(links)
                else:
                    break
            else:
                break
            cur = nxt
        if followers and (link_count >= 2 or len(clean_text(total_text)) < 1200):
            log.local_tocs_removed += 1
            log.removed("Removed local mini-contents", htext)
            strip_tag(heading)
            for f in followers:
                try:
                    f.extract()
                except Exception:
                    pass

    # Also detect C. (paragraph/div-based local TOCs)
    for heading in list(soup.find_all(["p", "div"])):
        htext = clean_text(heading.get_text(" "))
        if not C.LOCAL_TOC_HEADINGS.match(htext):
            continue
        followers: list[Any] = []
        cur = heading.next_sibling
        link_count = 0
        nav_lines = 0
        while cur is not None:
            nxt = cur.next_sibling
            if isinstance(cur, NavigableString):
                if not clean_text(str(cur)):
                    followers.append(cur)
                else:
                    break
            elif isinstance(cur, Tag):
                if re.match(r"h[1-6]", cur.name or ""):
                    break
                text = clean_text(cur.get_text(" "))
                links = cur.find_all("a")
                if not text:
                    followers.append(cur)
                elif cur.name in {"nav", "ol", "ul"} and (links or _looks_like_compact_local_contents(cur)):
                    followers.append(cur)
                    link_count += max(1, len(links))
                    nav_lines += 1
                elif links and len(text) < 140 and C.LOCAL_CONTENTS_LINE_RE.search(text):
                    followers.append(cur)
                    link_count += len(links)
                    nav_lines += 1
                else:
                    break
            else:
                break
            cur = nxt
        if followers and link_count >= 2 and nav_lines >= 2:
            log.local_tocs_removed += 1
            log.removed("Removed sibling local mini-contents", htext)
            strip_tag(heading)
            for f in followers:
                try:
                    f.extract()
                except Exception:
                    pass


def _block_text_lines(tag: Tag) -> list[str]:
    return [clean_text(x) for x in tag.get_text("\n").splitlines() if clean_text(x)]


def _looks_like_compact_local_contents(tag: Tag) -> bool:
    text = clean_text(tag.get_text(" "))
    if not text or len(text) > 2600:
        return False
    first = clean_text(" ".join(_block_text_lines(tag)[:4]))[:220]
    if not re.search(r"\bcontents\b", first, re.I):
        return False
    links = len(tag.find_all("a"))
    lines = _block_text_lines(tag)
    if links >= 3:
        return True
    if len(lines) < 6:
        return False
    short_lines = sum(1 for line in lines if visible_word_count(line) <= 8)
    nav_lines = sum(1 for line in lines if C.LOCAL_CONTENTS_LINE_RE.search(line))
    return nav_lines >= 4 and short_lines / max(1, len(lines)) >= 0.70


def remove_compact_local_contents_blocks(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["nav", "ol", "ul", "p", "div"])):
        if tag.find_parent(["nav", "ol", "ul", "p", "div"]) and not tag.find_all("a"):
            continue
        if _looks_like_compact_local_contents(tag):
            log.local_tocs_removed += 1
            log.removed("Removed compact local mini-contents", tag.get_text(" "))
            strip_tag(tag)


# ======================================================================================
# IMAGE HANDLING
# ======================================================================================


def _img_context(img: Tag) -> str:
    parts: list[str] = []
    alt = img.get("alt") or img.get("title") or ""
    if alt:
        parts.append(str(alt))
    parent = img.parent if isinstance(img.parent, Tag) else None
    if parent:
        parts.append(parent.get_text(" "))
        for sib in [
            parent.find_previous(["p", "figcaption", "h1", "h2", "h3"]),
            parent.find_next(["p", "figcaption", "h1", "h2", "h3"]),
        ]:
            if sib:
                parts.append(sib.get_text(" "))
    return clean_text(" ".join(parts))


def should_keep_image(
    img: Tag,
    settings: Settings,
    context: str,
    src: str,
    ai_client=None,
    ai_model: str = "gpt-5.4-mini",
    ai_provider: str = "openai",
    log: Optional[BuildLog] = None,
) -> bool:
    if settings.image_policy == "keep-all":
        return True
    if settings.image_policy == "remove-all":
        return False
    lower_src = src.lower()
    combined = clean_text(f"{src} {context}")
    has_functional_context = bool(
        C.FUNCTIONAL_IMAGE_CLUES.search(combined) or C.FUNCTIONAL_IMAGE_SRC_CLUES.search(lower_src)
    )
    has_plate_context = bool(
        C.PLATE_CAPTION_PATTERNS.search(combined) or C.PUBLISHER_IMAGE_SRC_CLUES.search(lower_src)
    )
    if has_plate_context and not has_functional_context:
        return False
    if ai_client is not None:
        try:
            # Lazy import to avoid circular dependency
            from pipeline._ai import ai_image_decision

            decision = ai_image_decision(ai_client, ai_model, src, context, provider=ai_provider)
            keep = bool(decision.get("keep", True))
            if log:
                log.ai_decisions.append(
                    f"image {src}: keep={keep} conf={decision.get('confidence')} reason={decision.get('reason')}"
                )
            if float(decision.get("confidence", 0) or 0) >= 0.70:
                return keep
        except Exception as exc:
            if log:
                log.warn(f"{ai_provider.title()} image decision failed for {src}: {exc}")
    if has_plate_context and not has_functional_context:
        return False
    if has_functional_context:
        return True
    if "cover" in lower_src or ("title" in lower_src and len(context) < 140):
        return False
    # Catch standalone decorative plates with no semantic context:
    # no alt text, no caption, generic filename, alone in container.
    alt_text = clean_text(str(img.get("alt", "") or img.get("title", "") or ""))
    if not alt_text and not context and not has_functional_context:
        # Ultra-conservative: only catch images with ZERO surrounding context.
        # No alt text, no caption, no sibling text, no parent text, no nearby
        # heading/paragraph. Legitimate book illustrations almost always have
        # at least ONE of these signals; a completely orphan image in an empty
        # container is almost certainly a publisher decorative plate.
        parent = img.parent if isinstance(img.parent, Tag) else None
        if parent is not None:
            parent_text = clean_text(parent.get_text(" "))
            src_text = clean_text(str(img.get("src", "")))
            remaining = parent_text.replace(src_text, "").strip()
            if not remaining and len(parent.find_all("img")) <= 1:
                return False
    return False


def rewrite_images(
    soup: BeautifulSoup,
    src_map: dict[str, str],
    doc: SpineDoc,
    settings: Settings,
    log: BuildLog,
    ai_client=None,
    ai_model: str = "gpt-5.4-mini",
    ai_provider: str = "openai",
) -> None:
    from pathlib import Path
    for img in list(soup.find_all("img")):
        src = img.get("src") or img.get("href") or ""
        norm = normalize_src(src, doc.name)
        mapped = src_map.get(norm) or src_map.get(Path(norm).name)
        if not mapped:
            mapped = src_map.get(src)
        if not mapped:
            mapped = src_map.get(Path(norm).name)
        context = _img_context(img)
        keep = should_keep_image(
            img,
            settings,
            context,
            src,
            ai_client=ai_client,
            ai_model=ai_model,
            ai_provider=ai_provider,
            log=log,
        )
        if not keep:
            block = img.find_parent(["figure", "div", "p", "section"])
            log.removed_images.append((context or src)[:240])
            if block and len(clean_text(block.get_text(" "))) < 700:
                strip_tag(block)
            else:
                strip_tag(img)
            continue
        if not mapped:
            log.warn(f"Image source not found and removed: {src}")
            strip_tag(img)
            continue
        img["src"] = mapped
        img.attrs.pop("href", None)
        log.kept_images.append(f"{mapped}: {context[:160]}")
    remove_orphan_image_captions(soup, log)


def remove_orphan_image_captions(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in list(soup.find_all(["figcaption", "p", "div"])):
        if tag.find("img"):
            continue
        text = clean_text(tag.get_text(" "))
        if not text or len(text) >= 700:
            continue
        is_figcaption = tag.name == "figcaption"
        class_text = (
            " ".join(tag.get("class", []))
            if isinstance(tag.get("class", []), list)
            else str(tag.get("class", ""))
        )
        caption_class = bool(re.search(r"caption|fig|image|photo|plate", class_text, re.I))
        caption_front = clean_text(text[:240])
        caption_like = bool(C.PLATE_CAPTION_PATTERNS.search(caption_front))
        position_caption = (
            bool(re.search(r"\((?:left|right|above|below|centre|center)\)", caption_front, re.I))
            and visible_word_count(text) <= 24
        )
        starts_like_caption = bool(
            re.match(
                r"^(the )?author[’']?s |^(portrait|photo|photograph|frontispiece|illustration|"
                r"birthplace|grave|tomb|statue|monument|museum|translated by)\b",
                caption_front,
                flags=re.I,
            )
        )
        if position_caption:
            log.removed("Removed orphan positioned image caption", text)
            strip_tag(tag)
            continue
        if starts_like_caption:
            pass
        elif _looks_like_real_prose_or_dialogue(text) and not (is_figcaption or caption_class):
            continue
        if caption_like and (is_figcaption or caption_class or starts_like_caption or _looks_like_short_caption(text)):
            log.removed("Removed orphan plate caption", text)
            strip_tag(tag)


def _looks_like_short_caption(text: str) -> bool:
    words = visible_word_count(text)
    if words > 14:
        return False
    if re.search(r"[!?;:]", text):
        return False
    if re.search(r"[,;:]\s+\b(I|he|she|we|they|you|it|my|his|her|their|our)\b", text, re.I):
        return False
    return True


def _looks_like_real_prose_or_dialogue(text: str) -> bool:
    words = visible_word_count(text)
    if words >= 22:
        return True
    if re.search(r"^[\"'â€œâ€˜].{8,}", text):
        return True
    if re.search(r"[\"'â€â€™][,;:.!?]?\s*(said|asked|cried|replied|answered|whispered|exclaimed|thought)\b", text, re.I):
        return True
    if re.search(r"\b(I|you|he|she|we|they|my|your|his|her|our|their)\b", text, re.I) and re.search(r"[.!?]$", text):
        return True
    return False


def remove_empty_layout_shells(soup: BeautifulSoup, log: BuildLog) -> None:
    for tag in reversed(list(soup.find_all(["figure", "div", "section"]))):
        classes = tag.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        if "true-blank" in classes:
            continue
        if tag.find(["img", "svg", "table"]):
            continue
        text = clean_text(tag.get_text(" "))
        if not text:
            strip_tag(tag)
        elif len(text) < 700 and C.PLATE_CAPTION_PATTERNS.search(text):
            log.removed("Removed empty plate shell", text)
            strip_tag(tag)


def remove_duplicate_current_work_title_line(
    soup: BeautifulSoup, current_work: Optional[str], doc: SpineDoc, log: BuildLog
) -> None:
    if not current_work or doc.kind in {"major_work", "play", "backmatter", "division"}:
        return
    body = soup.body or soup
    first = first_significant_tag(body)
    if not isinstance(first, Tag) or first.name not in {"p", "div"}:
        return
    text = clean_text(first.get_text(" "))
    if visible_word_count(text) <= 10 and normalized_title_key(text) == normalized_title_key(current_work):
        log.removed("Removed duplicate current-work title line", text)
        strip_tag(first)


# ======================================================================================
# POETRY / VERSE / DRAMA NORMALIZATION
# ======================================================================================


def split_br_verse_block(tag: Tag, soup: BeautifulSoup, log: BuildLog) -> None:
    """Convert a p/div with <br> lineation into a verse block with line spans."""
    lines: list[str] = []
    cur_text = ""
    for child in list(tag.children):
        if isinstance(child, Tag) and child.name == "br":
            lines.append(clean_text(cur_text))
            cur_text = ""
        else:
            cur_text += child.get_text(" ") if isinstance(child, Tag) else str(child)
    if clean_text(cur_text):
        lines.append(clean_text(cur_text))
    lines = [x for x in lines if x]
    if len(lines) < 2:
        return
    block = soup.new_tag("div")
    block["class"] = ["verse-block"]
    for line in lines:
        span = soup.new_tag("span")
        span["class"] = ["verse-line"]
        span.string = line
        block.append(span)
    tag.replace_with(block)
    log.detected_poetry_blocks += 1


def normalize_poetry(soup: BeautifulSoup, doc: SpineDoc, log: BuildLog) -> None:
    for tag in list(soup.find_all(["p", "div"])):
        if len(tag.find_all("br")) >= 2 and len(clean_text(tag.get_text(" "))) < 2200:
            split_br_verse_block(tag, soup, log)
            continue
        classes = (
            " ".join(tag.get("class", []))
            if isinstance(tag.get("class", []), list)
            else str(tag.get("class", ""))
        )
        if C.POETRY_CLASS_RE.search(classes):
            tag["class"] = list(
                set(
                    (tag.get("class", []) if isinstance(tag.get("class", []), list) else [tag.get("class")])
                    + ["verse-block"]
                )
            )
            log.detected_poetry_blocks += 1

    if not doc.contains_poetry:
        return
    body = soup.body or soup
    run: list[Tag] = []

    def _flush() -> None:
        nonlocal run
        if len(run) >= 4:
            block = soup.new_tag("div")
            block["class"] = ["verse-block", "verse-sequence"]
            for p in run:
                line = clean_text(p.get_text(" "))
                if not line:
                    continue
                span = soup.new_tag("span")
                span["class"] = ["verse-line"]
                span.string = line
                block.append(span)
            run[0].insert_before(block)
            for p in run:
                strip_tag(p)
            log.detected_poetry_sequences += 1
        run = []

    for child in list(body.descendants):
        if not isinstance(child, Tag) or child.name != "p":
            continue
        if child.find_parent(["blockquote", "table", "div"]):
            continue
        t = clean_text(child.get_text(" "))
        if 2 <= len(t) <= 76 and not t.endswith(".") and not C.CHAPTER_HEADINGS.match(t):
            run.append(child)
        else:
            _flush()
    _flush()


# ======================================================================================
# CAST / DRAMA NORMALIZATION
# ======================================================================================


def normalize_cast_and_drama(soup: BeautifulSoup, log: BuildLog) -> None:
    for h in list(soup.find_all(re.compile(r"^h[1-6]$"))):
        text = clean_text(h.get_text(" "))
        if C.CAST_HEADINGS.match(text):
            h["class"] = add_classes(h, ["cast-heading", "formal-opener"])
            log.detected_cast_sections += 1
            cur = h.next_sibling
            while cur is not None:
                nxt = cur.next_sibling
                if isinstance(cur, Tag):
                    if re.match(r"h[1-6]", cur.name or ""):
                        ht = clean_text(cur.get_text(" "))
                        if C.ACT_SCENE_HEADINGS.match(ht):
                            cur["class"] = add_classes(cur, ["act-opening"])
                        break
                    if cur.name in {"p", "div", "ul", "ol", "table"}:
                        cur["class"] = add_classes(cur, ["cast-list"])
                        _normalize_cast_entries(cur, log)
                cur = nxt
        elif C.ACT_SCENE_HEADINGS.match(text):
            h["class"] = add_classes(h, ["act-scene-heading"])
    for p in soup.find_all("p"):
        t = clean_text(p.get_text(" "))
        if re.match(r"^\[.*\]$|^\(.*\)$", t) and len(t) < 240:
            p["class"] = add_classes(p, ["stage-direction"])


def _normalize_cast_entries(container: Tag, log: BuildLog) -> None:
    for el in container.find_all(["p", "li"], recursive=True):
        t = clean_text(el.get_text(" "))
        if not t or len(t) > 240:
            continue
        if re.match(r"^[A-Z][A-Z .'-]{2,35}([—–-]|,|\s{2,})", t) or t.isupper():
            el["class"] = add_classes(el, ["cast-entry"])
            log.normalized_cast_entries += 1


# ======================================================================================
# NOTE / FOOTNOTE NORMALIZATION
# ======================================================================================


def normalize_notes_refs(soup: BeautifulSoup) -> None:
    for a in soup.find_all("a"):
        href = str(a.get("href", ""))
        text = clean_text(a.get_text(" "))
        if ("note" in href.lower() or "fn" in href.lower()) and re.match(r"^\[?\d+\]?$", text):
            sup = soup.new_tag("sup")
            sup["class"] = ["note-ref"]
            sup.string = text.strip("[]")
            a.clear()
            a.append(sup)


# ======================================================================================
# INLINE FOOTNOTE NORMALIZATION
# ======================================================================================


def normalize_inline_footnotes(soup: BeautifulSoup, settings: Settings, log: BuildLog) -> None:
    """Detect and restructure inline footnote bodies in malformed EPUBs.

    Many poorly-constructed EPUBs place footnote bodies directly inline in the
    body text (e.g. ``<p>[1] This is the note.</p>``). This function:
      1. Detects clusters of numbered footnote blocks.
      2. Collects and wraps them in a ``<section class="footnotes">``.
      3. Inserts the section at the first footnote reference point.

    Proper endnote sections (matching BACKMATTER_PATTERNS) are left untouched.
    Set ``footnote_handling = "endnotes-only"`` or ``"disabled"`` to skip.
    """
    if settings.footnote_handling == "disabled":
        return

    body = soup.body or soup
    # Skip documents that are clearly backmatter (endnotes sections the user wants kept)
    is_endnotes_section = bool(
        body.find(["h1", "h2", "h3"], string=lambda t: t and "endnote" in t.lower() or "note" in t.lower())
    ) if settings.footnote_handling == "endnotes-only" else False
    if is_endnotes_section:
        return

    FOOTNOTE_BODY_RE = re.compile(r"^\[(\d+)\](?:\.)?\s+|^(\d+)[.)]\s+")

    candidates: list[Tag] = []
    for tag in list(body.find_all(["p", "div"])):
        if tag.find_parent(["blockquote", "table", "figcaption"]):
            continue
        text = clean_text(tag.get_text(" "))
        if not text:
            continue
        m = FOOTNOTE_BODY_RE.match(text)
        if m:
            candidates.append(tag)

    # Need at least 2 consecutive footnote bodies to treat as a set
    if len(candidates) < 2:
        return

    # Group consecutive candidates into clusters
    clusters: list[list[Tag]] = []
    current_cluster: list[Tag] = []
    for tag in candidates:
        if current_cluster:
            # Check if this tag follows the previous one in document order
            prev = current_cluster[-1]
            node = prev.next_sibling
            found = False
            while node is not None:
                if node is tag:
                    found = True
                    break
                if isinstance(node, Tag) and clean_text(node.get_text(" ")) and node is not tag:
                    break
                node = node.next_sibling
            if found:
                current_cluster.append(tag)
            else:
                if len(current_cluster) >= 2:
                    clusters.append(current_cluster)
                current_cluster = [tag]
        else:
            current_cluster = [tag]
    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    if not clusters:
        return

    for cluster in clusters:
        # Build footnote section
        fn_section = soup.new_tag("section")
        fn_section["class"] = ["footnotes"]
        fn_section["role"] = "doc-footnotes"

        for tag in cluster:
            text = clean_text(tag.get_text(" "))
            m = FOOTNOTE_BODY_RE.match(text)
            if not m:
                continue
            num = m.group(1) or m.group(2)
            # Create footnote element
            fn = soup.new_tag("p")
            fn["class"] = ["footnote"]
            fn["id"] = f"fn-{num}"
            fn["role"] = "doc-footnote"
            # Footnote marker
            marker = soup.new_tag("sup")
            marker["class"] = ["fn-marker"]
            marker.string = num
            fn.append(marker)
            fn.append(" ")
            # Note text (strip the leading number marker)
            note_text = re.sub(r"^\[?\d+\]?[.)]?\s*", "", text, count=1)
            fn.append(note_text)
            fn_section.append(fn)
            # Remove original tag
            strip_tag(tag)

        # Insert the footnote section after the last body paragraph before the cluster,
        # or at the end of the document body
        # Find the last proper paragraph before the first footnote in the cluster
        prev_tag = cluster[0].find_previous_sibling(["p", "div", "section"])
        if prev_tag:
            prev_tag.insert_after(fn_section)
        else:
            body.append(fn_section)

        log.warn(
            f"Restructured {len(cluster)} inline footnote bodies into a footnotes section."
        )


# ======================================================================================
# TYPOGRAPHIC CLEANUP
# ======================================================================================


def simple_typographic_cleanup(soup: BeautifulSoup, log: BuildLog) -> None:
    """Conservative punctuation cleanup. Does not touch pre/code/verse/cast blocks."""
    skip_parents = {"pre", "code", "kbd", "samp"}
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent and parent.name in skip_parents:
            continue
        text = str(node)
        new = text.replace("\xa0", " ")
        new = re.sub(r"[ \t\r\f\v]+", " ", new)
        new = new.replace("...", "…")
        new = re.sub(r"\s+--\s+", " — ", new)
        new = re.sub(r"(?<=\w)--(?=\w)", "—", new)
        new = re.sub(r"(?<=[.!?]) {2,}", " ", new)
        if new != text:
            node.replace_with(new)
            log.typographic_fixes += 1


# ======================================================================================
# HEADING PROMOTION & CLASSIFICATION
# ======================================================================================


def promote_paragraph_headings(soup: BeautifulSoup, log: BuildLog) -> None:
    """Promote disguised headings in <p>/<div> tags to proper <h2>."""
    last_heading: Optional[Tag] = None
    for tag in list(soup.find_all(["p", "div"])):
        if tag.find_parent(["blockquote", "table", "figcaption"]):
            continue
        if tag.find(["img", "svg", "table"]):
            continue
        text = clean_display_title(tag.get_text(" "))
        if not text or len(text) > 80 or visible_word_count(text) > 8:
            continue
        is_chapter_heading = C.CHAPTER_HEADINGS.match(text) is not None
        prev = tag.find_previous_sibling()
        while isinstance(prev, NavigableString) and not clean_text(str(prev)):
            prev = prev.previous_sibling
        follows_heading = isinstance(prev, Tag) and prev.name in {"h1", "h2", "h3", "h4"}
        is_section_label = (
            not is_chapter_heading
            and follows_heading
            and text.isupper()
            and visible_word_count(text) <= 5
            and not re.search(r"[.!?]$", text)
        )
        if not is_section_label:
            is_section_label = (
                not is_chapter_heading
                and last_heading is not None
                and text.isupper()
                and visible_word_count(text) <= 5
                and not re.search(r"[.!?]$", text)
            )
        if not is_chapter_heading and not is_section_label:
            continue
        anchors = tag.find_all("a")
        first_anchor_id = ""
        for a in anchors:
            first_anchor_id = str(a.get("id") or "").strip()
            if first_anchor_id:
                break
        tag.name = "h2"
        classes = ["chapter-section-heading"] if is_section_label else ["subdivision"]
        if is_chapter_heading and re.match(r"^part\b", text, re.I):
            classes.append("part-heading")
        tag["class"] = add_classes(tag, classes)
        if first_anchor_id and not tag.get("id"):
            tag["id"] = first_anchor_id
        tag.clear()
        tag.string = text
        last_heading = tag


def normalize_headings(
    soup: BeautifulSoup,
    doc: SpineDoc,
    toc: list[TocEntry],
    used_ids: set[str],
    current_work: Optional[str],
    current_division: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Classify each heading tag and assign the correct semantic class and TOC entry."""
    doc_major_key = normalized_title_key(doc.major_title)
    doc_division_key = normalized_title_key(doc.current_division)
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        text = clean_display_title(h.get_text(" "))
        if not text:
            strip_tag(h)
            continue
        h.clear()
        h.string = text
        level = int(h.name[1])
        text_key = normalized_title_key(text)
        h_classes = h.get("class", [])
        if isinstance(h_classes, str):
            h_classes = h_classes.split()
        is_chapter_section_heading = "chapter-section-heading" in h_classes
        is_division = (
            C.COLLECTION_DIVISIONS.match(text) is not None
            or (doc.kind == "division" and bool(doc_division_key) and text_key == doc_division_key)
            or (
                doc.kind == "division"
                and bool(doc_major_key)
                and text_key == doc_major_key
                and not re.match(r"^part\s+", text, re.I)
            )
        )
        is_backmatter = C.BACKMATTER_PATTERNS.match(text) is not None or doc.kind == "backmatter"
        is_chapterish = C.CHAPTER_HEADINGS.match(text) is not None
        is_major = False
        if level == 1 and not is_chapterish and not is_chapter_section_heading:
            is_major = True
        if doc_major_key and doc_major_key == text_key and doc.kind in {"major_work", "play", "poetry", "backmatter"}:
            is_major = True
        if level == 2 and current_division and not is_chapterish and not is_chapter_section_heading and len(text) < 90:
            is_major = True
        if is_division:
            current_division = text
            is_major = True
            h["class"] = add_classes(h, ["collection-division", "formal-opener"])
        elif is_backmatter:
            current_work = text
            h["class"] = add_classes(h, ["backmatter-opener", "formal-opener"])
        elif is_major:
            current_work = text
            h["class"] = add_classes(h, ["major-work", "formal-opener"])
        elif is_chapter_section_heading:
            h["class"] = add_classes(h, ["chapter-section-heading"])
        elif level == 2:
            h["class"] = add_classes(h, ["subdivision"])
        else:
            h["class"] = add_classes(h, ["minor-heading"])
        ident = h.get("id") or unique_id(text, used_ids)
        if ident in used_ids:
            ident = unique_id(text, used_ids)
        else:
            used_ids.add(str(ident))
        h["id"] = ident
        if is_division:
            toc.append(TocEntry(1, text, str(ident), "division"))
        elif is_backmatter or is_major:
            toc.append(
                TocEntry(1 if not current_division else 2, text, str(ident), "backmatter" if is_backmatter else "work")
            )
        elif level == 2 and not is_chapterish and not is_chapter_section_heading and len(text) < 90:
            toc.append(TocEntry(3 if current_division else 2, text, str(ident), "work"))
    return current_work, current_division


# ======================================================================================
# SYNTHETIC OPENER & WORK DESCRIPTION DETECTION
# ======================================================================================


def add_synthetic_opener_if_needed(
    soup: BeautifulSoup, doc: SpineDoc, toc: list[TocEntry], used_ids: set[str], current_work: Optional[str]
) -> Optional[str]:
    """Insert a synthetic <h1> when the AI-assigned major_title is not already in the HTML."""
    if not doc.major_title:
        return current_work
    title = clean_display_title(doc.major_title)
    title_key = normalized_title_key(title)
    current_key = normalized_title_key(current_work)
    if current_key and current_key == title_key:
        return current_work
    if doc.kind not in {"major_work", "play", "backmatter"}:
        return current_work or title
    first_text = clean_text((soup.body or soup).get_text(" "))[:350].lower()
    if title.lower() in first_text:
        return current_work or title
    ident = unique_id(title, used_ids)
    h = soup.new_tag("h1")
    h["class"] = ["major-work", "formal-opener", "synthetic-opener"]
    h["id"] = ident
    h.string = title
    body = soup.body or soup
    body.insert(0, h)
    toc.append(TocEntry(1, title, ident, "work"))
    return title


def _looks_like_work_description(text: str, heading_text: str) -> bool:
    """Detect Delphi-style editorial blurbs after major work titles."""
    s = clean_text(text)
    if not s:
        return False
    words = visible_word_count(s)
    if words < 18 or words > 260:
        return False
    if re.match(r"^[\"'\"\u201c\u2018\u2014\u2013]", s):
        return False
    if re.search(r"\b(CHAPTER|BOOK|PART|SCENE|ACT)\b", s[:80]):
        return False
    heading_key = normalized_title_key(heading_text)
    text_key = normalized_title_key(s)
    heading_words = [w for w in heading_key.split() if len(w) > 2]
    title_mentioned = bool(heading_words and all(w in text_key for w in heading_words[: min(3, len(heading_words))]))
    editorial_vocab = re.search(
        r"\b(was|were|is|are|published|appeared|written|wrote|composed|completed|"
        r"novel|novella|story|tale|poem|play|drama|work|collection|translated|"
        r"first|last|inspired|based|deals with|concerns|tells|features)\b",
        s,
        re.I,
    )
    bibliographic_marker = re.search(
        r"\b(1[5-9]\d{2}|20\d{2}|Dostoevsky|Dostoyevsky|Tolstoy|Dickens|Balzac|Poe|Gogol|Turgenev)\b", s
    )
    return bool(editorial_vocab and (title_mentioned or bibliographic_marker))


def mark_major_work_descriptions(soup: BeautifulSoup, log: BuildLog) -> None:
    """Style editorial blurbs after major work titles as smaller italic apparatus."""
    body = soup.body or soup
    marked = 0
    for heading in list(
        body.find_all(["h1", "h2"], class_=lambda c: c and "major-work" in str(c).split())
    ):
        node = heading.find_next_sibling()
        heading_text = clean_text(heading.get_text(" "))
        subtitle_text = ""
        inspected = 0
        while node is not None and inspected < 4:
            while isinstance(node, NavigableString) and not clean_text(str(node)):
                node = node.next_sibling
            if not isinstance(node, Tag):
                break
            text = clean_text(node.get_text(" "))
            if not text:
                node = node.next_sibling
                continue
            inspected += 1
            words = visible_word_count(text)
            if (
                node.name in {"h2", "h3", "h4"}
                and not subtitle_text
                and words <= 8
                and not re.search(r"\b(CHAPTER|BOOK|PART|ACT|SCENE)\b", text, re.I)
            ):
                node["class"] = add_classes(node, ["work-subtitle", "no-indent"])
                subtitle_text = text
                node = node.next_sibling
                continue
            if node.name != "p":
                break
            is_short_subtitle = (
                words <= 8
                and not _looks_like_work_description(text, heading_text)
                and not re.search(r"\b(CHAPTER|BOOK|PART|ACT|SCENE)\b", text, re.I)
            )
            if is_short_subtitle and not subtitle_text:
                node["class"] = add_classes(node, ["work-subtitle", "no-indent"])
                subtitle_text = text
                node = node.next_sibling
                continue
            if _looks_like_work_description(text, f"{heading_text} {subtitle_text}".strip()):
                node["class"] = add_classes(node, ["work-description", "editorial-description", "no-indent"])
                marked += 1
            break
    if marked:
        log.warn(f"Styled {marked} post-opener editorial description paragraph(s).")


def _looks_like_supplemental_work_description(text: str) -> bool:
    """Detect short standalone editorial notes split into a separate EPUB file."""
    s = clean_text(text)
    words = visible_word_count(s)
    if words < 8 or words > 90:
        return False
    if re.match(r"^[\"'\"\u201c\u2018\u2014\u2013]", s):
        return False
    if re.search(r"\b(CHAPTER|BOOK|PART|SCENE|ACT)\b", s[:80], re.I):
        return False
    if re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)\b", s):
        return False
    if re.search(r"\((?:left|right|above|below|centre|center)\)|\b(?:left|right|above|below):", s, re.I):
        return False
    return bool(
        re.search(
            r"\b(author|translator|editor|published|publication|unfinished|fragment|prologue|"
            r"novel|novella|story|poem|play|sketch|exile|arrest|execution|Siberia)\b",
            s,
            re.I,
        )
    )


def mark_standalone_work_description_fragment(
    soup: BeautifulSoup, current_work: Optional[str], doc: SpineDoc, log: BuildLog
) -> None:
    if not current_work or doc.kind not in {"chapter", "major_work", "unknown"}:
        return
    body = soup.body or soup
    if body.find(["h1", "h2", "h3", "h4", "table", "img", "svg"]):
        return
    paragraphs = [p for p in body.find_all("p", recursive=False) if clean_text(p.get_text(" "))]
    if not paragraphs or len(paragraphs) > 2:
        return
    total_text = clean_text(" ".join(p.get_text(" ") for p in paragraphs))
    if not _looks_like_supplemental_work_description(total_text):
        return
    for p in paragraphs:
        p["class"] = add_classes(p, ["work-description", "editorial-description", "no-indent"])
    log.warn(f"Styled standalone editorial description fragment for {current_work}.")


# ======================================================================================
# OPENER SEPARATORS
# ======================================================================================


def insert_major_opener_separators(soup: BeautifulSoup, settings: Settings, log: BuildLog) -> None:
    if not settings.major_opener_blank_before and not settings.major_opener_blank_after:
        return
    body = soup.body or soup
    for heading in list(
        body.find_all(
            ["h1", "h2"],
            class_=lambda c: c
            and any(x in str(c).split() for x in ["major-work", "collection-division", "backmatter-opener"]),
        )
    ):
        if settings.major_opener_blank_before:
            before = soup.new_tag("div")
            before["class"] = ["true-blank", "major-opener-separator", "before-major-opener"]
            heading.insert_before(before)
        if settings.major_opener_blank_after:
            after = soup.new_tag("div")
            after["class"] = ["true-blank", "major-opener-separator", "after-major-opener"]
            heading.insert_after(after)
    if settings.major_opener_blank_before or settings.major_opener_blank_after:
        log.warn("Inserted no-folio blank separators around major work/division opener pages.")


# ======================================================================================
# DROP CAPS
# ======================================================================================


def mark_drop_caps(soup: BeautifulSoup, settings: Settings, log: BuildLog) -> None:
    """Wrap the first letter of the first paragraph after chapter headings.

    Only applies to ordinary chapter start paragraphs (not major work openers,
    cast lists, poetry, or other special-form tags).
    """
    if not settings.drop_caps:
        return
    body = soup.body or soup
    # Find chapter-level headings (subdivision = ordinary chapter heading)
    for heading in list(body.find_all(["h1", "h2", "h3"], class_=lambda c: c and "subdivision" in str(c).split())):
        node = heading.find_next_sibling()
        inspected = 0
        while node is not None and inspected < 6:
            while isinstance(node, NavigableString) and not clean_text(str(node)):
                node = node.next_sibling
            if not isinstance(node, Tag):
                break
            inspected += 1
            if node.name != "p":
                node = node.next_sibling
                continue
            text = clean_text(node.get_text(" "))
            if not text or text.startswith('"') or text.startswith("'") or text.startswith("\u201c"):
                node = node.next_sibling
                continue
            # Check it's not already a special class
            classes = " ".join(node.get("class", [])) if isinstance(node.get("class", []), list) else str(node.get("class", ""))
            if any(x in classes for x in ["work-description", "work-subtitle", "no-indent", "stage-direction", "cast-list"]):
                node = node.next_sibling
                continue
            # Get the first actual letter
            full_text = "".join(node.strings)
            stripped = full_text.lstrip()
            if not stripped:
                break
            first_char = stripped[0]
            if not first_char.isalpha():
                break
            # Wrap the first letter in a drop-cap span
            original_html = str(node)
            # Replace first occurrence of first_char in the rendered text
            # Use a regex that matches the first letter in the HTML
            import re as _re
            new_html = _re.sub(
                r"\b(" + _re.escape(first_char) + r")",
                r'<span class="drop-cap">\1</span>',
                original_html,
                count=1,
            )
            if new_html != original_html:
                new_soup = BeautifulSoup(new_html, "lxml")
                new_tag = new_soup.find(node.name)
                if new_tag:
                    node.replace_with(new_tag)
            break


# ======================================================================================
# SMALL CAPS NORMALIZATION
# ======================================================================================


def normalize_small_caps(soup: BeautifulSoup, settings: Settings, log: BuildLog) -> None:
    """Wrap known small-caps abbreviations in <span class="small-caps">.

    Common small caps: AM, PM, BC, AD, BCE, CE, NB, PS, etc.
    Also handles dotted abbreviations like A.M., P.M., B.C., A.D.
    """
    if not settings.small_caps:
        return

    small_caps_abbr = re.compile(
        r"\b("
        r"[Aa]\.?[Mm]\.?(?=\s|\.|,|;|:)|"       # AM / A.M.
        r"[Pp]\.?[Mm]\.?(?=\s|\.|,|;|:)|"       # PM / P.M.
        r"[Bb]\.?[Cc]\.?(?=\s|\.|,|;|:)|"       # BC / B.C.
        r"[Aa]\.?[Dd]\.?(?=\s|\.|,|;|:)|"       # AD / A.D.
        r"[Bb][Cc][Ee](?=\s|\.|,|;|:)|"          # BCE
        r"[Cc][Ee](?=\s|\.|,|;|:)|"              # CE
        r"[Nn]\.?[Bb]\.?(?=\s|\.|,|;|:)|"       # NB / N.B.
        r"[Pp]\.?[Ss]\.?(?=\s|\.|,|;|:)"        # PS / P.S.
        r")\b",
    )

    skip_parents = {"pre", "code", "kbd", "samp", "h1", "h2", "h3", "h4", "h5", "h6", "title"}
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent and parent.name in skip_parents:
            continue
        text = str(node)
        new_text = small_caps_abbr.sub(r'<span class="small-caps">\1</span>', text)
        if "<span" in new_text:
            new_soup = BeautifulSoup(f"<span>{new_text}</span>", "lxml")
            replacement = new_soup.span
            if replacement:
                replacement = replacement.extract()
                node.replace_with(replacement)



# ======================================================================================
# TITLE-ONLY FRAGMENT DETECTION
# ======================================================================================


def fragment_is_title_only(frag: str, settings: Settings) -> bool:
    soup = BeautifulSoup(frag, "lxml")
    if soup.find(["img", "svg", "table"]):
        return False
    text = clean_text(soup.get_text(" "))
    if not text:
        return True
    words = visible_word_count(text)
    key = normalized_title_key(text)
    title_key = normalized_title_key(settings.title)
    if title_key and key == title_key and words <= 14:
        return True
    if title_key and key.startswith(title_key) and words <= 18:
        tail = key[len(title_key) :].strip()
        if not tail or tail in {"a novel", "novel", "book"}:
            return True
    bodyish = [clean_text(x.get_text(" ")) for x in soup.find_all(["p", "blockquote", "li"])]
    bodyish = [x for x in bodyish if x]
    if words <= 20 and not any(visible_word_count(x) > 10 for x in bodyish):
        return True
    return False


# ======================================================================================
# MAIN DOCUMENT CLEANUP ORCHESTRATOR
# ======================================================================================


def clean_document(
    doc: SpineDoc,
    src_map: dict[str, str],
    settings: Settings,
    toc: list[TocEntry],
    used_ids: set[str],
    current_work: Optional[str],
    current_division: Optional[str],
    log: BuildLog,
    ai_client=None,
    ai_model: str = "gpt-5.4-mini",
    ai_provider: str = "openai",
) -> tuple[str, Optional[str], Optional[str]]:
    """Apply the full cleanup pipeline to one EPUB spine document.

    Returns (HTML fragment, updated current_work, updated current_division).
    """

    soup = parse_html(doc.raw)
    remove_comments_scripts_styles(soup)
    strip_bad_attributes(soup)
    remove_local_mini_tocs(soup, log)
    remove_compact_local_contents_blocks(soup, log)
    remove_promotional_blocks(soup, log)
    rewrite_images(
        soup,
        src_map,
        doc,
        settings,
        log,
        ai_client=ai_client,
        ai_model=ai_model,
        ai_provider=ai_provider,
    )
    remove_empty_layout_shells(soup, log)
    unwrap_useless_inline_tags(soup)
    if settings.smart_punctuation:
        simple_typographic_cleanup(soup, log)
    normalize_notes_refs(soup)
    normalize_inline_footnotes(soup, settings, log)
    normalize_poetry(soup, doc, log)
    remove_compact_local_contents_blocks(soup, log)
    normalize_cast_and_drama(soup, log)
    promote_paragraph_headings(soup, log)
    current_work = add_synthetic_opener_if_needed(soup, doc, toc, used_ids, current_work)
    current_work, current_division = normalize_headings(soup, doc, toc, used_ids, current_work, current_division)
    mark_major_work_descriptions(soup, log)
    insert_major_opener_separators(soup, settings, log)
    mark_drop_caps(soup, settings, log)
    normalize_small_caps(soup, settings, log)
    remove_duplicate_current_work_title_line(soup, current_work, doc, log)
    remove_empty_layout_shells(soup, log)
    mark_standalone_work_description_fragment(soup, current_work, doc, log)

    body = soup.body or soup
    for tag in list(body.find_all(["p", "div"])):
        classes = tag.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        if "true-blank" in classes:
            continue
        if tag.find(["img", "svg", "table", "span"]):
            continue
        if not clean_text(tag.get_text(" ")):
            strip_tag(tag)

    wrapper_classes = ["epub-doc"]
    if doc.kind:
        wrapper_classes.append(f"doc-{doc.kind}")
    first_tag = first_significant_tag(body)
    if isinstance(first_tag, Tag):
        first_classes = first_tag.get("class", [])
        if isinstance(first_classes, str):
            first_classes = first_classes.split()
        if "major-work" in first_classes or "collection-division" in first_classes or "backmatter-opener" in first_classes:
            wrapper_classes.append("starts-major-work")
        elif first_tag.name in {"h1", "h2", "h3"}:
            ft = clean_text(first_tag.get_text(" "))
            if C.CHAPTER_HEADINGS.match(ft) or "subdivision" in first_classes:
                wrapper_classes.append("starts-chapter-opener")
    if doc.contains_poetry:
        wrapper_classes.append("contains-poetry")
    if doc.contains_drama:
        wrapper_classes.append("contains-drama")
    direct_content_tags = [
        child
        for child in body.children
        if isinstance(child, Tag) and clean_text(child.get_text(" "))
    ]
    if direct_content_tags and all(
        "work-description"
        in (
            child.get("class", [])
            if not isinstance(child.get("class", []), str)
            else child.get("class", "").split()
        )
        for child in direct_content_tags
    ):
        wrapper_classes.append("editorial-description-fragment")
    attrs = f'class="{" ".join(wrapper_classes)}"'
    if current_work:
        attrs += f' data-current-work="{html_escape(current_work)}"'
    frag = "\n".join(str(child) for child in body.children if str(child).strip())
    current_marker = (
        f'<span class="set-current-work">{html_escape(current_work)}</span>\n'
        if current_work and "starts-major-work" not in wrapper_classes
        else ""
    )
    import html as html_module
    return (
        f'<!-- source: {html_module.escape(doc.href)} -->\n<section {attrs}>\n{current_marker}{frag}\n</section>',
        current_work,
        current_division,
    )


def html_escape(s: Optional[str]) -> str:
    """Safely escape a string for HTML attribute insertion."""
    import html as html_module

    if s is None:
        return ""
    return html_module.escape(s)


def sample_word_budget(sample_pages: int) -> int:
    """Approximate body words needed for a print sample."""
    if sample_pages <= 0:
        return 0
    return max(6000, sample_pages * 700)
