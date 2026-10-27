# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Cairn's Python workers bundle.

Produces a single-file 'workers' binary (bridge.py entry point) with all
worker modules and their dependencies statically linked.  The binary is
shipped inside the plugin's resources/ folder and replaces the Python
runtime requirement for end users.

Build (run from project root, active Python env must have all deps + pyinstaller):

    pyinstaller packaging/workers.spec \\
        --distpath packaging/dist \\
        --workpath packaging/build/pyinstaller \\
        --noconfirm

Outputs:
    packaging/dist/workers          macOS / Linux
    packaging/dist/workers.exe      Windows
"""

import sys
from pathlib import Path

SRC_WORKERS = str(Path(SPECPATH).parent / "src" / "workers")

block_cipher = None

a = Analysis(
    [str(Path(SRC_WORKERS) / "bridge.py")],
    pathex=[SRC_WORKERS],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Cairn worker modules — dispatched dynamically by bridge.py
        "ingest",
        "classify",
        "transcribe",
        "vision",
        "soundbites",
        "matching",
        "brief",
        "sidecar",
        "resolve_api",
        "resolve_import",
        "selects",
        "selects_learn",
        "selects_metrics",
        "selects_presets",
        "selects_score",
        "selects_timeline",
        "timeline",
        # silero-vad 6+ runtime (ONNX — no torch needed)
        "onnxruntime",
        "onnxruntime.capi",
        "onnxruntime.capi.onnxruntime_pybind11_state",
        # faster-whisper
        "ctranslate2",
        "faster_whisper",
        "faster_whisper.transcribe",
        "faster_whisper.audio",
        "faster_whisper.feature_extractor",
        "faster_whisper.tokenizer",
        # assemblyai HTTP client
        "assemblyai",
        "assemblyai.api",
        "assemblyai.transcriber",
        # anthropic HTTP client
        "anthropic",
        "anthropic._client",
        "anthropic.resources",
        # shared HTTP stack
        "httpx",
        "httpcore",
        "anyio",
        "anyio._backends._asyncio",
        "anyio._backends._trio",
        "sniffio",
        "certifi",
        "h11",
        "h2",
        # file utilities
        "send2trash",
        "send2trash.plat_other",
        "send2trash.plat_osx",
        "send2trash.plat_win",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # dev/test tooling — keep binary lean
        "pytest",
        "hypothesis",
        "IPython",
        "ipykernel",
        "notebook",
        "tkinter",
        "matplotlib",
        "PIL",
        "cv2",
        "scipy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="workers",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=str(Path(SPECPATH) / "entitlements.mac.plist"),
)
