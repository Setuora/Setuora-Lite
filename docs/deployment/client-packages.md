# Windows Client Package

Build the self-extracting installer with:

```powershell
py -3.11 scripts\build_client_packages.py --version 1.0.0
```

The output contains one `Setuora-Lite-1.0.0-windows.cmd` and a SHA256 checksum
file. Runtime data, `.env`, archives, caches, and credentials are excluded.

The same installer handles a new installation and an update. Updates stop the
startup task, replace reviewed release files, reinstall hash-locked Python
dependencies, restart Lite, and verify health while preserving runtime data.
