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

NOTE: keep hiddenimports in sync with src/workers/*.py. The transcribe, vision,
brief, soundbites and matching workers were removed in the scope reset, so they
and their ML/network deps are intentionally not bundled.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

SRC_WORKERS = str(Path(SPECPATH).parent / "src" / "workers")

block_cipher = None

# classify.py loads cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
# at runtime, so OpenCV's data files must ship alongside its native module.
cv2_datas = collect_data_files("cv2")
cv2_binaries = collect_dynamic_libs("cv2")

a = Analysis(
    [str(Path(SRC_WORKERS) / "bridge.py")],
    pathex=[SRC_WORKERS],
    binaries=cv2_binaries,
    datas=cv2_datas,
    hiddenimports=[
        # Cairn worker modules imported by bridge.py and dispatched by message type.
        "ingest",
        "classify",
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
        # Classify runtime deps (OpenCV face + motion, numpy maths).
        "cv2",
        "cv2.data",
        "numpy",
        # Ingest trashes empty Sony shell folders (never hard-deletes). send2trash
        # picks its platform backend at import time, so bundle both shipped OSes.
        "send2trash",
        "send2trash.exceptions",
        "send2trash.util",
        "send2trash.plat_other",
        "send2trash.mac",
        "send2trash.mac.legacy",
        "send2trash.mac.modern",
        "send2trash.win",
        "send2trash.win.legacy",
        "send2trash.win.modern",
        "send2trash.win.IFileOperationProgressSink",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev/test tooling and heavy libs that no shipping worker imports.
        "pytest",
        "hypothesis",
        "IPython",
        "ipykernel",
        "notebook",
        "tkinter",
        "matplotlib",
        "scipy",
        "torch",
        "onnxruntime",
        "ctranslate2",
        "faster_whisper",
        "assemblyai",
        "anthropic",
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
