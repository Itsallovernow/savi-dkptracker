# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for dkp_client.exe

Builds a single-file Windows console executable that bundles:
- dkp_client.py (entry point)
- All local project modules (dkptrackerv3, models, client_config, etc.)
- Third-party dependencies: requests, rapidfuzz
- Python standard library

Requirements: 5.1, 5.2, 5.3, 5.4
"""

import os

block_cipher = None

# Collect all local .py modules that dkp_client.py depends on
local_modules = [
    'dkptrackerv3',
    'models',
    'client_config',
    'offline_queue',
    'api_client',
    'dkp_logger',
]

a = Analysis(
    ['dkp_client.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle seed_items.txt alongside the exe for item validation
        ('seed_items.txt', '.'),
        # Bundle items.zip (full EQEmu item database) for complete item lookup
        ('items.zip', '.'),
    ],
    hiddenimports=[
        # Local project modules
        'dkptrackerv3',
        'models',
        'client_config',
        'offline_queue',
        'api_client',
        'dkp_logger',
        # Third-party dependencies (Requirement 5.3)
        'requests',
        'requests.adapters',
        'requests.auth',
        'requests.cookies',
        'requests.exceptions',
        'requests.models',
        'requests.sessions',
        'requests.structures',
        'requests.utils',
        'urllib3',
        'charset_normalizer',
        'certifi',
        'idna',
        'rapidfuzz',
        'rapidfuzz.fuzz',
        'rapidfuzz.process',
        'rapidfuzz.utils',
        # Standard library modules used by the project
        'configparser',
        'hashlib',
        'json',
        'threading',
        'datetime',
        'collections',
        'glob',
        'shutil',
        're',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test modules and unnecessary packages to reduce exe size
        'pytest',
        'hypothesis',
        'unittest',
        'tkinter',
        'matplotlib',
        'numpy',
        'scipy',
        'PIL',
        'pystray',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='dkp_client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Requirement 5.2: Opens a console/terminal window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
