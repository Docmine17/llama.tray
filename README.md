# Llama.tray

`llama.tray` is a system tray application for Linux designed to manage, configure, monitor, and update instances of `llama.cpp` (including `llama-server` and `llama-cli`).

## Features

- **Direct Tray Control**: Start and stop the llama.cpp server with a single click. The tray icon changes color dynamically (Green: running, Red: stopped, Blue: updating/downloading).
- **Integrated Update Manager**:
  - Queries official releases from the `ggml-org/llama.cpp` repository.
  - Displays an ordered dropdown with available versions for download or already installed locally (local cache).
  - Automatically downloads and validates the package integrity using **SHA256** hashes.
  - Automatically extracts and configures the executable.
- **Terminal Integration**: Automatically creates symlinks for all available `llama.cpp` executables in `~/.local/bin` if enabled, allowing you to run them directly from your terminal.
- **Settings Panel**:
  - Profile management, you can create and switch between different profiles.
  - Custom environment variable definition.
  - Custom command-line argument configuration (e.g., port, host, model paths).
  - Selection of acceleration backend (Vulkan for GPU or default CPU).
  - Autostart configuration(Disabled, Enabled and Enabled with Server).
- **Real-time Log Monitor**:
  - GTK 3-based log viewer with integrated auto-scroll.
  - Automatic log rotation (10 MB limit per file to save disk space).

## Directory Structure Used

- **llama.cpp binaries installation**: `~/.local/share/llama-tray/bin/<version>/`
- **Configuration file (JSON)**: `~/.config/llama-tray/config.json`
- **Server log**: `~/.local/share/llama-tray/llama.log`
- **Terminal integration (Optional)**: `integrate llama.cpp binaries`

## Prerequisites

You will need Python 3, PyGObject, and Ayatana AppIndicator libraries installed on your system.

## Installation

To install run:

```bash
$ git clone https://github.com/Docmine17/llama.tray.git
```
```
$ cd llama.tray
```


```bash
$ ./setup.sh
```
