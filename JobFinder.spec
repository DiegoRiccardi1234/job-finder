# JobFinder.spec — PyInstaller config for the standalone Windows build.
# Build:  pyinstaller JobFinder.spec --noconfirm

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

hiddenimports: list[str] = []
hiddenimports += collect_submodules("app.migrations")
hiddenimports += collect_submodules("app.providers")
hiddenimports += [
    "openai",
    "groq",
    "anthropic",
    "google.generativeai",
    "cerebras.cloud.sdk",
    "uvicorn.logging",
    "uvicorn.protocols",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

datas: list[tuple[str, str]] = [
    ("web", "web"),
    ("app/prompts", "app/prompts"),
]

# jobspy ships templates / static data; pull them in if available.
try:
    datas += collect_data_files("jobspy", include_py_files=False)
except Exception:
    pass

a = Analysis(
    ["scripts/launch_exe.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "matplotlib",
        "tkinter",
        "PyQt5",
        "PySide2",
        "PyQt6",
        "PySide6",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JobFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # keep the console: stack traces visible if the app crashes
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JobFinder",
)
