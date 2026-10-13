# 🌅 MindOS Sunrise Official Package Repository

Official binary package repository for **MindOS** (Sunrise and Paradise).

- `packages.json` is the index; every package file lives in **GitHub Releases** (`v1.0-chunk-NN`, up to 990 files each).
- New packages are built every day from **Debian 13 (trixie)** by the package factory (see below).

## 📦 What is a `.mind` package?
A `.mind` package is a high-speed, lightweight software container compressed with **Zstandard (zstd)** specifically tuned for the **MindOS Sunrise**.

```
package-name-version-release-arch.mind
├── metadata.json       # name, version, description, Debian-style dependencies, origin
├── files.tar.zst       # Root filesystem payload (/usr, /etc, etc.)
├── pre_install.sh      # Pre-installation hook (optional)
├── post_install.sh     # Post-installation hook (optional)
├── pre_remove.sh       # Pre-removal hook (optional)
└── post_remove.sh      # Post-removal hook (optional)
```

## 🚀 Using `mindpkg`

### Update Repository Index
```bash
sudo mindpkg update
```

### Upgrade Everything
Upgrades mindpkg itself (when a newer release is published), then installed `.mind` packages, then system packages:
```bash
sudo mindpkg upgrade
```

### Install Packages
```bash
sudo mindpkg install fastfetch htop tree jq
sudo mindpkg install chrome vscode steam    # official vendor repositories on Debian-based MindOS
```

### Search Packages
```bash
mindpkg search htop
```

### Remove Packages
```bash
sudo mindpkg remove htop
```

### Build a `.mind` Package
```bash
mindpkg build /path/to/source    # needs metadata.json and rootfs/, hooks optional
```

### How `mindpkg` installs
- On Debian-based MindOS (Paradise), apt is tried first (vendor repositories for Chrome, VS Code and Steam, then Debian), then the `.mind` repository.
- Every `.mind` download is verified by SHA256 from the index; only `https://` sources are accepted.
- Missing dependencies are installed with apt or from the `.mind` repository. On systems without dpkg (Sunrise) they are only listed, so core libraries are never overwritten.
- Files owned by system (dpkg) packages are never overwritten or removed.

## 🏭 Package factory
`.github/workflows/package-factory.yml` runs `tools/mind_factory.py` every day in a `debian:trixie` container:
1. rebuilds packages that have a newer Debian version, and replaces older Ubuntu-based builds with Debian ones,
2. adds packages that are not in the repository yet, most-installed first (Debian popcon),
3. uploads them to the current chunk release (a new chunk is opened when one is full) and commits `packages.json`.

Run it by hand from the Actions tab: set `new`/`updates`, or list packages in `only` (e.g. `vim htop`).

## 🔄 Releasing a new mindpkg
1. Update `tools/mindpkg` (bump `VERSION`), commit and push.
2. Run `python3 tools/publish_mindpkg.py` — it pins the release in `packages.json` to that commit with its SHA256.
3. Commit and push `packages.json`. Every `sudo mindpkg upgrade` then updates itself.

---

*Maintained by the MindOS Core Team.*
