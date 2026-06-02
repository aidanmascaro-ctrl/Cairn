from dataclasses import dataclass

from helpers import transcribe


@dataclass
class FakeWord:
    word: str
    start: float
    end: float


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    words: list


@dataclass
class FakeInfo:
    language: str
    duration: float


class FakeModel:
    def __init__(self, segments, info):
        self._segments, self._info = segments, info
        self.called_with = None

    def transcribe(self, path, **kw):
        self.called_with = (path, kw)
        return iter(self._segments), self._info


def test_segments_to_record_flattens_words():
    segs = [
        FakeSegment(0.0, 1.0, " Hello ", [FakeWord(" Hello", 0.0, 0.5)]),
        FakeSegment(1.0, 2.0, " world ", [FakeWord(" world", 1.0, 1.5)]),
    ]
    rec = transcribe.segments_to_record(segs, FakeInfo("en", 2.0))
    assert rec["language"] == "en"
    assert rec["duration"] == 2.0
    assert [w["text"] for w in rec["words"]] == ["Hello", "world"]
    assert rec["words"][0] == {"text": "Hello", "start": 0.0, "end": 0.5}
    assert [s["text"] for s in rec["segments"]] == ["Hello", "world"]


def test_transcribe_file_requests_word_timestamps():
    model = FakeModel(
        [FakeSegment(0.0, 1.0, "hi", [FakeWord("hi", 0.0, 1.0)])], FakeInfo("en", 1.0)
    )
    rec = transcribe.transcribe_file("/x/clip.mp4", model)
    assert model.called_with[0] == "/x/clip.mp4"
    assert model.called_with[1]["word_timestamps"] is True
    assert rec["words"][0]["text"] == "hi"


def test_segments_with_no_words_still_records_segment():
    segs = [FakeSegment(0.0, 1.0, "ambient", None)]
    rec = transcribe.segments_to_record(segs, FakeInfo("en", 1.0))
    assert rec["words"] == []
    assert rec["segments"][0]["text"] == "ambient"
