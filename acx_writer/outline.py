"""Step 2: outline draft → expanded writing plan per chapter → manuscript information."""

from __future__ import annotations

from . import llm
from .docs import save_markdown_docx
from .project import Book, render
from .research import reader_text

_SECTION = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "word_count": {"type": "integer"}},
    "required": ["title", "word_count"],
    "additionalProperties": False,
}
_BOOKEND = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "purpose": {"type": "string"},
        "word_count": {"type": "integer"},
    },
    "required": ["title", "purpose", "word_count"],
    "additionalProperties": False,
}
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "introduction": _BOOKEND,
        "chapters": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "purpose": {"type": "string"},
                "word_count": {"type": "integer"},
                "intro_word_count": {"type": "integer"},
                "sections": {"type": "array", "items": _SECTION},
            },
            "required": ["title", "purpose", "word_count", "intro_word_count", "sections"],
            "additionalProperties": False,
        }},
        "conclusion": _BOOKEND,
    },
    "required": ["introduction", "chapters", "conclusion"],
    "additionalProperties": False,
}
EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "promise": {"type": "string"},
        "opening_hook": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "word_count": {"type": "integer"},
                "description": {"type": "string"},
                "content_flow": {"type": "array", "items": {"type": "string"}},
                "story_or_example": {"type": "string"},
                "practical_tool": {"type": "string"},
            },
            "required": ["title", "word_count", "description", "content_flow",
                         "story_or_example", "practical_tool"],
            "additionalProperties": False,
        }},
        "callbacks": {"type": "array", "items": {"type": "string"}},
        "closing": {"type": "string"},
    },
    "required": ["promise", "opening_hook", "sections", "callbacks", "closing"],
    "additionalProperties": False,
}
INFO_SCHEMA = {
    "type": "object",
    "properties": {
        "main_goals": {"type": "array", "items": {"type": "string"}},
        "about": {"type": "string"},
        "key_takeaways": {"type": "array", "items": {"type": "string"}},
        "reader_questions": {"type": "array", "items": {"type": "string"}},
        "age_groups": {"type": "array", "items": {"type": "string", "enum": [
            "Toddler", "Teen", "Student", "Women", "Other", "Child", "General adult", "Parent", "Men"]}},
        "audience_interests": {"type": "string"},
        "reading_level": {"type": "string", "enum": ["Basic", "Intermediate", "Advanced"]},
        "writing_styles": {"type": "array", "items": {
            "type": "object",
            "properties": {"style": {"type": "string"}, "why": {"type": "string"}},
            "required": ["style", "why"],
            "additionalProperties": False,
        }},
        "bestsellers": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "author": {"type": "string"}},
            "required": ["title", "author"],
            "additionalProperties": False,
        }},
        "competition_strengths": {"type": "array", "items": {"type": "string"}},
        "competition_weaknesses": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["main_goals", "about", "key_takeaways", "reader_questions", "age_groups",
                 "audience_interests", "reading_level", "writing_styles", "bestsellers",
                 "competition_strengths", "competition_weaknesses"],
    "additionalProperties": False,
}


def parts_from_draft(draft: dict) -> list[dict]:
    """Flatten the draft into the parts of the book in reading order."""
    intro, outro = draft["introduction"], draft["conclusion"]
    parts = [{"kind": "introduction", "name": "the Introduction", "heading": f"Introduction: {intro['title']}",
              "sections": [], **intro}]
    for n, ch in enumerate(draft["chapters"], 1):
        parts.append({"kind": "chapter", "name": f"Chapter {n}", "heading": f"Chapter {n}: {ch['title']}", **ch})
    parts.append({"kind": "conclusion", "name": "the Conclusion", "heading": f"Conclusion: {outro['title']}",
                  "sections": [], **outro})
    return parts


def draft_markdown(parts: list[dict]) -> str:
    lines = []
    for p in parts:
        lines.append(f"## {p['heading']} ({p['word_count']:,} words)")
        lines.append(p["purpose"])
        if p.get("intro_word_count"):
            lines.append(f"- Chapter introduction ({p['intro_word_count']:,} words)")
        lines += [f"- {s['title']} ({s['word_count']:,} words)" for s in p["sections"]]
        lines.append("")
    return "\n".join(lines)


