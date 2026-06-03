"""Generate a multi-track FCPXML for the Peter Ryan reel.

A-roll = primary-storyline asset-clips (video + dialogue audio) on V1/A1.
B-roll = video-only <video> connected clips on lane 1 (-> V2), anchored to the
A-roll clip that contains each one's record frame. Frame-accurate, 25fps.

Connected-clip offset is expressed in the PARENT clip's source timebase:
    offset = parent.src_start + (broll_tl_pos - parent.tl_offset)
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

SPINE_ONLY = os.environ.get("SPINE_ONLY") == "1"

FPS = 25
V = "/Volumes/Crucial 4TB/260502 sample open day/01_VIDEOS"

# id -> (rel_path, total_frames, has_audio, audio_channels)
ASSETS = {
    "C3108": ("A_CAM/C3108.MP4", 4092, True, 4),
    "C3109": ("A_CAM/C3109.MP4", 4308, True, 4),
    "DJI_0346": ("C_CAM/DJI_0346.MOV", 1916, True, 2),
    "C3152": ("A_CAM/C3152.MP4", 132, True, 4),
    "C3115": ("A_CAM/C3115.MP4", 276, True, 4),
    "C3110": ("A_CAM/C3110.MP4", 312, True, 4),
    "C3113": ("A_CAM/C3113.MP4", 276, True, 4),
    "C3141": ("A_CAM/C3141.MP4", 252, True, 4),
    "C3149": ("A_CAM/C3149.MP4", 192, True, 4),
}

# A-roll spine: (ref, name, tl_offset, src_start, dur)  — all in frames
SPINE = [
    ("C3108", "identity", 0, 74, 179),
    ("C3108", "gallery-hub", 179, 1479, 301),
    ("C3108", "art-a", 480, 3235, 101),
    ("C3108", "art-b", 581, 3451, 118),
    ("C3109", "community", 699, 1735, 348),
]

# B-roll connected clips: (ref, name, parent_idx, tl_pos, src_start, dur)
BROLL = [
    ("DJI_0346", "DJI aerial - establish", 0, 45, 750, 134),
    ("C3152", "OPEN sign", 1, 185, 8, 115),
    ("C3115", "gallery interior", 1, 305, 20, 175),
    ("C3110", "hero piece on easel", 2, 485, 30, 75),
    ("C3113", "coastal painting", 2, 560, 40, 60),
    ("C3141", "painting process", 3, 620, 30, 79),
    ("DJI_0346", "DJI aerial - streets", 4, 705, 100, 165),
    ("C3149", "gallery shopfront", 4, 875, 10, 165),
]

SEQ_FRAMES = sum(s[4] for s in SPINE)  # 1047


def t(frames: int) -> str:
    """Frame count -> FCPXML rational seconds, frame-aligned."""
    return f"{frames}/{FPS}s"


def file_url(rel: str) -> str:
    return "file://" + quote(f"{V}/{rel}")


def build() -> str:
    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<!DOCTYPE fcpxml>")
    lines.append('<fcpxml version="1.9">')
    lines.append("  <resources>")
    lines.append(f'    <format id="r1" frameDuration="1/{FPS}s" width="2160" height="3840"/>')
    for aid, (rel, frames, has_audio, ch) in ASSETS.items():
        audio_attrs = (
            f' hasAudio="1" audioSources="1" audioChannels="{ch}" audioRate="48000"'
            if has_audio
            else ' hasAudio="0"'
        )
        lines.append(
            f'    <asset id="A_{aid}" name="{escape(aid)}" start="0s" '
            f'duration="{t(frames)}" hasVideo="1" format="r1"{audio_attrs}>'
        )
        lines.append(f'      <media-rep kind="original-media" src="{file_url(rel)}"/>')
        lines.append("    </asset>")
    lines.append("  </resources>")
    lines.append("  <library>")
    lines.append('    <event name="Cairn AutoCut">')
    lines.append('      <project name="CAIRN_AUTOCUT_PeterRyan_FCP">')
    lines.append(
        f'        <sequence format="r1" duration="{t(SEQ_FRAMES)}" tcStart="0s" tcFormat="NDF">'
    )
    lines.append("          <spine>")
    for idx, (ref, name, tl_off, src_start, dur) in enumerate(SPINE):
        children = [] if SPINE_ONLY else [b for b in BROLL if b[2] == idx]
        open_tag = (
            f'            <asset-clip ref="A_{ref}" offset="{t(tl_off)}" '
            f'name="{escape(name)}" start="{t(src_start)}" duration="{t(dur)}" '
            'audioRole="dialogue"'
        )
        if not children:
            lines.append(open_tag + "/>")
            continue
        lines.append(open_tag + ">")
        for bref, bname, _pi, tl_pos, bsrc, bdur in children:
            conn_off = src_start + (tl_pos - tl_off)
            lines.append(
                f'              <video ref="A_{bref}" lane="1" '
                f'offset="{t(conn_off)}" name="{escape(bname)}" '
                f'start="{t(bsrc)}" duration="{t(bdur)}"/>'
            )
        lines.append("            </asset-clip>")
    lines.append("          </spine>")
    lines.append("        </sequence>")
    lines.append("      </project>")
    lines.append("    </event>")
    lines.append("  </library>")
    lines.append("</fcpxml>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    suffix = "_spineonly" if SPINE_ONLY else ""
    out = Path(f".cache/CAIRN_AUTOCUT_PeterRyan{suffix}.fcpxml")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out.resolve()}  ({SEQ_FRAMES} frames = {SEQ_FRAMES / FPS:.2f}s)")
