"""
AI integration for the EPUB-to-PDF pipeline.

Supports OpenAI and DeepSeek providers for:
- Book structure planning (spine document classification)
- Image classification (functional vs plate)
- Visual QA (rendered page review via vision model)
- Text QA (post-build structural review with regex rule suggestions)
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from pipeline import _constants as C
from pipeline._models import BuildLog, QAVerdict, Settings, SpineDoc
from pipeline._render import expected_trim_size_points, render_selected_pages
from pipeline._rule_packs import extract_review_rule_suggestions, write_review_rule_suggestions
from pipeline._utils import clean_text


# ======================================================================================
# Client management
# ======================================================================================


def require_openai_client():
    try:
        from openai import OpenAI
    except Exception as exc:
        raise SystemExit("Install OpenAI support with: pip install openai\n" + str(exc))
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. PowerShell: $env:OPENAI_API_KEY='sk-...' ")
    return OpenAI()


def require_deepseek_client():
    try:
        from openai import OpenAI
    except Exception as exc:
        raise SystemExit("Install OpenAI-compatible client support with: pip install openai\n" + str(exc))
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is not set. PowerShell: $env:DEEPSEEK_API_KEY='sk-...' ")
    return OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")


def require_ai_client(provider: str):
    if provider == "none":
        return None
    if provider == "deepseek":
        return require_deepseek_client()
    return require_openai_client()


# ======================================================================================
# JSON extraction
# ======================================================================================


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as original_exc:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        raise original_exc


def openai_json(
    client, model: str, system: str, user: str, schema: dict[str, Any], name: str, provider: str = "openai"
) -> dict[str, Any]:
    if provider == "deepseek":
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system + " Return only valid JSON."},
                {
                    "role": "user",
                    "content": user + "\n\nJSON schema:\n" + json.dumps(schema, ensure_ascii=False),
                },
            ],
            response_format={"type": "json_object"},
        )
        return extract_json(resp.choices[0].message.content or "{}")
    try:
        resp = client.responses.create(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        )
        return extract_json(resp.output_text)
    except Exception:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system + " Return only valid JSON."},
                {"role": "user", "content": user + "\n\nJSON schema:\n" + json.dumps(schema)},
            ],
        )
        return extract_json(resp.output_text)


# ======================================================================================
# Book structure planning
# ======================================================================================


def apply_openai_book_plan(
    client, model: str, docs: list[SpineDoc], title: str, log: BuildLog, batch_size: int = 24, provider: str = "openai"
) -> None:
    from pipeline._models import SECTION_SCHEMA

    system = (
        "You are a conservative professional book-structure classifier for an EPUB-to-print-PDF pipeline. "
        "You must preserve legitimate book text. Remove only obvious promotional/catalogue/ebook-navigation junk. "
        "Classify front matter, body divisions, major works, chapters, poetry, plays/cast lists, and back matter. "
        "Do not classify ordinary chapter names as running-head major works."
    )
    for offset in range(0, len(docs), batch_size):
        batch = docs[offset : offset + batch_size]
        parts = []
        for d in batch:
            parts.append(
                {
                    "index": d.index,
                    "href": d.href,
                    "headings": d.headings[:20],
                    "text_sample": d.text_sample[:3500],
                    "text_length": d.text_length,
                    "local_contains_poetry": d.contains_poetry,
                    "local_contains_drama": d.contains_drama,
                    "local_contains_images": d.contains_images,
                }
            )
        user = (
            f"Book title: {title}\n"
            "Classify these EPUB spine documents for deluxe print-book conversion. Return JSON only.\n"
            + json.dumps(parts, ensure_ascii=False)
        )
        try:
            result = openai_json(client, model, system, user, SECTION_SCHEMA, "book_structure_plan", provider=provider)
        except Exception as exc:
            log.warn(
                f"{provider.title()} book-plan classification failed for batch starting {offset}: {exc}"
            )
            continue
        by_index = {int(x.get("index")): x for x in result.get("documents", []) if "index" in x}
        for d in batch:
            x = by_index.get(d.index)
            if not x:
                continue
            d.kind = str(x.get("kind") or d.kind)
            d.remove = bool(x.get("remove_document")) and float(x.get("confidence", 0)) >= 0.86
            d.major_title = clean_text(x.get("major_title")) or d.major_title
            d.current_division = clean_text(x.get("current_division")) or d.current_division
            d.contains_poetry = bool(x.get("contains_poetry", d.contains_poetry))
            d.contains_drama = bool(x.get("contains_drama_or_cast", d.contains_drama))
            d.confidence = float(x.get("confidence", 0) or 0)
            d.notes = str(x.get("notes") or "")
            log.ai_decisions.append(
                f"{d.index} {d.href}: kind={d.kind} remove={d.remove} major={d.major_title!r} "
                f"division={d.current_division!r} conf={d.confidence:.2f} {d.notes}"
            )


# ======================================================================================
# Image classification
# ======================================================================================


def ai_image_decision(
    client, model: str, src: str, context: str, provider: str = "openai"
) -> dict[str, Any]:
    from pipeline._models import IMAGE_SCHEMA

    system = (
        "You classify EPUB images for a print-book pipeline. Keep authorial/functionally necessary images: "
        "maps, diagrams, charts, symbols, runes, inscriptions, facsimiles, image-texts, and images directly "
        "referenced by surrounding text. Remove publisher-added plates, portraits, unrelated illustrations, "
        "catalogue/promotional images, and orphan captions. Be conservative when uncertain."
    )
    user = f"Image src: {src}\nContext/caption around the image:\n{context[:3000]}"
    return openai_json(client, model, system, user, IMAGE_SCHEMA, "image_decision", provider=provider)


# ======================================================================================
# Visual QA
# ======================================================================================


def openai_visual_qa(
    client,
    model: str,
    pdf_path: Path,
    qa_json: Path,
    qa_dir: Path,
    max_pages: int,
    settings: Optional[Settings] = None,
) -> Path:
    images = render_selected_pages(pdf_path, qa_dir, prefix="openai_page", max_pages=max_pages, jpg=True)
    report_excerpt = (
        qa_json.read_text(encoding="utf-8", errors="ignore")[:12000] if qa_json.exists() else ""
    )
    expected_width_pt, expected_height_pt, expected_trim_label = expected_trim_size_points(settings)
    prompt = (
        "Review these rendered pages from a deluxe print-book PDF as a strict book-production QA inspector.\n"
        f"Expected trim from the active config: {expected_trim_label} "
        f"({expected_width_pt:.2f} x {expected_height_pt:.2f} pt). "
        "Judge page size and geometry against that configured trim, not against A4 unless the trim is A4. "
        "If the local QA JSON contains a field named non_a4_pages, treat it as a legacy field meaning "
        "'pages not matching the configured trim.'\n\n"
        "Check all of these prompt requirements:\n"
        "1. Body typography: prose paragraphs should be fully justified, not ragged-right, except legitimate poetry, "
        "drama, TOC, headings, captions, and front matter. Watch for loose rivers, bad word spacing, broken words, "
        "single-letter line spills, and narrow/overwide columns.\n"
        "2. Paragraph rhythm: first-line indents should be consistent, prose should not have ebook-like blank gaps "
        "between ordinary paragraphs, and paragraphs should not collide with headings, runners, folios, or page edges.\n"
        "3. Chapter and work titles: major work openers should be centered and placed with deliberate vertical space; "
        "ordinary chapter titles should be centered with proper space before/after, not blue/underlined, not inline "
        "with body text, and not stranded at the bottom of a page.\n"
        "4. Running heads and rules: body pages should have one clean rule, enough clearance from body text, no "
        "crowding/collision, and correct alternating logic: collection title on verso/left pages, current work/chapter "
        "on recto/right pages where applicable. Title pages, blank pages, and major openers should not show "
        "inappropriate runners.\n"
        "5. Folios/page numbers: front matter and body numbering should look intentional, centered, unobtrusive, "
        "and absent from true blanks/title pages where expected.\n"
        "6. Contents/TOC: generated contents should look print-native, with no blue hyperlinks or underlines, no "
        "duplicate local mini-TOCs, and page numbers/leaders aligned cleanly.\n"
        "7. Image and plate cleanup: publisher-added portraits, decorative plates, catalogue pages, and orphan "
        "captions should be gone; authorial or functional images, if present, should not be cropped or oversized.\n"
        "8. Poetry/drama/special forms: verse lineation, hanging indents, cast lists, stage directions, letters, "
        "and block quotes should look intentional rather than flattened into ordinary prose.\n"
        "9. Ebook artifacts: flag raw ebook layout, colored links, browser-like styling, bad CSS remnants, dark/black "
        "pages, accidental blank/title-only pages, cropped text, or anything that looks non-print-ready.\n\n"
        "Output exactly this structure:\n"
        "FINAL: PASS or FAIL\n"
        "SUMMARY: one short paragraph\n"
        "FINDINGS:\n"
        "- Page N: [category] issue and suggested fix\n"
        "If there are no issues, write '- None'.\n"
        "CHECKED:\n"
        "- Body justification: OK or ISSUE\n"
        "- Chapter/title placement: OK or ISSUE\n"
        "- Running heads/rules: OK or ISSUE\n"
        "- Folios/page numbers: OK or ISSUE\n"
        "- TOC: OK or ISSUE\n"
        "- Image cleanup: OK or ISSUE\n"
        "- Poetry/drama/special forms: OK or ISSUE\n"
        "- Ebook artifacts: OK or ISSUE\n\n"
        "Do not mark an item as ISSUE unless the rendered pages visibly show a real problem. "
        "Give page-specific findings and a final PASS/FAIL.\n\n"
        "Local QA JSON excerpt:\n" + report_excerpt
    )
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for img in images:
        b64 = base64.b64encode(img.read_bytes()).decode("utf-8")
        content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})
    resp = client.responses.create(model=model, input=[{"role": "user", "content": content}])
    out = qa_dir.parent / "openai_visual_qa.txt"
    out.write_text(resp.output_text, encoding="utf-8")
    return out


def openai_visual_issue_lines(visual_text: str) -> list[str]:
    issue_terms = re.compile(
        r"\b(FAIL|ISSUE|problem|warning|collision|crowd(?:ed|ing)?|touch(?:es|ing)?|"
        r"misalign(?:ed|ment)?|wrong|broken|spill|overwide|narrow|ragged|unjustified|"
        r"not justified|stranded|orphan|duplicate|blue|underlined|raw ebook|artifact|"
        r"blank|empty|dark|black|cropped|oversized|missing|inappropriate)\b",
        re.I,
    )
    ok_terms = re.compile(
        r"\b(OK|PASS|passes|acceptable|clean|fine|none|no issues?|no problems?|not observed)\b", re.I
    )
    lines: list[str] = []
    for raw_line in visual_text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        if not issue_terms.search(line):
            continue
        if ok_terms.search(line) and not re.search(r"\bFAIL\b|\bISSUE\b", line, re.I):
            continue
        lines.append(line)
    return lines


def ai_text_issue_lines(report_text: str) -> list[str]:
    issue_terms = re.compile(
        r"\b(FAIL|ISSUE|problem|warning|duplicate|missing|wrong|residue|artifact|"
        r"promo|publisher|caption|image|TOC|contents|chapter|heading|folio|page number|"
        r"publisher-apparatus|vendor|boilerplate|catalogue|catalog|delphi classics|"
        r"project gutenberg|gutenberg|subscribe|newsletter|ISBN|eBook|delphiclassics|"
        r"ragged|unjustified|not justified|line spill|single-letter|blank|empty|raw ebook|"
        r"blue|underlined|hyperlink|poetry|verse|drama|cast|stage direction)\b",
        re.I,
    )
    ok_terms = re.compile(
        r"\b(OK|PASS|passes|acceptable|clean|fine|none|no issues?|no problems?|not observed)\b", re.I
    )
    lines: list[str] = []
    for raw_line in report_text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        if not issue_terms.search(line):
            continue
        if ok_terms.search(line) and not re.search(r"\bFAIL\b|\bISSUE\b", line, re.I):
            continue
        lines.append(line)
    return lines


def add_visual_flag(verdict: QAVerdict, flag: str) -> None:
    if flag not in verdict.openai_visual_flags:
        verdict.openai_visual_flags.append(flag)


def add_text_qa_flag(verdict: QAVerdict, flag: str) -> None:
    if flag not in verdict.text_qa_flags:
        verdict.text_qa_flags.append(flag)


def visual_feedback_text(verdict: QAVerdict) -> str:
    return "\n".join(
        verdict.openai_visual_flags
        + verdict.openai_visual_issue_lines
        + verdict.text_qa_flags
        + verdict.text_qa_issue_lines
    )


def has_visual_feedback(verdict: QAVerdict, pattern: str) -> bool:
    return re.search(pattern, visual_feedback_text(verdict), re.I) is not None


# ======================================================================================
# PDF text extraction for AI
# ======================================================================================


def extract_pdf_text_for_ai(pdf_path: Path, max_pages: int = 50,
                            extra_pages: Optional[list[int]] = None) -> str:
    """Extract text from the first *max_pages* sequential pages, plus any
    *extra_pages* (zero-based PDF page indices) that fall beyond the sequential
    range. This ensures work-opening pages are sampled regardless of book length.
    """
    import fitz

    doc = fitz.open(pdf_path)
    chunks: list[str] = []
    seen: set[int] = set()
    try:
        # Sequential pages
        for i in range(min(doc.page_count, max_pages)):
            text = clean_text(doc[i].get_text("text"))
            if text:
                chunks.append(f"--- PAGE {i + 1} ---\n{text[:3500]}")
                seen.add(i)

        # Extra work-start pages beyond the sequential range
        if extra_pages:
            for i in extra_pages:
                if i in seen or i >= doc.page_count:
                    continue
                text = clean_text(doc[i].get_text("text"))
                if text:
                    chunks.append(f"--- PAGE {i + 1} (work start) ---\n{text[:3500]}")
                seen.add(i)
    finally:
        doc.close()
    return "\n\n".join(chunks)[:60000]


def chat_text(client, model: str, system: str, user: str, provider: str) -> str:
    if provider == "deepseek":
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""
    resp = client.responses.create(
        model=model,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.output_text


# ======================================================================================
# Text QA
# ======================================================================================


def ai_text_qa(
    client,
    model: str,
    provider: str,
    pdf_path: Path,
    qa_json: Path,
    qa_txt: Path,
    artifact_dir: Path,
    settings: Settings,
    log: BuildLog,
    max_pages: int = 50,
    extra_pages: Optional[list[int]] = None,
) -> tuple[Path, Optional[Path]]:
    page_text = extract_pdf_text_for_ai(pdf_path, max_pages=max_pages, extra_pages=extra_pages)
    qa_report = qa_txt.read_text(encoding="utf-8", errors="ignore")[:20000] if qa_txt.exists() else ""
    qa_verdict = qa_json.read_text(encoding="utf-8", errors="ignore")[:20000] if qa_json.exists() else ""
    system = (
        "You are a conservative text-and-structure QA reviewer for a deluxe EPUB-to-print-PDF pipeline. "
        "You cannot see rendered page images. Review only the provided local QA, extracted PDF text, "
        "removed-block logs, and settings. Focus on textual/structural problems and regex cleanup "
        "opportunities. Do not invent visual claims."
    )
    user = (
        "Review this generated book after local deterministic QA has already run.\n\n"
        "Return this structure:\n"
        "FINAL: PASS or FAIL\n"
        "SUMMARY: short paragraph\n"
        "FINDINGS:\n- Page/area: [category] issue and suggested fix, or '- None'\n"
        "AUTO_FIXABLE_SIGNALS:\n"
        "- Use these words only when visibly supported by extracted text/local QA: body-typography, "
        "chapter/title placement, TOC spacing/page-number, runner/header clearance, folio/page-numbering, "
        "ebook artifact, publisher-apparatus, image/caption cleanup, poetry/drama/special-form.\n"
        "- For safe layout tuning, say ISSUE with one of those categories. For structural cleanup requiring "
        "regex rules, put the proposed pattern in REGEX_RULE_SUGGESTIONS.\n"
        "- Editorial work descriptions after a major work title or subtitle should be styled as smaller italic "
        "apparatus, separated from the author's text. Since you cannot see visual italics in extracted text, "
        "**independently scan the extracted PDF text** for paragraphs that look like editorial introductions:\n"
        "  - Text patterns: 'was first published', 'deals with', 'is a novel/novella/story/play/poem', "
        "'was written', 'appeared in', 'this work', 'the novel', 'the story', 'the present work'\n"
        "  - These typically appear right after a work title heading and before 'CHAPTER I' or similar.\n"
        "  - If you find one, flag it with category 'work-description-style' even if the local QA didn't.\n"
        "    Say: 'Page X: editorial work description for [Work Name] may not be italic/smaller.'\n"
        "- Independently audit for publisher/vendor apparatus that should never appear in the final book body or TOC. "
        "Flag category 'publisher-apparatus' if extracted text, TOC text, removed-log gaps, or local QA suggest surviving "
        "Project Gutenberg boilerplate/license text, Delphi Classics material, publisher catalogues/catalogs, copyright/vendor "
        "pages, sales blurbs such as 'Interested in...', 'comprehensive editions', 'bonus texts', 'Explore our wide range', "
        "social-media links, store links, ISBN/app/eBook marketing, newsletter/subscribe prompts, or web URLs such as "
        "delphiclassics.com. Do not flag ordinary authorial uses of words like 'catalogue' or the ancient place 'Delphi' "
        "inside the literary/philosophical text unless it is clearly publisher/vendor apparatus.\n"
        "- Treat frontmatter clutter as blocker-class when supported by extracted text/local QA: generated pages saying "
        "'No reliable table of contents could be inferred', TOCs with only one trivial entry, duplicate title-only pages, "
        "body pages that contain only a runner/title/folio, source mini-contents made of roman numerals, and Project "
        "Gutenberg START/END markers, and transcriber/source-production notes such as 'Transcriber's Note', "
        "'Produced by', 'replicate this text', or 'non-standard spelling'. These should be reported as ISSUE even in short books.\n"
        "- For REGEX_RULE_SUGGESTIONS, prefer stable publisher/source-layout markers such as Project Gutenberg START/END "
        "lines, pg-header, Delphi catalogue phrases, or compact roman-numeral mini-contents. Do not propose broad "
        "book-title-specific patterns as generic cleanup rules unless the pattern also contains a source-layout marker.\n"
        "REGEX_RULE_SUGGESTIONS:\n"
        "Provide a fenced yaml block named rule_suggestions using only these optional keys: "
        + ", ".join(C.RULE_PACK_KEYS)
        + ". Include only high-confidence patterns that would help future EPUB cleanup. Do not include broad "
        "patterns likely to remove real literature.\n\n"
        f"Expected trim from the active config: {settings.trim_size}. "
        "Judge page-size and layout expectations against this configured trim, not against A4 unless trim_size is A4. "
        "If the local QA JSON contains a field named non_a4_pages, treat it as a legacy field meaning "
        "'pages not matching the configured trim.'\n\n"
        "Current settings excerpt:\n" + json.dumps(dataclass_dict(settings), ensure_ascii=False)[:12000]
        + "\n\nLocal QA report:\n" + qa_report
        + "\n\nLocal QA verdict JSON:\n" + qa_verdict
        + "\n\nRemoved documents:\n" + "\n".join(log.removed_documents[:80])
        + "\n\nRemoved blocks:\n" + "\n".join(log.removed_blocks[:80])
        + "\n\nRemoved images:\n" + "\n".join(log.removed_images[:80])
        + "\n\nExtracted PDF page text sample:\n" + page_text
    )
    report_text = chat_text(client, model, system, user, provider)
    report_path = artifact_dir / f"{provider}_text_qa.txt"
    report_path.write_text(report_text, encoding="utf-8")
    suggestions_path: Optional[Path] = None
    if settings.write_ai_rule_suggestions:
        suggestions = extract_review_rule_suggestions(report_text)
        if suggestions:
            suggestions_path = artifact_dir / f"{provider}_rule_suggestions.review.yaml"
            write_review_rule_suggestions(suggestions_path, suggestions, report_path)
    return report_path, suggestions_path


def dataclass_dict(obj):
    import dataclasses

    return dataclasses.asdict(obj)