def plan_markdown(part: dict) -> str:
    plan = part["plan"]
    lines = [f"**Promise:** {plan['promise']}", f"**Opening hook:** {plan['opening_hook']}", ""]
    for s in plan["sections"]:
        lines.append(f"### {s['title']} ({s['word_count']:,} words)")
        lines.append(s["description"])
        lines += [f"{i}. {step}" for i, step in enumerate(s["content_flow"], 1)]
        lines.append(f"**Story or example:** {s['story_or_example']}")
        if s["practical_tool"]:
            lines.append(f"**Practical tool:** {s['practical_tool']}")
        lines.append("")
    if plan["callbacks"]:
        lines.append("**Callbacks:** " + "; ".join(plan["callbacks"]))
    lines.append(f"**Closing:** {plan['closing']}")
    return "\n".join(lines)


def info_markdown(info: dict, book: Book) -> str:
    bullets = lambda items: "\n".join(f"- {i}" for i in items) or "- N/A"
    return "\n".join([
        "## Manuscript information", f"- **Book title:** {book.title}", f"- **Book subtitle:** {book.subtitle}",
        "## Manuscript objectives", "**Main goals**", bullets(info["main_goals"]),
        f"**What it is about:** {info['about']}", "**Key reader takeaways**", bullets(info["key_takeaways"]),
        "**Questions readers want answered**", bullets(info["reader_questions"]),
        "## Audience analysis", f"- **Age groups:** {', '.join(info['age_groups'])}",
        f"- **Audience interests:** {info['audience_interests']}",
        f"- **Reading level:** {info['reading_level']}",
        "**Writing style**", bullets(f"**{w['style']}**: {w['why']}" for w in info["writing_styles"]),
        "## Brand positioning and differentiation", "**Bestsellers in the niche**",
        bullets(f"{b['title']} by {b['author']}" for b in info["bestsellers"]),
        "**Competition strengths**", bullets(info["competition_strengths"]),
        "**Competition weaknesses**", bullets(info["competition_weaknesses"]),
    ])


def run(book: Book, research: dict) -> dict:
    print(f"\n[2/7] Outline: {book.num_chapters} chapters, {book.word_count:,} words")
    avg = round((book.word_count - 2000) / book.num_chapters)

    def make_draft():
        prompt = render("outline_draft", title=book.title, subtitle=book.subtitle, drp=research["drp"],
                        word_count=book.word_count, chapters=book.num_chapters, avg_chapter_words=avg,
                        english_style=book.english_style)
        for attempt in range(2):
            draft = llm.ask_json(prompt, schema=DRAFT_SCHEMA, kind="outline", label="outline draft")
            if len(draft["chapters"]) == book.num_chapters:
                return draft
            print(f"  ⚠ Draft has {len(draft['chapters'])} chapters, wanted {book.num_chapters}; retrying")
        return draft

    parts = parts_from_draft(book.step("outline_draft.json", make_draft))
    overview = draft_markdown(parts)
    reader = reader_text(research)

    for i, part in enumerate(parts):
        print(f"  Expanding {part['heading']}…")
        part["plan"] = book.step(f"plan_{i:02d}.json", lambda part=part: llm.ask_json(
            render("outline_expand", title=book.title, subtitle=book.subtitle, reader_brief=reader,
                   outline=overview, part=draft_markdown([part])),
            schema=EXPAND_SCHEMA, kind="outline", label=f"expand {part['name']}",
        ))

    print("  Manuscript information…")
    info = book.step("manuscript_info.json", lambda: llm.ask_json(
        render("manuscript_information", title=book.title, subtitle=book.subtitle,
               drp=research["drp"], outline=overview),
        schema=INFO_SCHEMA, kind="extract", max_tokens=16000, label="manuscript info",
    ))

    full = "\n\n".join(f"## {p['heading']} ({p['word_count']:,} words)\n\n{plan_markdown(p)}" for p in parts)
    save_markdown_docx(
        f"# 1. Outline draft\n\n{overview}\n\n# 2. Expanded outline\n\n{full}\n\n"
        f"# 3. Manuscript information and audience analysis\n\n{info_markdown(info, book)}",
        book.research / f"Book Outline - {book.file_title}.docx", f"Book Outline — {book.title}",
    )
    return {"parts": parts, "overview": overview, "info": info}
