# Llama.tray

`llama.tray` is a system tray application for Linux designed to manage, configure, monitor, and update instances of `llama.cpp` (specifically `llama-server`).

## Features

- **Direct Tray Control**: Start and stop `llama-server` with a single click. The tray icon changes color dynamically:
  - 🟢 **Green**: Server running
  - 🔴 **Red**: Server stopped
  - 🔵 **Blue**: Downloading/updating binaries
- **Integrated Update Manager**:
  - Queries official releases from the [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp) repository.
  - Displays an ordered dropdown with available versions for download and locally installed ones.
  - Automatically downloads and validates package integrity using **SHA256** hashes.
  - Safely extracts archives with path-traversal protection and atomic installation (staged extraction + rename).
  - Supports both **x86_64** and **ARM64** architectures.
- **Profile Management**:
  - Create, edit, switch, and delete profiles, each with its own set of command-line arguments and environment variables.
  - Profiles are stored separately from the main config and automatically migrated from legacy single-profile configurations.
- **Terminal Integration**: Optionally creates symlinks for all available `llama.cpp` executables in `~/.local/bin`, allowing you to run them directly from your terminal. Safely handles conflicts with existing files.
- **Settings Panel** (GTK 3):
  - Profile management (create, rename, delete, switch).
  - Custom environment variable definition per profile.
  - Custom command-line argument configuration per profile (e.g., `--port`, `--host`, `--model`).
  - Selection of acceleration backend (**Vulkan** for GPU or **CPU**).
  - Version management with inline download, install, and apply.
  - Autostart configuration (Disabled, Enabled, Enabled with Server).
  - Terminal integration toggle.
- **Real-time Log Monitor** (GTK 3):
  - Live log viewer with auto-scroll and tail-follow (500ms polling).
  - Log rotation (10 MB limit per file, 1 backup).
  - Clear logs directly from the UI.
- **Crash Detection**: Desktop notifications when `llama-server` exits unexpectedly.
- **Autostart**: Manages `~/.config/autostart/llama-tray.desktop` with optional `--autostart` flag to also start the server on login.

## Directory Structure

| Path | Description |
|---|---|
| `~/.local/share/llama-tray/bin/<version>/` | Installed llama.cpp binaries |
| `~/.local/share/llama-tray/llama.log` | Server log file |
| `~/.config/llama-tray/config.json` | Main configuration (version, backend, autostart, etc.) |
| `~/.config/llama-tray/profiles.json` | Profile definitions (args, env_vars per profile) |
| `~/.cache/llama-tray/` | GitHub releases cache and temporary downloads |
| `~/.config/autostart/llama-tray.desktop` | Autostart entry (managed by the app) |
| `~/.local/bin/llama-tray` | Application launcher wrapper |
| `~/.local/bin/llama-*` | Terminal integration symlinks (optional) |

## Prerequisites

- **Python 3** (3.10+)
- **PyGObject** (`python-gobject` / `python3-gi`)
- **GTK 3** (`gtk3` / `libgtk-3-dev`)
- **Ayatana AppIndicator** or **AppIndicator3** (`libayatana-appindicator3` / `gir1.2-ayatanaappindicator3-0.1`)
- **libnotify** (`libnotify` / `gir1.2-notify-0.7`)

### Arch Linux

```bash
sudo pacman -S python-gobject gtk3 libayatana-appindicator libnotify
```

### Ubuntu / Debian

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
```

### Fedora

```bash
sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3 libnotify
```

## Installation

```bash
git clone https://github.com/Docmine17/llama.tray.git
cd llama.tray
./setup.sh
```

The setup script provides an interactive menu:

1. **Install** — Copies source code, icons, and desktop entry to `~/.local/share`, creates a launcher wrapper in `~/.local/bin`.
2. **Uninstall** — Removes the app wrapper, source, icons, desktop entry, autostart entry, and terminal symlinks. Preserves downloaded binaries and configs.
3. **Full Cleanup** — Uninstalls everything and also deletes all downloaded binaries, configs, cache, and logs.

## Usage

Launch from your system menu or terminal:

```bash
llama-tray
```

Start with automatic server launch:

```bash
llama-tray --autostart
```

## License

This project is open source.
