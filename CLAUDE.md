# Working in this repo

This folder is a book writer: `write_book.py` researches, outlines, writes, edits and markets a non-fiction self-help book using Claude Sonnet 5.5 through the user's own Anthropic API key. Many users are not technical. When someone opens this folder and says anything like "help me write a book", "run it" or "what is this?", be their guide: explain in a sentence what it does, then walk them through the steps below one at a time, in plain language, doing the technical parts yourself. Don't show them commands unless they ask.

## Guiding a new user

1. **Python.** Check that Python 3.10+ is installed (`python3 --version`; on Windows `py -3 --version`). If not, send them to https://www.python.org/downloads/ (on Windows, tell them to tick "Add python.exe to PATH" on the first screen) and wait until they say it's done.
2. **Setup.** Create the project's virtual environment if `.venv` doesn't exist (`python3 -m venv .venv`; Windows `py -3 -m venv .venv`) and install the requirements with it (`.venv/bin/python -m pip install -r requirements.txt`; Windows `.venv\Scripts\python.exe -m pip install -r requirements.txt`). Use that Python for everything below. Nothing is installed system-wide.
3. **API key.** Check whether `ANTHROPIC_API_KEY` is set or `anthropic_key.txt` exists (check that the file exists; never print or read its contents). If neither:
   - Explain that the book writer uses their own Anthropic account, so they need an API key with credits: sign in at https://console.anthropic.com, add credits under Billing (a 20,000-30,000 word book usually costs a few dollars), then Settings → API keys → Create key, and copy it.
   - **Never ask them to paste the key into the chat.** Instead, create an empty `anthropic_key.txt` in this folder and open it for them (Mac: `open -e anthropic_key.txt`; Windows: `notepad anthropic_key.txt`). Ask them to paste the key into that file, save and close it, then tell you. Confirm the file isn't empty by checking its size, not its contents.
   - If they paste a key into the chat anyway, don't repeat it. Tell them to delete that key in the console, create a new one, and put the new one in the file.
4. **Where to save.** If `settings.json` has an `output_dir`, use it. Otherwise ask where books should go. If they use Google Drive for Desktop, suggest a folder inside it (Mac: under `~/Library/CloudStorage/GoogleDrive-…/My Drive/`; Windows: usually `G:\My Drive\`), so finished books appear in Drive by themselves. Look for that folder yourself rather than making them type a path. Otherwise the `output` folder here is fine. Save their choice to `settings.json` as `{"output_dir": "..."}`.
5. **The book.** Ask for the title, subtitle, pen name and word count (suggest 15,000-35,000; 15,000 if they don't mind). Tell them roughly how long it takes (30-60 minutes) and that they can leave it running.
6. **Write it.** Run `write_book.py --title ... --subtitle ... --pen-name ... --word-count N --output-dir ...` in the background with the virtual environment's Python. Always pass the details as options: the script's interactive questions don't work when you run it. Give a short update as each stage finishes (research, outline, each chapter, edit). If it fails (no credits, no internet), explain the problem in plain words, help fix it, and re-run the same command; finished steps are reused from `.work/`, so nothing is paid for twice.
7. **Check it.** Look over the edited text for continuity the editor missed (days, times, names, ages, chapter cross-references) with `grep` over `.work/edited_*.md`; fix small slips by hand in those files and re-run the same command to rebuild the `.docx`/`.md` (every step is reused, so it costs nothing). If the log says "keeping the unrevised text" for a chapter whose notes asked for big cuts, delete its `edited_NN.md` and re-run with `EDIT_MIN_RATIO=0.6`.
8. **Hand it over.** Tell them where the files are (offer to open the folder: Mac `open <folder>`, Windows `explorer <folder>`), which file is the manuscript, the final word count and editorial scores, and anything they should check before publishing: claims about the pen name's credentials, bonuses or PDFs the book promises that don't exist yet, helpline numbers, and any title clash the categories research found.

Next time, they can just say "write another book": the key and save folder are remembered.

## In a cloud session (Claude Code on the web)

The same steps apply, with these differences: install with `pip install -r requirements.txt`; the API key should be the `ANTHROPIC_API_KEY` environment variable (if it's missing, tell them to add it in the cloud environment settings and start a new session); and books are saved to `output/`, then uploaded to Google Drive with the Google Drive connector:

- The Drive folder for books is in the `DRIVE_FOLDER_URL` environment variable (`echo $DRIVE_FOLDER_URL`); its folder id is the part after `/folders/`. If it's not set, ask for the folder link, and suggest adding it as `DRIVE_FOLDER_URL` in the environment settings.
- Mirror the local folder inside it: `<pen name>` (reuse an existing folder with that name if there is one) → `<title>` → files, with `Book Research/` and `Drafts/` as subfolders.
- Upload documents as Google Docs from Markdown (`contentMimeType: text/markdown`, `textContent`). One upload is one message, so a manuscript over ~50 KB of Markdown must go up in parts (e.g. three Google Docs of 3-4 chapters each), split at `# ` chapter headings. Tables convert, but bold inside table cells shows as literal asterisks. Do not upload `.work/`. Binary files such as zips are too large to upload this way.
- Send the `.docx` files with SendUserFile too.

## Writing a book whose research already exists

Some book folders already hold numbered documents made earlier, for example `00 Market Research Inputs`, `01 Dream Reader Persona`, `02 Subtitle` (the chosen subtitle at the top), `04 Outline Expanded`, `04a Tone and Style`, `05 Outline Information`, plus description, blurb and cover docs. Ignore any `_Superseded` folders.

1. Get 00, 01, 04, 04a and 05 as Markdown, `.txt` or `.docx` files in one folder, keeping the number at the start of each file name (e.g. `04 Outline Expanded.md`). In a cloud session, read them from Drive with the connector; on the user's computer, ask them to put the files in a folder (or find them in their Google Drive for Desktop folder). 01 and 04 are required; the expanded outline needs `## Chapter N: Title (N words)` headings.
2. Take the title from the folder name and the subtitle from `02 Subtitle`.
3. Run `write_book.py --title ... --subtitle ... --pen-name ... --from-folder <that folder>`, adding `--skip descriptions` and/or `--skip cover` when those documents already exist. Word count and chapters come from the outline.
4. Put the manuscript, editorial report and categories next to the existing docs, numbered after them (e.g. `17 Categories and Keywords`, `18 Manuscript`, `19 Editorial Report`).
