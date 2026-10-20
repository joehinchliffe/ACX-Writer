"""Step 3: write the first draft, one part at a time, with the whole book so far in view."""

from __future__ import annotations

from . import llm
from .outline import plan_markdown
from .project import Book, render, word_count
from .research import reader_text

LENGTHEN = """The draft below of {name} ("{title}") is {words:,} words, but the plan calls for about {target:,}.
Rewrite it in full at about {target:,} words. Add depth, not padding: develop the stories further, \
walk through the techniques step by step, and answer the objections the reader would raise. \
Keep the "##" section headings. Output only the rewritten text.

<draft>
{text}
</draft>"""


def style_guide(book: Book, research: dict, info: dict, tone: str = "") -> str:
    book_tone = (f"# Tone and style for this book (follows the rules above; wins where they differ)\n{tone.strip()}\n"
                 if tone.strip() else "")
    return render(
        "style_guide", pen_name=book.pen_name, title=book.title, subtitle=book.subtitle,
        reader_brief=reader_text(research), english_style=book.english_style,
        reading_level=info["reading_level"],
        writing_style=", ".join(w["style"] for w in info["writing_styles"]) or "Supportive/Empathetic",
        book_tone=book_tone,
    )


def position_note(parts: list[dict], i: int) -> str:
    part = parts[i]
    if part["kind"] == "introduction":
        return ("This opens the book. Hook the listener within the first 30 seconds, make them feel "
                "understood, show why the usual advice hasn't worked, and preview what each chapter gives them.")
    if part["kind"] == "conclusion":
        return ("This ends the book. Bring the whole method together, show the reader who they are becoming, "
                "and close with a resonant final line and one clear next step. Do not introduce new techniques.")
    nxt = parts[i + 1]["heading"]
    return f"End with a forward pull that makes the listener want the next part ({nxt}) without summarising this one."


def manuscript(chapters: list[dict]) -> str:
    return "\n\n".join(f"# {c['heading']}\n\n{c['text']}" for c in chapters)


def run(book: Book, research: dict, outline: dict) -> list[dict]:
    parts = outline["parts"]
    print(f"\n[3/7] Writing the first draft ({len(parts)} parts)")
    system = book.step("style_guide.md", lambda: style_guide(book, research, outline["info"],
                                                             outline.get("tone", "")))
    chapters: list[dict] = []

    for i, part in enumerate(parts):
        target = part["word_count"]
        print(f"  Writing {part['heading']} (~{target:,} words)…")

        def write(part=part, i=i, target=target):
            text = llm.ask(
                render("write_chapter", outline=outline["overview"],
                       written_so_far=manuscript(chapters) or "(Nothing yet: this is the start of the book.)",
                       part_name=part["name"], part_title=part["title"], target_words=target,
                       plan=plan_markdown(part), position_note=position_note(parts, i)),
                system=system, kind="write", label=part["name"],
            )
            words = word_count(text)
            if words < 0.85 * target:
                print(f"    {words:,} words is short of {target:,}; lengthening")
                text = llm.ask(LENGTHEN.format(name=part["name"], title=part["title"], words=words,
                                               target=target, text=text),
                               system=system, kind="write", label=f"{part['name']} lengthen")
            return text

        text = book.step(f"draft_{i:02d}.md", write)
        print(f"    ✓ {word_count(text):,} words")
        chapters.append({"heading": part["heading"], "name": part["name"], "title": part["title"], "text": text})

    total = sum(word_count(c["text"]) for c in chapters)
    print(f"  ✓ First draft: {total:,} words")
    return chapters
