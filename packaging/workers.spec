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

NOTE: this spec is kept in sync with src/workers/*.py. After the 966ce94 scope
reset the shipping pipeline is 4 stages (ingest, classify, colour, timeline);
the transcribe/vision/brief/soundbites/matching workers and their ML/network
deps were removed and are intentionally NOT bundled (see docs/archive/). When a
deferred stage returns, re-add its module to hiddenimports and its deps here.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

SRC_WORKERS = str(Path(SPECPATH).parent / "src" / "workers")

block_cipher = None

# OpenCV ships the Haar cascade XMLs as package data (cv2/data/*.xml) and its
# native module as a dynamic lib. classify.py loads
# `cv2.data.haarcascades + "haarcascade_frontalface_default.xml"` at runtime, so
# both the data files and the binary must be collected — otherwise every
# classify operation raises ModuleNotFoundError/FileNotFound inside the bundle.
cv2_datas = collect_data_files("cv2")
cv2_binaries = collect_dynamic_libs("cv2")

a = Analysis(
    [str(Path(SRC_WORKERS) / "bridge.py")],
    pathex=[SRC_WORKERS],
    binaries=cv2_binaries,
    datas=cv2_datas,
    hiddenimports=[
        # Cairn worker modules imported by bridge.py and dispatched by message
        # type. Keep this list in lockstep with src/workers/*.py.
        "ingest",
        "classify",
        "timeline",
        "sidecar",
        "resolve_api",
        # resolve_import is a LEGACY standalone subprocess entrypoint (spawned by
        # the Electron main process, not a bridge handler). Bundled for the
        # fallback import path; slated for removal (see fix_plan.md).
        "resolve_import",
        # Classify stage runtime deps (OpenCV face + motion, numpy maths).
        "cv2",
        "cv2.data",
        "numpy",
        # Ingest stage — trash empty Sony shell directories (never hard-delete).
        # send2trash picks its platform backend at import time; bundle the mac +
        # win backends (Cairn ships Mac/Windows) plus the shared helpers. NOTE:
        # the backend modules are send2trash.mac / send2trash.win (NOT the older
        # plat_osx / plat_win names, which no longer exist).
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
        # Deferred-stage ML/network deps (stages 3-8, stripped in 966ce94).
        # Re-add the relevant hiddenimports above when those stages return.
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
