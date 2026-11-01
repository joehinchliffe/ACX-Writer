"""End-to-end orchestration with a fake engine and a fake Whisper (no GPU)."""
import json

import numpy as np
import pytest

from narrate import job as jobmod
from narrate.engine import SynthStats, Take

SR = 24000


class FakeEngine:
    sr, upsample = SR, 1920

    def __init__(self, cfg):
        self.calls = []

    def voice(self, wav, text):
        return object()

    def free_cache(self):
        pass

    def synthesize(self, texts, voice, lang, progress=None):
        self.calls.append(list(texts))
        takes = []
        for t in texts:
            n = int(len(t) / 14.5 * SR)
            x = 0.1 * np.sin(2 * np.pi * 200 * np.arange(n) / SR).astype(np.float32)
            takes.append(Take(x, n // self.upsample, False))
        st = SynthStats(audio_s=sum(len(t.wav) for t in takes) / SR, wall_s=1.0, batch_size_final=96)
        return takes, st


class FakeWhisper:
    """Garbles one chunk on the first two passes, so it is redone twice."""

    def __init__(self, *a, **k):
        self.seen = {}

    def transcribe_many(self, wavs, sr, lang, hotwords=None):
        out = []
        for w in wavs:
            key = len(w)
            text = FakeWhisper.by_len.get(key, "")
            if "mackiavelli" in text.lower():          # sanity: QC must see the ORIGINAL spelling
                raise AssertionError("respelled text leaked into the comparison")
            self.seen[key] = self.seen.get(key, 0) + 1
            if "garble" in text and self.seen[key] <= 2:
                text = "something else entirely and repeated repeated repeated"
            out.append(text)
        return out


@pytest.fixture
def book(tmp_path, monkeypatch):
    import soundfile as sf
    b = tmp_path / "my-book"
    (b / "chapters").mkdir(parents=True)
    sf.write(str(b / "voice.wav"), (0.1 * np.random.default_rng(0).standard_normal(22 * SR)).astype(np.float32), SR)
    (b / "book.json").write_text(json.dumps({
        "title": "Test Book", "author": "A. Writer", "language": "english",
        "voice": {"wav": "voice.wav", "text": "reference words here"},
        "respell": {"Machiavelli": "Mackiavelli"}}))
    (b / "chapters" / "01 Start.md").write_text(
        "# Chapter One\n\nMachiavelli wrote in 1513. This sentence will garble at first.\n\nA new paragraph.")
    (b / "chapters" / "02 End.md").write_text("# Chapter Two\n\nThe end is near. Truly.")
    # map synthesized length -> what "Whisper" hears (the original text)
    from narrate.text import chunk_chapter, load_book_text, tts_text
    by_len = {}
    for ch in load_book_text(b):
        for c in chunk_chapter(ch):
            n = int(len(tts_text(c.text, "en", {"Machiavelli": "Mackiavelli"})) / 14.5 * SR)
            by_len[n] = c.text
    FakeWhisper.by_len = by_len
    monkeypatch.setattr(jobmod, "Engine", FakeEngine)
    monkeypatch.setattr(jobmod, "WhisperChecker", FakeWhisper)
    monkeypatch.setattr(jobmod, "check_reference", lambda *a, **k: {"ok": True, "warnings": [], "wer": 0})
    return b


def test_book_end_to_end(book, tmp_path):
    from narrate.engine import EngineConfig
    from narrate.qc import QCConfig
    n = jobmod.Narrator(EngineConfig(), QCConfig(), max_redos=3)
    out = tmp_path / "out"
    r = n.run_book(book, out)
    mp3s = sorted(p.name for p in (out / "my-book").glob("*.mp3"))
    assert mp3s == ["01 Chapter One.mp3", "02 Chapter Two.mp3"]
    assert all(f["ok"] for f in r["files"]), r["files"]
    assert r["redos"]["chunks_redone"] == 1 and r["redos"]["takes"] == 2
    assert r["redos"]["still_failing"] == []
    # the TTS saw the respelling and spelled-out year; QC compared against the original
    first = n.engine.calls[0]
    assert any("Mackiavelli wrote in fifteen thirteen." in t for t in first)
    # second run reuses every chunk from the cache: no synthesis at all
    n2 = jobmod.Narrator(EngineConfig(), QCConfig(), max_redos=3)
    n2.run_book(book, out)
    assert n2.engine.calls == []
