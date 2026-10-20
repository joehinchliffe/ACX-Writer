# Working in this repo

When the user sends a title, subtitle and pen name (and optionally a word count) and asks for a book:

1. Install dependencies if needed: `pip install -r requirements.txt`.
2. The API key comes from `ANTHROPIC_API_KEY` or a git-ignored `anthropic_key.txt`. Never commit a key, never ask the user to paste one into chat.
3. Run `python write_book.py --title ... --subtitle ... --pen-name ... --word-count N` in the background (it takes a while). Default word count is 15000 if none is given. If it fails, fix the cause and re-run the same command; finished steps are reused from `.work/`.
4. Upload the results to Google Drive with the Google Drive connector, mirroring the local folder:
   `Potato Publishing` (folder id `1JNpuMYiTLO0gPIyyGID9amBjFOOZgzC6`) → `<pen name>` (reuse the existing folder if there is one) → `<title>` → files, with `Book Research/` and `Drafts/` as subfolders.
   Upload `.docx` files with `contentMimeType` `application/vnd.openxmlformats-officedocument.wordprocessingml.document` and `disableConversionToGoogleType: true`. Do not upload `.work/` or the `.md` file.
5. Reply with the Drive folder link, the final word count, and the editorial scores.
