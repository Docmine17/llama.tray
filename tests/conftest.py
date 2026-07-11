from pathlib import Path

import pytest

import updater


@pytest.fixture
def updater_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Isolate updater paths so tests never use the user's XDG directories."""
    cache_dir = tmp_path / "cache"
    install_dir = tmp_path / "install"
    config_dir = tmp_path / "config"
    autostart_dir = tmp_path / "autostart"
    log_dir = tmp_path / "logs"
    bin_link_dir = tmp_path / "bin"

    monkeypatch.setattr(updater, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(updater, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(updater, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(updater, "AUTOSTART_DIR", autostart_dir)
    monkeypatch.setattr(updater, "LOG_DIR", log_dir)
    monkeypatch.setattr(updater, "CACHE_FILE", cache_dir / "releases_cache.json")
    monkeypatch.setattr(updater, "CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(updater, "PROFILES_FILE", config_dir / "profiles.json")
    monkeypatch.setattr(updater, "AUTOSTART_FILE", autostart_dir / "llama-tray.desktop")
    monkeypatch.setattr(updater, "BIN_LINK_DIR", bin_link_dir)

    return {
        "cache": cache_dir,
        "install": install_dir,
        "config": config_dir,
        "autostart": autostart_dir,
        "logs": log_dir,
        "bin": bin_link_dir,
    }
