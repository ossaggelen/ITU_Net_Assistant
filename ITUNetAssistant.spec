# -*- mode: python ; coding: utf-8 -*-

import os
import PyInstaller
import sys
from PyInstaller.utils.hooks import collect_data_files

tkinter_runtime_hook = os.path.join(
    os.path.dirname(PyInstaller.__file__), "hooks", "rthooks", "pyi_rth__tkinter.py"
)

def collect_tree(source, destination, python_only=False):
    files = []
    for root, directories, filenames in os.walk(source):
        directories[:] = [name for name in directories if name != "__pycache__" and name.lower() != "demos"]
        for filename in filenames:
            if python_only and not filename.endswith(".py"):
                continue
            source_path = os.path.join(root, filename)
            relative_path = os.path.relpath(source_path, source)
            files.append((source_path, os.path.join(destination, os.path.dirname(relative_path))))
    return files

tcl_root = os.path.join(sys.prefix, "tcl")
python_tkinter = os.path.join(sys.prefix, "Lib", "tkinter")

a = Analysis(
    ['ITU_Net_Assistant.pyw'],
    pathex=[],
    binaries=[
        (os.path.join(sys.prefix, "DLLs", name), ".")
        for name in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll")
    ],
    datas=[('icon.png', '.'), ('icon.ico', '.')]
          + collect_data_files('customtkinter')
          + collect_tree(python_tkinter, "tkinter", python_only=True)
          + collect_tree(os.path.join(tcl_root, "tcl8.6"), "_tcl_data")
          + collect_tree(os.path.join(tcl_root, "tk8.6"), "_tk_data"),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[tkinter_runtime_hook],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ITUNetAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon=['icon.ico'],
)
