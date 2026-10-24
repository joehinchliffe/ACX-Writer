# ACX Writer

Writes a complete non-fiction self-help book with Claude Sonnet 5.5: research, outline, first draft, edit, descriptions, categories, and cover brief. One manuscript serves Audible (ACX) narration and Amazon KDP (Kindle and paperback).

## Setting up your own copy

Everyone uses their own Anthropic API key, so API costs go to whoever runs the book. Keys are never stored in the repo.

**With Claude Code on the web (recommended).** Claude runs the script for you and uploads the finished book to your Google Drive.

1. Get access to this repository on GitHub (as a collaborator, or a copy in your own account) and start a Claude Code session on it at claude.ai/code.
2. In the session's cloud environment settings, add two environment variables:
   - `ANTHROPIC_API_KEY`: your key from console.anthropic.com (the account needs credits).
   - `DRIVE_FOLDER_URL`: the link to the Google Drive folder your books should go in.
3. Connect Google Drive under claude.ai Settings → Connectors.
4. Start a new session and say: *"Write a book: title …, subtitle …, pen name …, 20,000 words."* Claude follows `CLAUDE.md` from there.

**On your own computer, no GitHub needed.** Install Python 3.10+ and double-click `Write a Book (Mac).command` or `Write a Book (Windows).bat`. It sets itself up, asks for your API key once (saved only in `anthropic_key.txt` on that computer), then asks for the title, subtitle, pen name, word count and save folder. `READ ME FIRST.txt` walks through it step by step. Or run the commands below yourself. To have books land straight in Google Drive, install Google Drive for Desktop and add `--output-dir` pointing at a folder inside it (or set `ACX_OUTPUT_DIR`), for example `--output-dir "~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/Books"` on a Mac.

## Run a book

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # or put the key in anthropic_key.txt (git-ignored)

python write_book.py \
  --title "The Anxious Attachment Cure" \
  --subtitle "How to Stop Overthinking, Feel Secure and Build Relationships That Last" \
  --pen-name "Jane Example" \
  --word-count 20000
```

Options: `--output-dir <folder>`, `--english British`, `--competitor-cover cover.jpg` (repeatable; gives the cover brief competing covers to beat), `--no-web-search`, `--skip descriptions|categories|cover`.

### From existing research and outline

If the reader persona and expanded outline already exist (for example from an earlier Claude session), put them in a folder as `.md`, `.txt` or `.docx` files named by number: `01 Dream Reader Persona` and `04 Outline Expanded` (required), and optionally `00 Market Research Inputs`, `04a Tone and Style`, `05 Outline Information`. Then add `--from-folder that/folder`. The research and outline steps are skipped; chapters and word counts come from the outline's `## Chapter N: Title (4,000 words)` headings, each chapter is written from its section of the outline, and the tone-and-style file is added to the style guide.

If a run stops partway (network error, credits), run the same command again. Every step's result is saved in `output/<pen name>/<title>/.work/`, so it continues where it stopped. To redo one step, delete its file there (for example `draft_03.md` rewrites Chapter 3; every later step that depends on it must be deleted too).

## What it produces

```
output/<pen name>/<title>/
  <title>.docx                      final manuscript (title page, copyright + disclaimer, chapters)
  <title>.md                        same, as Markdown
  Descriptions - <title>.docx       audience-optimised + Hormozi versions, eBook + audiobook, KDP HTML + plain text
  Categories - <title>.docx         KDP categories, 7 KDP keywords, Audible categories, positioning
  Editorial Report - <title>.docx   scores and what the edit changed
  Drafts/<title> - First Draft.docx unedited draft, for comparison
  Book Research/
    Dream Reader Persona, Subtitle Suggestions, Book Outline, Cover Brief, Ideogram Prompts
```

## The pipeline

| Step | What happens |
|---|---|
| 1. Research | Dream Reader Persona with live web research on competing books and real reader language; a reader brief for the writer; subtitle options checked against Amazon's rules. |
| 2. Outline | Chapter and section plan with word counts, then a detailed writing plan for each chapter (flow, story, practical tool, callbacks), then the manuscript information sheet. |
| 3. Draft | Each chapter is written from its plan, with the whole book so far in view so nothing repeats. Short chapters are lengthened. |
| 4. Edit | A developmental review of the complete draft (flow, gaps, repetition, credibility, audio), then a revision and line edit of every chapter for ear and page. |
| 5. Descriptions | Two description styles, each in eBook and audiobook versions. |
| 6. Categories | KDP and Audible categories and keywords, checked with web search. |
| 7. Cover | Cover brief (Claude sees the cover-type examples in `prompts/cover_types/`) and 3 Ideogram concepts, each with a square audiobook and a portrait eBook prompt. Images are not generated. |

## Customising

All prompts are Markdown files in `prompts/`; edit them freely. `{{name}}` placeholders are filled in by the code. `prompts/style_guide.md` controls the voice of the whole book. The original Nespola prompts these were adapted from are in `prompts/original/` (not included in shared copies).

Effort per step can be changed with environment variables (`EFFORT_WRITE=high`, etc.; see `acx_writer/llm.py`).
