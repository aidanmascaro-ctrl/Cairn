import pytest
from helpers import timecode as tc


def test_seconds_to_frames_rounds_to_nearest():
    assert tc.seconds_to_frames(2.0, 25) == 50
    assert tc.seconds_to_frames(2.02, 25) == 51  # 50.5 -> 51 (round half up via int(x + 0.5))
    assert tc.seconds_to_frames(0.0, 25) == 0


def test_frames_to_seconds():
    assert tc.frames_to_seconds(50, 25) == pytest.approx(2.0)


def test_timecode_to_frames_non_drop():
    assert tc.timecode_to_frames("00:00:02:00", 25) == 50
    assert tc.timecode_to_frames("00:00:00:24", 25) == 24
    assert tc.timecode_to_frames("01:00:00:00", 25) == 25 * 60 * 60


def test_frames_to_timecode_roundtrip():
    for frames in (0, 24, 25, 50, 9000, 90000):
        s = tc.frames_to_timecode(frames, 25)
        assert tc.timecode_to_frames(s, 25) == frames


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        tc.seconds_to_frames(-1, 25)
    with pytest.raises(ValueError):
        tc.timecode_to_frames("00:00:25", 25)  # wrong field count
    with pytest.raises(ValueError):
        tc.timecode_to_frames("00:00:00:25", 25)  # frame out of range


@pytest.mark.parametrize("fps", [29.97, 23.976, 59.94])
def test_drop_frame_fps_rejected(fps):
    # v1 supports non-drop-frame integer fps only; fractional rates must fail loud.
    with pytest.raises(ValueError):
        tc.timecode_to_frames("00:00:01:00", fps)
    with pytest.raises(ValueError):
        tc.frames_to_timecode(30, fps)
