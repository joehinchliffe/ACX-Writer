#!/usr/bin/env python3
"""Write one book end to end.

    python write_book.py --title "The Anxious Attachment Cure" \\
        --subtitle "How to Stop Overthinking, Feel Secure and Build Relationships That Last" \\
        --pen-name "Jane Example" --word-count 20000

Everything is saved under output/<pen name>/<title>/. Each step's result is kept in
the hidden .work folder there, so re-running the same command after a failure
picks up where it stopped. Delete a file in .work to redo that step.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from acx_writer import editor, imported, llm, marketing, outline, research, writer
from acx_writer.docs import save_manuscript_docx
from acx_writer.project import OUTPUT_DIR, REPO_DIR, Book, word_count

SETTINGS_FILE = REPO_DIR / "settings.json"  # remembers the save folder; git-ignored


def ask(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    while True:
        answer = input(f"{question}{hint}: ").strip().strip('"')
        if answer or default:
            return answer or default


def ask_for_book(args):
    """Interactive mode, used by the double-click launchers."""
    print("\nLet's write a book. Press Enter to accept a suggestion in [brackets].\n")
    args.title = args.title or ask("Title")
    args.subtitle = args.subtitle or ask("Subtitle")
    args.pen_name = args.pen_name or ask("Pen name")
    if not args.word_count:
        while True:
            words = ask("Word count", "15000").replace(",", "")
            if words.isdigit() and int(words) >= 5000:
                args.word_count = int(words)
                break
            print("  Please enter a number of at least 5000.")
    if not args.output_dir and not os.getenv("ACX_OUTPUT_DIR"):
        saved = load_settings().get("output_dir", "")
        folder = ask("Folder to save books in (e.g. a Google Drive folder)", saved or str(OUTPUT_DIR))
        save_settings({"output_dir": folder})
        args.output_dir = Path(folder).expanduser()
    print()


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(values: dict):
    SETTINGS_FILE.write_text(json.dumps({**load_settings(), **values}, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title")
    parser.add_argument("--subtitle")
    parser.add_argument("--pen-name")
    parser.add_argument("--word-count", type=int)
    parser.add_argument("--english", default="American", choices=["American", "British"])
    parser.add_argument("--competitor-cover", action="append", type=Path, default=[],
                        help="Image of a competing cover for the cover brief (repeatable)")
    parser.add_argument("--no-web-search", action="store_true",
                        help="Research and categories from Claude's own knowledge only")
    parser.add_argument("--from-folder", type=Path,
                        help="Use existing research and outline documents from this folder instead of "
                             "generating them (see acx_writer/imported.py for the file names)")
    parser.add_argument("--skip", action="append", default=[], choices=["descriptions", "categories", "cover"],
                        help="Skip a marketing step, e.g. when its document already exists (repeatable)")
    parser.add_argument("--output-dir", type=Path,
                        help="Where books are saved (default: output/, or ACX_OUTPUT_DIR). Point it at a "
                             "Google Drive for Desktop folder to have books sync to Drive.")
    args = parser.parse_args()
    if not (args.title and args.subtitle and args.pen_name):
        ask_for_book(args)
    args.word_count = args.word_count or 15000
    args.output_dir = args.output_dir or OUTPUT_DIR
    llm.client()  # asks for the API key now, if there isn't one yet

    for path in args.competitor_cover:
        if not path.exists():
            sys.exit(f"Competitor cover not found: {path}")

    book = Book(args.title, args.subtitle, args.pen_name, args.word_count, args.english, args.output_dir)
    web = not args.no_web_search
    print(f"Book: {book.title} — {book.subtitle}\nBy {book.pen_name} · {book.word_count:,} words\nFolder: {book.root}")

    if args.from_folder:
        res, plan = imported.run(book, args.from_folder)
    else:
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

    if "descriptions" not in args.skip:
        marketing.descriptions(book, res, text)
    if "categories" not in args.skip:
        marketing.categories(book, res, final, web_search=web)
    if "cover" not in args.skip:
        marketing.cover(book, res, plan["overview"], args.competitor_cover)

    print(f"\nDone. Everything is in: {book.root}")


if __name__ == "__main__":
    main()
