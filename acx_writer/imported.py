"""Write a book from research and outline documents made earlier (e.g. by hand in Claude).

The folder holds the documents as .md, .txt or .docx files, named by their number
as in the Drive book folders:
    00 Market Research Inputs   (optional)
    01 Dream Reader Persona     (required)
    04 Outline Expanded         (required: "## Chapter N: Title (N words)" headings)
    04a Tone and Style          (optional)
    05 Outline Information      (optional)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import llm
from .docs import docx_text
from .outline import INFO_SCHEMA
from .project import Book, render
from .research import READER_BRIEF_SCHEMA

DEFAULT_INFO = {"reading_level": "Intermediate",
                "writing_styles": [{"style": "Supportive/Empathetic", "why": ""}]}


def load_folder(folder: Path) -> dict[str, str]:
    docs = {}
    for path in sorted(folder.iterdir()):
        match = re.match(r"^(\d+[a-z]?)\b", path.name)
        if not match or path.suffix.lower() not in (".md", ".txt", ".docx"):
            continue
        text = docx_text(path) if path.suffix.lower() == ".docx" else path.read_text(encoding="utf-8")
        docs[match.group(1)] = text
    for required in ("01", "04"):
        if required not in docs:
            sys.exit(f"{folder} needs a '{required} …' document (see acx_writer/imported.py)")
    return docs


def _clean(heading: str) -> str:
    return re.sub(r"[*#]", "", heading).strip()


def _words(heading: str) -> int:
    match = re.search(r"\(([\d,]+)\s*(?:words?|word count)\)", heading, re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else 0


def parse_expanded(text: str) -> list[dict]:
    """Split the expanded outline into book parts, keeping each part's plan text as written."""
    chunks = re.split(r"(?m)^##(?!#)\s*", text)[1:]
    parts = []
    for chunk in chunks:
        heading, _, body = chunk.partition("\n")
        heading = _clean(heading)
        target = _words(heading)
        title = re.sub(r"\s*\([\d,]+\s*(?:words?|word count)\)\s*$", "", heading, flags=re.IGNORECASE)
        sections = [{"title": re.sub(r"\s*\([^)]*\)\s*$", "", _clean(s)), "word_count": _words(s)}
                    for s in re.findall(r"(?m)^###\s*(.+)$", body)]
        chapter = re.match(r"Chapter\s+(\d+)\s*[:.\-–—]\s*(.+)", title, re.IGNORECASE)
        if chapter:
            n = len([p for p in parts if p["kind"] == "chapter"]) + 1
            parts.append({"kind": "chapter", "name": f"Chapter {n}", "title": chapter.group(2).strip(),
                          "heading": f"Chapter {n}: {chapter.group(2).strip()}",
                          "word_count": target, "sections": sections, "plan": body.strip()})
        elif re.search(r"introduction", title, re.IGNORECASE):
            parts.append({"kind": "introduction", "name": "the Introduction", "title": "Introduction",
                          "heading": "Introduction", "word_count": target or 1000, "sections": [],
                          "plan": body.strip()})
        elif re.search(r"conclusion", title, re.IGNORECASE):
            parts.append({"kind": "conclusion", "name": "the Conclusion", "title": "Conclusion",
                          "heading": "Conclusion", "word_count": target or 1000, "sections": [],
                          "plan": body.strip()})
    if not any(p["kind"] == "chapter" for p in parts):
        sys.exit("Could not find any '## Chapter N: Title (N words)' headings in the expanded outline")
    return parts


def overview(parts: list[dict]) -> str:
    lines = []
    for p in parts:
        lines.append(f"## {p['heading']} ({p['word_count']:,} words)")
        lines += [f"- {s['title']} ({s['word_count']:,} words)" for s in p["sections"]]
    return "\n".join(lines)


def run(book: Book, folder: Path) -> tuple[dict, dict]:
    """Return (research, outline) in the same shape the generated steps produce."""
    docs = load_folder(folder)
    print(f"\n[1-2/7] Using research and outline from {folder}")
    drp = docs["01"] + (f"\n\n# Market research\n\n{docs['00']}" if "00" in docs else "")
    book.save("drp.md", drp)

    brief = book.step("reader_brief.json", lambda: llm.ask_json(
        render("reader_brief", title=book.title, subtitle=book.subtitle, drp=drp),
        schema=READER_BRIEF_SCHEMA, kind="extract", max_tokens=8000, label="reader brief"))

    info = DEFAULT_INFO
    if "05" in docs:
        info = book.step("manuscript_info.json", lambda: llm.ask_json(
            "Fill in these fields from the manuscript information sheet below. Leave a list empty "
            f"where the sheet has nothing for it.\n\n<sheet>\n{docs['05']}\n</sheet>",
            schema=INFO_SCHEMA, kind="extract", max_tokens=8000, label="manuscript info"))

    parts = parse_expanded(docs["04"])
    total = sum(p["word_count"] for p in parts)
    print(f"  {len(parts)} parts, {total:,} words planned")
    book.word_count = total
    research = {"drp": drp, **brief}
    outline = {"parts": parts, "overview": overview(parts), "info": info, "tone": docs.get("04a", "")}
    return research, outline
