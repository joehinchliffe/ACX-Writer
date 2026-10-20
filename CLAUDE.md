# Working in this repo

When the user sends a title, subtitle and pen name (and optionally a word count) and asks for a book:

1. Install dependencies if needed: `pip install -r requirements.txt`.
2. The API key comes from `ANTHROPIC_API_KEY` or a git-ignored `anthropic_key.txt`. Never commit a key, never ask the user to paste one into chat.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --word-count N` in the background (it takes a while). Default word count is 15000 if none is given. If it fails, fix the cause and re-run the same command; finished steps are reused from `.work/`.
4. Upload the results to Google Drive with the Google Drive connector, mirroring the local folder:
   `ACX Est Oct 2026` (folder id `11jiclM1yPmpzZGFI8DlX2EjcmBF1zqKd`, https://drive.google.com/drive/folders/11jiclM1yPmpzZGFI8DlX2EjcmBF1zqKd) → `<pen name>` (reuse the existing folder if there is one, e.g. `Nora Linley`) → `<title>` → files, with `Book Research/` and `Drafts/` as subfolders.
   Upload `.docx` files with `contentMimeType` `application/vnd.openxmlformats-officedocument.wordprocessingml.document` and `disableConversionToGoogleType: true`. Do not upload `.work/` or the `.md` file.
5. Reply with the Drive folder link, the final word count, and the editorial scores.

## Writing a book whose research is already in Drive

Some book folders (e.g. `ACX Est Oct 2026/Nora Linley/Anxious Attachment and Avoidant Detachment`) already hold numbered documents made earlier: `Research/00 Market Research Inputs`, `Research/01 Dream Reader Persona`, and in `Manuscript/`: `02 Subtitle` (the chosen subtitle is at the top), `04 Outline Expanded`, `04a Tone and Style`, `05 Outline Information`, plus description, blurb and cover docs. Ignore `_Superseded` folders.

1. Read 00, 01, 04, 04a and 05 with the Drive connector and save each as Markdown in a scratch folder, keeping the number at the start of the file name (e.g. `04 Outline Expanded.md`).
2. Take the title from the folder name and the subtitle from `02 Subtitle`.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --from-folder <scratch folder> --skip descriptions --skip cover` (descriptions and cover already exist there). Word count and chapters come from the outline.
4. Upload the manuscript `.docx`, `Editorial Report`, `Categories` and `Drafts/` into that book's `Manuscript` folder in Drive.
