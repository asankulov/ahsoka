"""Tests for ahsoka.text_utils.slice_utf16."""
from ahsoka.text_utils import slice_utf16


def test_slice_utf16_emoji_before_url_extracts_full_url():
    """The user's motivating example: emoji occupies 2 UTF-16 units, plain str
    slicing by those offsets truncates the URL — slice_utf16 must not."""
    text = "\U0001F680 Apply https://jobs.io now"
    assert slice_utf16(text, 9, 15) == "https://jobs.io"


def test_slice_utf16_ascii_only_matches_plain_str_slicing():
    text = "Apply at https://jobs.example.com/1 today"
    offset = text.index("https://jobs.example.com/1")
    length = len("https://jobs.example.com/1")
    assert slice_utf16(text, offset, length) == text[offset:offset + length]


def test_slice_utf16_multiple_astral_chars_compounding_offset():
    """Two astral emoji before the entity — offset must account for 2 UTF-16
    units each (4 units total), not 2 Python str indices."""
    text = "\U0001F680\U0001F525 https://x.io end"
    # UTF-16 units: 2 (rocket) + 2 (fire) + 1 (space) = 5, then URL starts.
    url = "https://x.io"
    assert slice_utf16(text, 5, len(url)) == url


def test_slice_utf16_entity_at_offset_zero():
    text = "https://start.io rest of text"
    url = "https://start.io"
    assert slice_utf16(text, 0, len(url)) == url


def test_slice_utf16_offset_plus_length_beyond_text_clamps():
    text = "short"
    # Way beyond the end — should clamp like plain str slicing, not raise.
    assert slice_utf16(text, 2, 1000) == "ort"


def test_slice_utf16_offset_beyond_text_returns_empty():
    text = "short"
    assert slice_utf16(text, 100, 5) == ""


def test_slice_utf16_zero_length_returns_empty_string():
    text = "https://jobs.io"
    assert slice_utf16(text, 0, 0) == ""


def test_slice_utf16_negative_length_returns_empty_without_raising():
    text = "https://jobs.io"
    assert slice_utf16(text, 5, -3) == ""


def test_slice_utf16_offset_splitting_surrogate_pair_does_not_raise():
    """An offset that lands inside an astral char's surrogate pair would
    normally corrupt UTF-16 decoding; errors='ignore' must drop the unpaired
    surrogate instead of raising UnicodeDecodeError."""
    text = "\U0001F680abc"  # rocket (2 UTF-16 units) + 'abc'
    # offset=1 splits the rocket's surrogate pair in half: the low surrogate
    # half is dropped by decode(errors="ignore"), leaving just "ab".
    assert slice_utf16(text, 1, 3) == "ab"


def test_slice_utf16_lone_surrogate_in_input_does_not_raise():
    """A lone (unpaired) surrogate already present in `text` — e.g. from
    malformed upstream input — must not raise UnicodeEncodeError on encode
    (text_utils encodes with errors='surrogatepass'), and is then dropped by
    the decode(errors='ignore') step, same as a split surrogate pair."""
    text = "a\ud800b"
    assert slice_utf16(text, 0, len(text)) == "ab"
