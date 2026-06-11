# Manual QA Checklist Before Printing

Open the PDF and the rendered PNGs in `qa/`. Also review `qa_report.txt`, `qa_verdict.json`, and any AI QA output files.

Check these pages at minimum:

1. half title page;
2. full title page;
3. source/copyright note page;
4. first TOC page and final TOC page;
5. first Arabic page of main text — confirm folio starts at 1;
6. at least three normal running-head text pages;
7. one later page from each major work/division;
8. each major work opener with a subtitle/editorial description, especially Delphi-style collected works;
9. any page with poetry;
10. any page with cast list / characters / dramatis personae;
11. any page with maps, diagrams, facsimiles, runes, inscriptions, or other image-text;
12. final 3-5 pages.

Reject and rebuild if you see:

- running-head rule visually acting like an overline on the first body line;
- visible folio/running head on blank/title pages;
- TOC without page numbers;
- local mini-TOCs inside individual works;
- editorial work descriptions printed as normal body text instead of smaller italic apparatus;
- work subtitles incorrectly italicized as descriptions;
- promotional catalogue matter;
- orphan captions from removed plates;
- single-letter or broken-word line spills;
- poetry justified as prose instead of ragged-right;
- cast lists collapsed awkwardly into Act I or Scene I;
- black/gray page artifacts;
- orphan headings (heading at bottom of page with text on next page);
- widow/single-word lines at page tops.

Tool-assisted checks to run:

- verify `qa_verdict.json` `first_body_folio_warnings` is empty — the first Arabic page should show folio 1;
- review `deepseek_text_qa.txt` or AI text QA output for structural/textual suggestions;
- review `deepseek_rule_suggestions.review.yaml` if generated — manually approve patterns before loading via `rules/generic_epub.yaml`.
- narrow ebook columns;
- poetry justified as prose;
- cast lists collapsed awkwardly into Act I;
- black/gray page artifacts;
- missing authorial maps/diagrams/image-texts.
