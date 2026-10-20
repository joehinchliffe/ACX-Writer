#!/usr/bin/env python3
"""Write one book end to end.

    python write_book.py --title "The Anxious Attachment Cure" \\
        --subtitle "How to Stop Overthinking, Feel Secure and Build Relationships That Last" \\
        --pen-name "Clara Voss" --word-count 20000

Everything is saved under output/<pen name>/<title>/. Each step's result is kept in
the hidden .work folder there, so re-running the same command after a failure
picks up where it stopped. Delete a file in .work to redo that step.
"""

import argparse
import sys
from pathlib import Path

from acx_writer import editor, marketing, outline, research, writer
from acx_writer.docs import save_manuscript_docx
from acx_writer.project import Book, word_count


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", required=True)
    parser.add_argument("--pen-name", required=True)
    parser.add_argument("--word-count", type=int, default=15000)
    parser.add_argument("--english", default="American", choices=["American", "British"])
    parser.add_argument("--competitor-cover", action="append", type=Path, default=[],
                        help="Image of a competing cover for the cover brief (repeatable)")
    parser.add_argument("--no-web-search", action="store_true",
                        help="Research and categories from Claude's own knowledge only")
    args = parser.parse_args()

    for path in args.competitor_cover:
        if not path.exists():
            sys.exit(f"Competitor cover not found: {path}")

    book = Book(args.title, args.subtitle, args.pen_name, args.word_count, args.english)
    web = not args.no_web_search
    print(f"Book: {book.title} — {book.subtitle}\nBy {book.pen_name} · {book.word_count:,} words\nFolder: {book.root}")

    res = research.run(book, web_search=web)
    plan = outline.run(book, res)
    draft = writer.run(book, res, plan)
    final, _ = editor.run(book, res, draft, book.load("style_guide.md"))

    meta = {"title": book.title, "subtitle": book.subtitle, "pen_name": book.pen_name, "copyright": book.copyright}
    save_manuscript_docx(meta, draft, book.root / "Drafts" / f"{book.file_title} - First Draft.docx")
    save_manuscript_docx(meta, final, book.root / f"{book.file_title}.docx")
    text = writer.manuscript(final)
    (book.root / f"{book.file_title}.md").write_text(text, encoding="utf-8")
    print(f"\n  ✓ Manuscript saved: {word_count(text):,} words")

    marketing.descriptions(book, res, text)
    marketing.categories(book, res, final, web_search=web)
    marketing.cover(book, res, plan["overview"], args.competitor_cover)

    print(f"\nDone. Everything is in: {book.root}")


if __name__ == "__main__":
    main()
