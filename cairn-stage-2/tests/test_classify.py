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


def test_parse_handles_leading_silence_with_no_start_event():
    # ffmpeg omits silence_start at t=0 for a clip that opens in silence: only the
    # silence_end is emitted. 10s clip, 2s leading + 1s mid silence -> 0.7 speech.
    stderr = (
        "silence_end: 2.0 | silence_duration: 2.0\n"
        "silence_start: 5.0\n"
        "silence_end: 6.0 | silence_duration: 1.0\n"
    )
    assert classify.parse_silence_ratio(stderr, 10.0) == pytest.approx(0.7)


def test_parse_leading_and_trailing_silence_without_explicit_starts():
    # Leading silence (no start) AND trailing unclosed silence. 10s clip:
    # 0-1s silent, 9s-end silent -> 8s speech -> 0.8.
    stderr = "silence_end: 1.0 | silence_duration: 1.0\nsilence_start: 9.0\n"
    assert classify.parse_silence_ratio(stderr, 10.0) == pytest.approx(0.8)


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


def test_probe_duration_rejects_na(monkeypatch):
    # ffprobe prints "N/A" for containers without a duration tag — must fail loud,
    # never let a non-numeric duration flow into the speech-ratio math.
    class FakeResult:
        returncode = 0
        stdout = "N/A\n"
        stderr = ""

    monkeypatch.setattr(classify.subprocess, "run", lambda *a, **k: FakeResult())
    with pytest.raises(ValueError):
        classify.probe_duration("/fake/clip.mxf")


def test_probe_duration_rejects_zero(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = "0.0\n"
        stderr = ""

    monkeypatch.setattr(classify.subprocess, "run", lambda *a, **k: FakeResult())
    with pytest.raises(ValueError):
        classify.probe_duration("/fake/clip.mp4")


def test_run_silencedetect_raises_on_ffmpeg_error(monkeypatch):
    import subprocess

    # Simulate ffmpeg exiting with returncode=1 without raising — check=True must raise.
    class FakeResult:
        returncode = 1
        stderr = ""
        stdout = ""

    def fake_run(*args, **kwargs):
        result = FakeResult()
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(result.returncode, args[0][0])
        return result

    monkeypatch.setattr(classify.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        classify.run_silencedetect("/fake/clip.mp4")
