from PyInstaller.utils.hooks import collect_all

windows_toasts_data, windows_toasts_binaries, windows_toasts_hidden = collect_all('windows_toasts')

a = Analysis(
    ['launch_mcq_maker.pyw'],
    pathex=[],
    binaries=windows_toasts_binaries,
    datas=[
        ('mcq_maker/assets', 'mcq_maker/assets'),
        ('mcq_maker/resources', 'mcq_maker/resources'),
    ] + windows_toasts_data,
    hiddenimports=windows_toasts_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
# Qt on Windows uses the operating system ICU. The development host's PATH also
# contains an unrelated Poppler ICU with the same DLL name; never ship that copy.
a.binaries = [entry for entry in a.binaries if entry[0].casefold() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MCQ Maker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['mcq_maker/assets/app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MCQ Maker',
)
