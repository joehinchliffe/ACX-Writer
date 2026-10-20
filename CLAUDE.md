# Working in this repo

When the user sends a title, subtitle and pen name (and optionally a word count) and asks for a book:

1. Install dependencies if needed: `pip install -r requirements.txt`.
2. The API key comes from `ANTHROPIC_API_KEY` or a git-ignored `anthropic_key.txt`. Never commit a key, never ask the user to paste one into chat.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --word-count N` in the background (it takes a while). Default word count is 15000 if none is given. If it fails, fix the cause and re-run the same command; finished steps are reused from `.work/`.
4. Check the edited text for continuity the editor missed (days, times, names, ages, chapter cross-references) with `grep` over `.work/edited_*.md`; fix small slips by hand in those files and re-run the same command to rebuild the `.docx`/`.md` (every step is reused, so it costs nothing). If the log says "keeping the unrevised text" for a chapter whose notes asked for big cuts, delete its `edited_NN.md` and re-run with `EDIT_MIN_RATIO=0.6`.
5. Send the user the `.docx` files with SendUserFile, then upload to Google Drive with the Google Drive connector, mirroring the local folder:
   `ACX Est Oct 2026` (folder id `11jiclM1yPmpzZGFI8DlX2EjcmBF1zqKd`, https://drive.google.com/drive/folders/11jiclM1yPmpzZGFI8DlX2EjcmBF1zqKd) → `<pen name>` (reuse the existing folder if there is one, e.g. `Nora Linley`) → `<title>` → files, with `Book Research/` and `Drafts/` as subfolders.
   Upload documents as Google Docs from Markdown (`contentMimeType: text/markdown`, `textContent`), which matches the existing Drive docs and is cheaper than base64 `.docx`. One upload is one message, so a manuscript over ~50 KB of Markdown must go up in parts (e.g. three Google Docs of 3-4 chapters each) — split at `# ` chapter headings. Tables convert, but bold inside table cells shows as literal asterisks. Do not upload `.work/`.
6. Reply with the Drive links, the final word count, the editorial scores, and anything in the text the user should check (e.g. claims about the pen name's credentials).

## Writing a book whose research is already in Drive

Some book folders (e.g. `ACX Est Oct 2026/Nora Linley/Anxious Attachment and Avoidant Detachment`) already hold numbered documents made earlier: `Research/00 Market Research Inputs`, `Research/01 Dream Reader Persona`, and in `Manuscript/`: `02 Subtitle` (the chosen subtitle is at the top), `04 Outline Expanded`, `04a Tone and Style`, `05 Outline Information`, plus description, blurb and cover docs. Ignore `_Superseded` folders.

1. Read 00, 01, 04, 04a and 05 with the Drive connector and save each as Markdown in a scratch folder, keeping the number at the start of the file name (e.g. `04 Outline Expanded.md`).
2. Take the title from the folder name and the subtitle from `02 Subtitle`.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --from-folder <scratch folder> --skip descriptions --skip cover` (descriptions and cover already exist there). Word count and chapters come from the outline.
4. Upload the manuscript, editorial report and categories into that book's `Manuscript` folder in Drive, numbered after the existing docs (the first book used `17 Categories and Keywords`, `18 Manuscript Part N of 3`, `19 Editorial Report`).
