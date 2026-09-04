---
inclusion: manual
---

# Build EXE — PyInstaller Build for dkp_client

When the user says "build the exe" or "build exe", execute these steps from the repository root:

## Step 1: Kill any running instance

The exe may be running and locked. Kill it first:

```
taskkill /f /im dkp_client.exe
```

Ignore errors if it's not running. Wait 2 seconds for the file handle to release.

## Step 2: Build with PyInstaller

```
pyinstaller --clean dkp_client.spec
```

This uses the spec file `dkp_client.spec` which builds a single-file console exe bundling all dependencies (rapidfuzz, requests, local modules, seed_items.txt, items.zip).

## Step 3: Report

The output exe is at:

```
dist\dkp_client.exe
```

Report whether the build succeeded. If it fails with a PermissionError on the exe, the file is still locked — ask the user to close it manually and retry.
