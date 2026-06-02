import pytest
from helpers import classify

SILENCE_STDERR = """\
[silencedetect @ 0x1] silence_start: 0.0
[silencedetect @ 0x1] silence_end: 1.0 | silence_duration: 1.0
[silencedetect @ 0x1] silence_start: 8.0
[silencedetect @ 0x1] silence_end: 9.0 | silence_duration: 1.0
"""


def test_parse_silence_ratio_subtracts_silent_time():
    # 10s clip, 2s silent -> 0.8 speech ratio
    assert classify.parse_silence_ratio(SILENCE_STDERR, 10.0) == pytest.approx(0.8)


def test_parse_handles_trailing_unclosed_silence():
    stderr = "silence_start: 9.0\n"  # runs to end
    assert classify.parse_silence_ratio(stderr, 10.0) == pytest.approx(0.9)


def test_parse_no_silence_is_full_speech():
    assert classify.parse_silence_ratio("", 10.0) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "ratio,label",
    [
        (0.9, "A-ROLL"),
        (0.45, "A-ROLL"),
        (0.44, "AMBIGUOUS"),
        (0.11, "AMBIGUOUS"),
        (0.10, "B-ROLL"),
        (0.0, "B-ROLL"),
    ],
)
def test_classify_thresholds(ratio, label):
    assert classify.classify(ratio) == label


def test_parse_rejects_bad_duration():
    with pytest.raises(ValueError):
        classify.parse_silence_ratio("", 0.0)
