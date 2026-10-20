"""Step 1: Dream Reader Persona, reader brief, and subtitle suggestions."""

from __future__ import annotations

from . import llm
from .docs import save_markdown_docx
from .project import Book, render

READER_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "audience": {"type": "string"},
        "pain_point": {"type": "string"},
        "transformation": {"type": "string"},
        "reader_brief": {"type": "string"},
        "lingo": {"type": "array", "items": {"type": "string"}},
        "competitor_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["audience", "pain_point", "transformation", "reader_brief", "lingo", "competitor_gaps"],
    "additionalProperties": False,
}


def run(book: Book, *, web_search: bool = True) -> dict:
    print("\n[1/7] Research: Dream Reader Persona")
    drp = book.step("drp.md", lambda: llm.ask(
        render("dream_reader_persona", title=book.title, subtitle=book.subtitle),
        kind="research", web_search=web_search, label="DRP",
    ))
    save_markdown_docx(drp, book.research / f"Dream Reader Persona - {book.file_title}.docx",
                       f"Dream Reader Persona — {book.title}")

    brief = book.step("reader_brief.json", lambda: llm.ask_json(
        render("reader_brief", title=book.title, subtitle=book.subtitle, drp=drp),
        schema=READER_BRIEF_SCHEMA, kind="extract", max_tokens=8000, label="reader brief",
    ))
    print(f"  Audience   : {brief['audience']}")
    print(f"  Pain point : {brief['pain_point']}")

    print("  Subtitle suggestions…")
    subtitles = book.step("subtitles.md", lambda: llm.ask(
        render("subtitles", title=book.title, subtitle=book.subtitle, drp=drp),
        kind="marketing", label="subtitles",
    ))
    save_markdown_docx(subtitles, book.research / f"Subtitle Suggestions - {book.file_title}.docx",
                       f"Subtitle Suggestions — {book.title}")
    return {"drp": drp, **brief}


def reader_text(research: dict) -> str:
    """The reader portrait handed to the writer and marketing prompts."""
    return (
        f"{research['reader_brief']}\n\n"
        f"Words this reader uses: {', '.join(research['lingo'])}\n\n"
        "Where competing books fall short (do better here):\n"
        + "\n".join(f"- {gap}" for gap in research["competitor_gaps"])
    )
