from narrate.text import (Chapter, Paragraph, apply_respell, chunk_chapter, numbers_to_words,
                          parse_markdown, split_sentences)


def test_cuts_only_after_sentence_end_plus_optional_quote():
    s = 'He said "stop" and walked on. "Why?" she asked. It was 3.5 km… Then silence.'
    assert split_sentences(s) == ['He said "stop" and walked on.', '"Why?"', 'she asked.',
                                  'It was 3.5 km…', 'Then silence.']


def test_never_cuts_at_bare_closing_quote():
    s = 'The word "freedom" means different things” to different people. Next.'
    out = split_sentences(s)
    assert out == ['The word "freedom" means different things” to different people.', 'Next.']


def test_closing_quote_after_full_stop_stays_with_sentence():
    assert split_sentences('“I know.” Then he left.') == ['“I know.”', 'Then he left.']


def test_abbreviations_and_initials():
    s = "Dr. Smith met John F. Kennedy at No. 5 Main St. in May. She said no. Then left."
    assert split_sentences(s) == ["Dr. Smith met John F. Kennedy at No. 5 Main St. in May.",
                                  "She said no.", "Then left."]


def test_chunks_pack_sentences_and_never_split_mid_sentence():
    sents = [f"This is sentence number {i} in a paragraph of moderate length." for i in range(20)]
    ch = Chapter(1, "One", [Paragraph(" ".join(sents)), Paragraph("Short one.")])
    chunks = chunk_chapter(ch, max_chars=200)
    assert chunks[0].text == "One."
    body = chunks[1:]
    assert all(len(c.text) <= 200 for c in body)
    assert all(c.text.endswith(".") for c in body)
    joined = " ".join(c.text for c in body)
    assert joined == " ".join(sents) + " Short one."
    assert chunks[-1].pause_after == 0.0
    assert body[-2].pause_after > body[0].pause_after   # paragraph pause > sentence pause


def test_monster_sentence_split_at_clause_punctuation():
    long = "; ".join(["a clause that goes on for a while"] * 30) + "."
    ch = Chapter(2, "", [Paragraph(long)])
    chunks = chunk_chapter(ch, max_chars=300, hard_max=400)
    assert len(chunks) > 1 and all(len(c.text) <= 400 for c in chunks)
    assert all(c.long_split for c in chunks)


def test_respell_whole_word_and_case():
    r = {"Machiavelli": "Mackiavelli", "Nietzsche": "Neecha"}
    assert apply_respell("Machiavelli and machiavellian; NIETZSCHE.", r) == \
        "Mackiavelli and machiavellian; NEECHA."


def test_numbers_english():
    t = numbers_to_words("In 1984 it cost $12.50, rose 45% by the 3rd week, 10-15 people, 2,500 total.")
    assert "nineteen eighty-four" in t
    assert "twelve dollars and fifty cents" in t
    assert "forty-five percent" in t and "third" in t
    assert "ten to fifteen" in t and "two thousand, five hundred" in t
    assert not any(ch.isdigit() for ch in t)


def test_helplines_read_digit_by_digit():
    t = numbers_to_words("Call 988 or 1-800-273-8255 today.")
    assert "nine eight eight" in t
    assert "one eight zero zero two seven three eight two five five" in t


def test_markdown_parse():
    md = "# Chapter 1: Start\n\nSome **bold** text and [a link](http://x).\n\n## Key idea\n\n- item one\n- item two\n\n| a | b |\n|---|---|\n\n# Chapter 2\n\nMore."
    chs = parse_markdown(md)
    assert [c.title for c in chs] == ["Chapter 1: Start", "Chapter 2"]
    texts = [p.text for p in chs[0].paragraphs]
    assert texts == ["Some bold text and a link.", "Key idea.", "item one.", "item two."]
