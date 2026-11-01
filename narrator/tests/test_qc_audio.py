import numpy as np
import pytest

from narrate import audio
from narrate.qc import QCConfig, audio_checks, edit_ops, judge, normalize_words

SR = 24000


def speechy(seconds, seed=0, level=0.1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2
    return (level * env * np.sin(2 * np.pi * 180 * t) + 0.01 * rng.standard_normal(len(t))).astype(np.float32)


def test_normalize_and_wer():
    assert normalize_words("It's 1984, OK? Dr. Who—really!") == \
        ["it's", "nineteen", "eighty", "four", "okay", "doctor", "who", "really"]
    assert edit_ops("a b c d".split(), "a x c d e".split()) == (2, 1, 0, 1)


def test_judge_passes_good_and_fails_rambling():
    text = "The quick brown fox jumps over the lazy dog near the river bank."
    wav = speechy(len(text) / 14.5)
    good = judge(text, "The quick brown fox jumps over the lazy dog near the riverbank.", wav, SR, "en",
                 QCConfig())
    assert good.ok, good.reasons            # one compound-word slip is allowed
    bad = judge(text, text + " the lazy dog the lazy dog the lazy dog", speechy(14), SR, "en", QCConfig())
    assert not bad.ok
    assert any("wer" in r for r in bad.reasons) and any("too long" in r for r in bad.reasons)
    assert bad.score > good.score


def test_internal_silence_and_click_detected():
    w = np.concatenate([speechy(2), np.zeros(int(3 * SR), np.float32), speechy(2)])
    assert audio_checks(w, SR)["max_gap"] >= 2.9
    c = speechy(2, level=0.05)
    c[:50] = 0.9
    assert audio_checks(c, SR)["start_click"] > 8


def test_limiter_respects_ceiling():
    x = speechy(5, level=0.3)
    x[SR] = 1.0
    y = audio.limit(x, SR, 0.5)
    assert np.max(np.abs(y)) <= 0.5 + 1e-6
    # far away from the spike the signal is untouched
    assert np.allclose(y[3 * SR:], x[3 * SR:])


def test_master_encode_measure_acx(tmp_path):
    pytest.importorskip("soundfile")
    try:
        audio.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("no ffmpeg")
    p = audio.PLATFORMS["acx"]
    raw = audio.assemble([(speechy(4, 1, 0.4), 0.4), (speechy(3, 2, 0.05), 0.0)], SR, p.head_s, p.tail_s)
    raw[SR * 2] = 0.99
    res = audio.master_and_encode_all([(raw, SR, tmp_path / "01.mp3", {"title": "One"})], p)[0]
    assert res["ok"], res
    assert -23 <= res["rms_db"] <= -18 and res["peak_db"] <= -3.0
