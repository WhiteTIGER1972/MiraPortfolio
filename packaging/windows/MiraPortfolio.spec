from pathlib import Path


SPEC_DIRECTORY = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIRECTORY.parents[1]
MIGRATIONS_DIRECTORY = PROJECT_ROOT / "migrations"

migration_data = [
    (
        str(source),
        str(Path("migrations") / source.parent.relative_to(MIGRATIONS_DIRECTORY)),
    )
    for source in sorted(MIGRATIONS_DIRECTORY.rglob("*"))
    if source.is_file()
    and "__pycache__" not in source.parts
    and source.suffix.casefold() not in {".pyc", ".pyo"}
]

# Built-in Pydantic/Pygments hooks otherwise reach development and image stacks that no
# production module imports. Real launch verification covers this deliberately narrow set.
unused_runtime_modules = [
    "PIL",
    "mypy",
    "numpy",
    "orjson",
    "pydantic.mypy",
    "pydantic.v1._hypothesis_plugin",
    "pydantic.v1.mypy",
    "pygments",
    "rich",
]

analysis = Analysis(
    [str(PROJECT_ROOT / "app" / "__main__.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[(str(PROJECT_ROOT / "alembic.ini"), "."), *migration_data],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=unused_runtime_modules,
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="MiraPortfolio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MiraPortfolio",
)
