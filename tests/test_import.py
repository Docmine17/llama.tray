from pathlib import Path

import config
import process_manager
import profiles


def test_config_importable_and_instantiable(
    updater_paths: dict[str, Path],
) -> None:
    """Ensure LlamaConfig can be imported and initialized without GTK initialization."""
    cfg = config.LlamaConfig()
    assert cfg is not None
    assert cfg.get("backend") == "vulkan"


def test_profiles_importable_and_instantiable(
    updater_paths: dict[str, Path],
) -> None:
    """Ensure LlamaProfilesManager can be imported and initialized without GTK initialization."""
    mgr = profiles.LlamaProfilesManager()
    assert mgr is not None
    assert len(mgr.profiles) >= 1
    assert mgr.profiles[0]["name"] == "Default"


def test_process_manager_importable_and_instantiable(
    updater_paths: dict[str, Path],
) -> None:
    """Ensure LlamaProcessManager can be imported and initialized without GTK initialization."""
    cfg = config.LlamaConfig()
    mgr = profiles.LlamaProfilesManager()
    proc_mgr = process_manager.LlamaProcessManager(cfg, mgr)
    assert proc_mgr is not None
    assert not proc_mgr.is_running()
