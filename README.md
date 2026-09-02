# 🌅 MindOS Sunrise Official Package Repository

Official binary package repository for **MindOS Sunrise v1.0 Linux** distribution.

## 📦 What is a `.mind` package?
A `.mind` package is a high-speed, lightweight software container compressed with **Zstandard (zstd)** specifically tuned for the **MindOS Sunrise**.

```
package-name-version-release-arch.mind
├── metadata.json       # Package metadata, architecture, dependencies
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

### Install Packages
```bash
sudo mindpkg install fastfetch htop neofetch tree jq
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
mindpkg build /path/to/source
```

---

*Maintained by the MindOS Core Team.*
