import pytest
from helpers import assemble


def test_soundbite_to_mark_converts_seconds_to_frames():
    sb = assemble.Soundbite(clip_id="abc", src_in_sec=2.0, src_out_sec=4.0, fps=25)
    m = assemble.soundbite_to_mark(sb)
    assert m.clip_id == "abc"
    assert m.mark_in == 50
    assert m.mark_out == 99  # 100 (out) - 1 inclusive last frame


def test_soundbite_rejects_zero_or_negative_span():
    with pytest.raises(ValueError):
        assemble.soundbite_to_mark(
            assemble.Soundbite("abc", src_in_sec=4.0, src_out_sec=4.0, fps=25)
        )


def test_assembly_plan_emits_set_mark_then_append_in_order():
    sbs = [
        assemble.Soundbite("a", 1.0, 2.0, 25),
        assemble.Soundbite("b", 0.0, 1.0, 25),
    ]
    plan = assemble.assembly_plan(sbs)
    assert [p["set_mark"]["clip_id"] for p in plan] == ["a", "b"]
    assert plan[0]["set_mark"]["mark_in"] == 25
    assert plan[0]["set_mark"]["mark_out"] == 49
    assert plan[0]["append"]["clip_id"] == "a"


def test_soundbite_shorter_than_one_frame_raises():
    with pytest.raises(ValueError):
        assemble.soundbite_to_mark(assemble.Soundbite("x", 2.0, 2.01, 25))
