# Working in this repo

When the user sends a title, subtitle and pen name (and optionally a word count) and asks for a book:

If the user just says "run it" without the book details, ask for the title, subtitle, pen name and word count first. Always pass them as options: the script's interactive questions don't work when you run it.

1. Install dependencies if needed. On the user's own computer, use the project's virtual environment so nothing is installed system-wide: create it with `python3 -m venv .venv` (Windows: `py -3 -m venv .venv`) if `.venv` doesn't exist, install with `.venv/bin/python -m pip install -r requirements.txt` (Windows: `.venv\Scripts\python.exe -m pip ...`), and run the script with that Python. In a cloud session, `pip install -r requirements.txt` is fine.
2. The API key comes from `ANTHROPIC_API_KEY` or a git-ignored `anthropic_key.txt`. Never commit a key, never ask the user to paste one into chat, and never print or read the key file's contents. If neither exists:
   - On the user's own computer: ask them to double-click `Write a Book (Mac).command` or `Write a Book (Windows).bat` once, paste their key when it asks, then close the window when it starts asking for the title. Or they can create `anthropic_key.txt` in this folder containing just the key.
   - In a cloud session: tell them to add `ANTHROPIC_API_KEY` as an environment variable in their cloud environment settings and start a new session.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --word-count N` in the background (it takes a while). Default word count is 15000 if none is given. On the user's own computer, if `settings.json` has an `output_dir`, pass it as `--output-dir`; otherwise ask where to save (a Google Drive for Desktop folder makes books appear in Drive with no upload needed). If it fails, fix the cause and re-run the same command; finished steps are reused from `.work/`.
4. Check the edited text for continuity the editor missed (days, times, names, ages, chapter cross-references) with `grep` over `.work/edited_*.md`; fix small slips by hand in those files and re-run the same command to rebuild the `.docx`/`.md` (every step is reused, so it costs nothing). If the log says "keeping the unrevised text" for a chapter whose notes asked for big cuts, delete its `edited_NN.md` and re-run with `EDIT_MIN_RATIO=0.6`.
5. Give the user the `.docx` files (SendUserFile if available; on their own computer, tell them the folder path). If the book was saved into a Google Drive for Desktop folder, it's already in Drive: skip the upload. Otherwise, if a Google Drive connector is available, upload to Drive:
   - The Drive folder for books is in the `DRIVE_FOLDER_URL` environment variable (`echo $DRIVE_FOLDER_URL`); its folder id is the part after `/folders/`. If it's not set, ask the user for the folder link, and suggest they add it as `DRIVE_FOLDER_URL` in their environment settings so future sessions don't need to ask.
   - Mirror the local folder inside it: `<pen name>` (reuse an existing folder with that name if there is one) → `<title>` → files, with `Book Research/` and `Drafts/` as subfolders.
   - Upload documents as Google Docs from Markdown (`contentMimeType: text/markdown`, `textContent`). One upload is one message, so a manuscript over ~50 KB of Markdown must go up in parts (e.g. three Google Docs of 3-4 chapters each), split at `# ` chapter headings. Tables convert, but bold inside table cells shows as literal asterisks. Do not upload `.work/`.
6. Reply with the Drive links, the final word count, the editorial scores, and anything in the text the user should check (e.g. claims about the pen name's credentials, promised bonuses that don't exist yet, title clashes the categories research found).

## Writing a book whose research is already in Drive

Some book folders already hold numbered documents made earlier, for example `00 Market Research Inputs`, `01 Dream Reader Persona`, `02 Subtitle` (the chosen subtitle at the top), `04 Outline Expanded`, `04a Tone and Style`, `05 Outline Information`, plus description, blurb and cover docs. Ignore any `_Superseded` folders.

1. Find the book's folder inside the `DRIVE_FOLDER_URL` folder. Read 00, 01, 04, 04a and 05 with the Drive connector and save each as Markdown in a scratch folder, keeping the number at the start of the file name (e.g. `04 Outline Expanded.md`). 01 and 04 are required; the expanded outline needs `## Chapter N: Title (N words)` headings.
2. Take the title from the folder name and the subtitle from `02 Subtitle`.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --from-folder <scratch folder>`, adding `--skip descriptions` and/or `--skip cover` when those documents already exist in the folder. Word count and chapters come from the outline.
4. Upload the manuscript, editorial report and categories into the book's existing manuscript folder, numbered after the docs already there (e.g. `17 Categories and Keywords`, `18 Manuscript Part 1 of 3`, `19 Editorial Report`).
