"""Manuscript parsing, sentence-safe chunking, respelling and number expansion."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ----------------------------------------------------------------- languages
# Qwen3-TTS 1.7B languages (checked again at runtime against the loaded model).
QWEN_LANGUAGES = {
    "english": "en", "chinese": "zh", "japanese": "ja", "korean": "ko", "german": "de",
    "french": "fr", "russian": "ru", "portuguese": "pt", "spanish": "es", "italian": "it",
}
NOT_SUPPORTED_HINT = {
    "dutch": "Qwen3-TTS has no Dutch. Use a different engine for Dutch books.",
}

# --------------------------------------------------------------- manuscripts
@dataclass
class Paragraph:
    text: str
    kind: str = "body"          # body | heading


@dataclass
class Chapter:
    index: int
    title: str
    paragraphs: List[Paragraph] = field(default_factory=list)


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPH = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_MD_CODE = re.compile(r"`([^`]*)`")


def strip_markdown(line: str) -> str:
    line = _MD_IMAGE.sub("", line)
    line = _MD_LINK.sub(r"\1", line)
    line = _MD_CODE.sub(r"\1", line)
    for _ in range(2):
        line = _MD_EMPH.sub(r"\2", line)
    line = re.sub(r"^\s*>\s?", "", line)                 # block quotes
    line = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", line)  # list markers
    return line.strip()


def _ensure_terminal(s: str) -> str:
    s = s.strip()
    if s and not re.search(r"[.!?…][\"'”’»)\]]*$", s):
        s += "."
    return s


def parse_markdown(text: str, default_title: str = "") -> List[Chapter]:
    """Split at '# ' headings. Sub-headings become their own spoken paragraphs.

    List items and headings get a full stop so they end a sentence (the
    chunker only cuts after sentence-ending punctuation)."""
    chapters: List[Chapter] = []
    cur: Optional[Chapter] = None
    buf: List[str] = []

    def flush():
        if buf and cur is not None:
            para = " ".join(x for x in buf if x)
            if para.strip():
                cur.paragraphs.append(Paragraph(para.strip()))
        buf.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if re.match(r"^#\s+", line):
            flush()
            cur = Chapter(len(chapters) + 1, strip_markdown(line.lstrip("#").strip()))
            chapters.append(cur)
            continue
        if cur is None:
            if not line.strip():
                continue
            cur = Chapter(1, default_title or "Opening")
            chapters.append(cur)
        if re.match(r"^#{2,6}\s+", line):
            flush()
            h = strip_markdown(line.lstrip("#").strip())
            if h:
                cur.paragraphs.append(Paragraph(_ensure_terminal(h), "heading"))
            continue
        if re.match(r"^\s*([-*_]\s*){3,}$", line) or re.match(r"^\s*\|", line):
            flush()                                      # rules and tables are not read
            continue
        if not line.strip():
            flush()
            continue
        is_item = bool(re.match(r"^\s*(?:[-*+•]|\d+[.)])\s+", line))
        s = strip_markdown(line)
        if is_item:
            flush()
            buf.append(_ensure_terminal(s))
            flush()
        else:
            buf.append(s)
    flush()
    return [c for c in chapters if c.paragraphs or c.title]


def load_book_text(book_dir: Path) -> List[Chapter]:
    """chapters/*.md|*.txt (one file per chapter, sorted) or manuscript.md (split at '# ')."""
    chap_dir = book_dir / "chapters"
    if chap_dir.is_dir():
        files = sorted(p for p in chap_dir.iterdir() if p.suffix.lower() in (".md", ".txt"))
        out: List[Chapter] = []
        for f in files:
            parsed = parse_markdown(f.read_text(encoding="utf-8"), default_title=_title_from_name(f.stem))
            for c in parsed:
                c.index = len(out) + 1
                out.append(c)
        return out
    for name in ("manuscript.md", "manuscript.txt"):
        p = book_dir / name
        if p.exists():
            return parse_markdown(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"{book_dir}: expected chapters/ or manuscript.md")


def _title_from_name(stem: str) -> str:
    return re.sub(r"^\d+[\s._-]*", "", stem).replace("_", " ").strip() or stem


# ------------------------------------------------------------------ chunking
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "e.g", "i.e", "fig", "mt", "inc",
    "ltd", "approx", "dept", "vol", "pp", "cf", "al", "lt", "sgt", "capt", "rev", "hon",
}
_CLOSERS = "\"'”’»)]"
# Sentence end = one or more of . ! ? …  then optional closing quotes/brackets, then space.
_SENT_END = re.compile(r"[.!?…]+[" + re.escape(_CLOSERS) + r"]*(?=\s+|$)")


def split_sentences(paragraph: str) -> List[str]:
    """Cut ONLY after . ! ? … plus optional closing quote(s). Never at a bare quote."""
    text = re.sub(r"\s+", " ", paragraph).strip()
    out, start = [], 0
    for m in _SENT_END.finditer(text):
        end = m.end()
        seg = text[start:end]
        if m.group(0).startswith(".") and m.group(0).rstrip(_CLOSERS) == ".":
            word = re.search(r"([A-Za-z.]+)\.$", text[:m.start() + 1])
            w = word.group(1).lower().rstrip(".") if word else ""
            nxt = text[end:].lstrip()[:1]
            prev = text[:m.start()].split()
            is_initial = (word is not None and re.fullmatch(r"[A-Z]", word.group(1) or "")
                          and len(prev) >= 2 and prev[-2][:1].isupper())
            num_abbrev = w == "no" and nxt.isdigit()
            if w in _ABBREV or is_initial or num_abbrev:
                continue                                  # Dr. / John F. Kennedy / No. 5
        if seg.strip():
            out.append(seg.strip())
        start = end
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _split_long(sentence: str, hard_max: int) -> List[str]:
    """Last resort for monster sentences: split at ; : — – , (never at quotes)."""
    if len(sentence) <= hard_max:
        return [sentence]
    for pat in (r"(?<=[;:])\s+", r"\s+(?=[—–]\s)|(?<=[—–])\s+", r"(?<=,)\s+"):
        parts = [p for p in re.split(pat, sentence) if p.strip()]
        if len(parts) > 1:
            out, cur = [], ""
            for p in parts:
                if cur and len(cur) + 1 + len(p) > hard_max:
                    out.append(cur)
                    cur = p
                else:
                    cur = f"{cur} {p}".strip()
            if cur:
                out.append(cur)
            res = []
            for o in out:
                res.extend(_split_long(o, hard_max) if len(o) > hard_max and o != sentence else [o])
            return res
    return [sentence]                                     # no safe cut point: keep whole


@dataclass
class Chunk:
    id: str                     # "c03_0012"
    chapter: int
    text: str                   # original text (what QC compares against)
    tts_text: str = ""          # respelled / numbers expanded
    pause_after: float = 0.4    # seconds of silence after this chunk
    long_split: bool = False


def chunk_chapter(ch: Chapter, max_chars: int = 300, hard_max: int = 480,
                  sentence_pause: float = 0.35, paragraph_pause: float = 0.85,
                  title_pause: float = 1.6) -> List[Chunk]:
    chunks: List[Chunk] = []

    def add(text, pause, long_split=False):
        chunks.append(Chunk(f"c{ch.index:02d}_{len(chunks):04d}", ch.index, text, pause_after=pause,
                            long_split=long_split))

    if ch.title:
        add(_ensure_terminal(ch.title), title_pause)
    for para in ch.paragraphs:
        sents = []
        for s in split_sentences(para.text):
            parts = _split_long(s, hard_max)
            sents.extend((p, len(parts) > 1) for p in parts)
        cur, cur_long = "", False
        for s, was_long in sents:
            if cur and len(cur) + 1 + len(s) > max_chars:
                add(cur, sentence_pause, cur_long)
                cur, cur_long = s, was_long
            else:
                cur = f"{cur} {s}".strip()
                cur_long = cur_long or was_long
        if cur:
            add(cur, paragraph_pause if para.kind == "body" else title_pause * 0.6, cur_long)
    if chunks:
        chunks[-1].pause_after = 0.0
    return chunks


# ------------------------------------------------------------------ respell
def apply_respell(text: str, respell: Dict[str, str]) -> str:
    """Whole-word, case-insensitive; keeps a leading capital. TTS text only."""
    for src, dst in sorted(respell.items(), key=lambda kv: -len(kv[0])):
        pat = re.compile(r"(?<![\w])" + re.escape(src) + r"(?![\w])", re.IGNORECASE)

        def rep(m, dst=dst):
            w = m.group(0)
            if w.isupper() and len(w) > 1:
                return dst.upper()
            if w[0].isupper():
                return dst[0].upper() + dst[1:]
            return dst
        text = pat.sub(rep, text)
    return text


# ------------------------------------------------------------------ numbers
_N2W_LANG = {"en": "en", "de": "de", "fr": "fr", "es": "es", "it": "it", "pt": "pt", "ru": "ru",
             "ja": "ja", "ko": "ko"}
_CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"),
             "€": ("euro", "euros", "cent", "cents")}


def _n2w(n, lang, **kw):
    from num2words import num2words
    return num2words(n, lang=_N2W_LANG.get(lang, "en"), **kw)


def numbers_to_words(text: str, lang: str = "en") -> str:
    """Spell out numbers. English gets currency/percent/ordinal/year/time rules;
    other languages get plain cardinals and decimals."""
    try:
        import num2words  # noqa: F401
    except ImportError:
        return text
    if lang not in _N2W_LANG:
        return text

    # phone / helpline numbers are read digit by digit
    def spell(s):
        return " ".join(_n2w(int(d), lang) for d in re.sub(r"\D", "", s))
    text = re.sub(r"(?<![\w.])\+?\d{1,4}(?:[-. ]\d{2,4}){2,}(?![\w.]\d)", lambda m: spell(m.group(0)), text)

    if lang == "en":
        text = re.sub(r"(?i)\b((?:call|text|dial|phone|ring)(?:ing)?\s+)(\d{3,})\b",
                      lambda m: m.group(1) + spell(m.group(2)), text)

        def money(m):
            sym, whole, frac, scale = m.group(1), m.group(2).replace(",", ""), m.group(3), m.group(4)
            one, many, sub1, subn = _CURRENCY[sym]
            if scale:
                amount = whole + (("." + frac) if frac else "")
                return f"{_n2w(float(amount) if frac else int(whole), lang)} {scale} {many}"
            w = int(whole)
            s = f"{_n2w(w, lang)} {one if w == 1 else many}"
            if frac and int(frac):
                c = int(frac.ljust(2, "0")[:2])
                s += f" and {_n2w(c, lang)} {sub1 if c == 1 else subn}"
            return s
        text = re.sub(r"([$£€])(\d[\d,]*)(?:\.(\d{1,2}))?(?:\s?(million|billion|thousand|trillion))?\b",
                      money, text)
        text = re.sub(r"(\d+(?:\.\d+)?)\s?%",
                      lambda m: f"{_num(m.group(1), lang)} percent", text)
        text = re.sub(r"\b(\d+)(st|nd|rd|th)\b",
                      lambda m: _n2w(int(m.group(1)), lang, to="ordinal"), text)
        text = re.sub(r"\b(\d{1,2}):(\d{2})\s?([ap])\.?m\.?",
                      lambda m: _time(m, lang), text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: _time(m, lang), text)
        text = re.sub(r"\b(1[1-9]\d\d|20\d\d)s\b",
                      lambda m: _year_plural(int(m.group(1)), lang), text)
        text = re.sub(r"(?<![\d,.])(1[1-9]\d\d|20\d\d)(?!\d|,\d|\.\d)",
                      lambda m: _n2w(int(m.group(1)), lang, to="year"), text)
        text = re.sub(r"\b(\d+)\s?[-–]\s?(\d+)\b",
                      lambda m: f"{_n2w(int(m.group(1)), lang)} to {_n2w(int(m.group(2)), lang)}", text)
    text = re.sub(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])",
                  lambda m: _num(m.group(0), lang), text)
    return text


def _num(s: str, lang: str) -> str:
    s = s.replace(",", "") if lang == "en" else s
    if "." in s:
        whole, frac = s.split(".", 1)
        point = {"en": "point", "de": "Komma", "fr": "virgule", "es": "coma", "it": "virgola",
                 "pt": "vírgula", "ru": "запятая"}.get(lang, "point")
        return f"{_n2w(int(whole or 0), lang)} {point} " + " ".join(_n2w(int(d), lang) for d in frac)
    return _n2w(int(s), lang)


def _time(m, lang):
    h, mi = int(m.group(1)), int(m.group(2))
    s = _n2w(h, lang)
    if mi:
        s += (" oh " + _n2w(mi, lang)) if mi < 10 else (" " + _n2w(mi, lang))
    if m.lastindex and m.lastindex >= 3 and m.group(3):
        s += " a.m." if m.group(3).lower() == "a" else " p.m."
    return s


def _year_plural(y, lang):
    w = _n2w(y, lang, to="year")
    if w.endswith("y"):
        return w[:-1] + "ies"
    return w + "s"


def tts_text(text: str, lang: str, respell: Dict[str, str], expand_numbers: bool = True) -> str:
    t = apply_respell(text, respell)
    if expand_numbers:
        t = numbers_to_words(t, lang)
    t = unicodedata.normalize("NFC", t)
    return t
