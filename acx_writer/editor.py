"""Step 4: structural review of the whole draft, then a revision and polish of every part."""

from __future__ import annotations

import os

from docx import Document

from . import llm
from .project import Book, render, word_count

# A revision shorter than this share of the draft is discarded and the draft kept,
# unless the editor's notes asked for big cuts (lower it with EDIT_MIN_RATIO=0.6).
MIN_KEEP_RATIO = float(os.getenv("EDIT_MIN_RATIO", "0.7"))

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "consistency_score": {"type": "integer"},
        "transformation_score": {"type": "integer"},
        "overall_assessment": {"type": "string"},
        "structural_changes": {"type": "array", "items": {"type": "string"}},
        "repeated_elements": {"type": "array", "items": {"type": "string"}},
        "credibility_flags": {"type": "array", "items": {"type": "string"}},
        "audience_alignment": {"type": "string"},
        "part_notes": {"type": "array", "items": {
            "type": "object",
            "properties": {"part_number": {"type": "integer"}, "notes": {"type": "string"}},
            "required": ["part_number", "notes"],
            "additionalProperties": False,
        }},
    },
    "required": ["consistency_score", "transformation_score", "overall_assessment", "structural_changes",
                 "repeated_elements", "credibility_flags", "audience_alignment", "part_notes"],
    "additionalProperties": False,
}


def numbered(chapters: list[dict]) -> str:
    return "\n\n".join(f"# Part {i} — {c['heading']}\n\n{c['text']}" for i, c in enumerate(chapters, 1))


def overall_notes(review: dict) -> str:
    lines = [review["overall_assessment"], "", "Structural changes:"]
    lines += [f"- {c}" for c in review["structural_changes"]]
    lines += ["", "Repeated across the book (keep the best instance, replace the others):"]
    lines += [f"- {r}" for r in review["repeated_elements"]] or ["- None"]
    lines += ["", "Credibility problems:"]
    lines += [f"- {f}" for f in review["credibility_flags"]] or ["- None"]
    return "\n".join(lines)


def run(book: Book, research: dict, chapters: list[dict], style_guide: str) -> tuple[list[dict], dict]:
    print("\n[4/7] Editing")
    print("  Structural review of the full draft…")
    review = book.step("review.json", lambda: llm.ask_json(
        render("structural_review", title=book.title, subtitle=book.subtitle, audience=research["audience"],
               pain_point=research["pain_point"], transformation=research["transformation"],
               manuscript=numbered(chapters)),
        schema=REVIEW_SCHEMA, kind="review", label="structural review",
    ))
    print(f"  Consistency {review['consistency_score']}/10 · transformation {review['transformation_score']}/10")
    notes_by_part = {n["part_number"]: n["notes"] for n in review["part_notes"]}
    shared_notes = overall_notes(review)
    draft = numbered(chapters)

    edited = []
    for i, chapter in enumerate(chapters):
        print(f"  Revising {chapter['heading']}…")
        last = i == len(chapters) - 1
        current = word_count(chapter["text"])
        prompt = render(
            "revise_chapter", manuscript=draft, overall_notes=shared_notes, part_name=f"Part {i + 1}",
            part_title=chapter["heading"], notes=notes_by_part.get(i + 1, "No specific notes."),
            current_words=current,
            ending_note=("This is the end of the book: close with a resonant line that leaves the listener "
                         "changed, and one clear next step." if last else
                         "End with a line that resonates and a forward pull into the next part."),
        )
        shared, task = prompt.split("# Your task", 1)

        def revise(chapter=chapter, current=current, shared=shared, task=task):
            best = None
            for attempt in range(2):
                text = llm.ask(llm.cached(shared, "# Your task" + task), system=style_guide,
                               kind="edit", label=f"revise part {i + 1}")
                ratio = word_count(text) / current
                if 0.85 <= ratio <= 1.2:
                    return text
                print(f"    revision came back at {ratio:.0%} of the original length; retrying")
                if best is None or abs(1 - ratio) < abs(1 - word_count(best) / current):
                    best = text
            if word_count(best) < MIN_KEEP_RATIO * current:
                print("    keeping the unrevised text rather than a much shorter one")
                return chapter["text"]
            return best

        text = book.step(f"edited_{i:02d}.md", revise)
        print(f"    ✓ {current:,} → {word_count(text):,} words")
        edited.append({**chapter, "text": text})

    save_report(book, review, chapters, edited)
    return edited, review


def save_report(book: Book, review: dict, before: list[dict], after: list[dict]):
    doc = Document()
    doc.add_heading(f"Editorial Report: {book.title}", level=0)
    doc.add_heading("Scores (first draft)", level=1)
    doc.add_paragraph(f"Consistency with pain point and transformation: {review['consistency_score']}/10")
    doc.add_paragraph(f"Transformation delivered by the final chapter: {review['transformation_score']}/10")
    doc.add_heading("Overall assessment", level=1)
    doc.add_paragraph(review["overall_assessment"])
    for heading, items in (("Structural changes", review["structural_changes"]),
                           ("Repetition fixed", review["repeated_elements"]),
                           ("Credibility issues fixed", review["credibility_flags"])):
        doc.add_heading(heading, level=1)
        for item in items or ["None found."]:
            doc.add_paragraph(item, style="List Bullet")
    doc.add_heading("Audience alignment", level=1)
    doc.add_paragraph(review["audience_alignment"])
    doc.add_heading("Part by part", level=1)
    notes = {n["part_number"]: n["notes"] for n in review["part_notes"]}
    for i, (b, a) in enumerate(zip(before, after), 1):
        doc.add_heading(b["heading"], level=2)
        doc.add_paragraph(f"{word_count(b['text']):,} → {word_count(a['text']):,} words")
        doc.add_paragraph(notes.get(i, "No specific notes."))
    doc.save(str(book.root / f"Editorial Report - {book.file_title}.docx"))
