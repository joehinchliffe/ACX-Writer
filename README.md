# ACX Writer

Writes a complete non-fiction self-help book with Claude Sonnet 5.5: research, outline, first draft, edit, descriptions, categories, and cover brief. One manuscript serves Audible (ACX) narration and Amazon KDP (Kindle and paperback).

## Run a book

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # or put the key in anthropic_key.txt (git-ignored)

python write_book.py \
  --title "The Anxious Attachment Cure" \
  --subtitle "How to Stop Overthinking, Feel Secure and Build Relationships That Last" \
  --pen-name "Clara Voss" \
  --word-count 20000
```

Options: `--english British`, `--competitor-cover cover.jpg` (repeatable; gives the cover brief competing covers to beat), `--no-web-search`, `--skip descriptions|categories|cover`.

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

All prompts are Markdown files in `prompts/`; edit them freely. `{{name}}` placeholders are filled in by the code. `prompts/style_guide.md` controls the voice of the whole book. The original Nespola prompts these were adapted from are in `prompts/original/`.

Effort per step can be changed with environment variables (`EFFORT_WRITE=high`, etc.; see `acx_writer/llm.py`).
