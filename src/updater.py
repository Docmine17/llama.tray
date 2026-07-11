import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from gi.repository import GLib

# Directory setup
CACHE_DIR = Path("~/.cache/llama-tray").expanduser()
INSTALL_DIR = Path("~/.local/share/llama-tray/bin").expanduser()
CONFIG_DIR = Path("~/.config/llama-tray").expanduser()
AUTOSTART_DIR = Path("~/.config/autostart").expanduser()
LOG_DIR = Path("~/.local/share/llama-tray").expanduser()
CACHE_FILE = CACHE_DIR / "releases_cache.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROFILES_FILE = CONFIG_DIR / "profiles.json"
AUTOSTART_FILE = AUTOSTART_DIR / "llama-tray.desktop"
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

BIN_LINK_DIR = Path("~/.local/bin").expanduser()


def ensure_dirs() -> None:
    """Creates all required application directories. Called once at startup."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_system_arch() -> str:
    """
    Detects the system architecture and maps it to the naming convention
    used by llama.cpp releases (x64 or arm64).
    """
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "x64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    raise RuntimeError(f"Unsupported system architecture: {arch}")


def get_version_id(tag_name: str, backend: str) -> str:
    """Generates a unique folder identifier based on the tag and backend."""
    return tag_name if backend == "cpu" else f"{tag_name}-{backend}"


def parse_version_id(folder_name: str) -> tuple[str, str]:
    """Inverse of get_version_id. Extracts the base tag and the backend."""
    if folder_name.endswith("-vulkan"):
        return folder_name[:-7], "vulkan"
    return folder_name, "cpu"


def get_releases(force_check: bool = False) -> list[dict]:
    """Fetches releases from GitHub API with a 1-hour cache."""
    now = time.time()

    if not force_check and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                cached = json.load(f)
            if now - cached.get("timestamp", 0) < CACHE_EXPIRY_SECONDS:
                return cached.get("releases", [])
        except (json.JSONDecodeError, IOError):
            pass

    url = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
    req = urllib.request.Request(url, headers={"User-Agent": "llama.tray-updater"})

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            releases = json.loads(response.read().decode())

        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"timestamp": now, "releases": releases}, f)
        except IOError:
            pass

        return [
            release
            for release in releases
            if not release.get("draft", False) and not release.get("prerelease", False)
        ]
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error while fetching updates: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Error processing updates: {e}")


def get_asset_for_backend(
    release: dict, backend: str
) -> Optional[tuple[str, str, str]]:
    """Finds the correct asset in the release based on the specified backend."""
    assets = release.get("assets", [])
    system_arch = get_system_arch()

    for asset in assets:
        name = asset.get("name", "")
        if not (name.startswith("llama-") and name.endswith(".tar.gz")):
            continue

        is_compatible = (
            "vulkan" in name and f"{system_arch}.tar.gz" in name
            if backend == "vulkan"
            else name.endswith(f"bin-ubuntu-{system_arch}.tar.gz")
        )
        if not is_compatible:
            continue

        download_url = asset.get("browser_download_url")
        digest = asset.get("digest", "")
        sha256 = digest.removeprefix("sha256:").lower()
        if not isinstance(download_url, str) or not re.fullmatch(
            r"[0-9a-f]{64}", sha256
        ):
            continue
        return name, download_url, sha256

    return None


def is_version_installed(tag_name: str, backend: str) -> bool:
    """Checks if a release version combined with its backend is already extracted."""
    version_id = get_version_id(tag_name, backend)
    server_bin = INSTALL_DIR / version_id / "llama-server"
    return server_bin.exists() and server_bin.is_file()


def get_installed_versions() -> list[str]:
    """Returns a list of folder names that are currently installed/extracted."""
    if not INSTALL_DIR.exists():
        return []
    versions = []
    try:
        for item in INSTALL_DIR.iterdir():
            if item.is_dir():
                if (item / "llama-server").exists():
                    versions.append(item.name)
    except Exception:
        pass
    return sorted(versions, reverse=True)


def get_version_list(releases_list: list[dict], backend: str) -> list[tuple[str, str]]:
    """
    Processes releases and installed versions to return a list of
    (tag, display_text) for the UI combo box.
    """
    items = []
    online_tags = set()

    # Process remote releases
    for r in releases_list:
        tag = r.get("tag_name", "")
        if not tag:
            continue
        online_tags.add(tag)
        is_inst = is_version_installed(tag, backend)
        status_text = " (Installed)" if is_inst else " (Available)"
        items.append((tag, f"{tag}{status_text}"))

    # Process local versions not found in remote list
    installed_folders = get_installed_versions()
    local_tags_added = set()
    for folder in installed_folders:
        tag, f_backend = parse_version_id(folder)
        if tag and tag not in online_tags and tag not in local_tags_added:
            local_tags_added.add(tag)
            is_inst = is_version_installed(tag, backend)
            status_text = " (Installed)" if is_inst else " (Available - Other Backend)"
            items.append((tag, f"{tag}{status_text}"))

    return items


def get_version_binaries(version_id: str) -> list[str]:
    """
    Scans the version directory and returns a list of executable binaries
    that are not shared libraries (avoiding anything containing '.so').
    """
    target_dir = INSTALL_DIR / version_id
    if not target_dir.exists():
        return []

    return [
        item.name
        for item in target_dir.iterdir()
        if item.is_file() and os.access(item, os.X_OK) and ".so" not in item.name
    ]


@dataclass
class SymlinkResult:
    """Outcome of updating optional terminal-integration symlinks."""

    success: bool
    conflicts: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success


def _is_within(path: Path, root: Path) -> bool:
    """Return whether a resolved path is contained by root."""
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_managed_symlink(path: Path) -> bool:
    return path.is_symlink() and _is_within(path, INSTALL_DIR)


def _managed_symlinks() -> list[Path]:
    if not BIN_LINK_DIR.exists():
        return []
    return [path for path in BIN_LINK_DIR.iterdir() if _is_managed_symlink(path)]


def manage_symlinks(version_id: Optional[str], enabled: bool) -> SymlinkResult:
    """Safely synchronize terminal links owned by llama.tray.

    Files and links not pointing inside ``INSTALL_DIR`` are never removed. When
    enabled, conflicts are reported and left intact rather than overwritten.
    """
    try:
        if not enabled:
            for link_path in _managed_symlinks():
                link_path.unlink()
            return SymlinkResult(True)

        if not version_id:
            return SymlinkResult(False, error="No installed version was selected.")

        target_dir = INSTALL_DIR / version_id
        if not target_dir.is_dir():
            return SymlinkResult(
                False, error=f"Version '{version_id}' is not installed."
            )

        binaries = get_version_binaries(version_id)
        desired = {name: target_dir / name for name in binaries}
        BIN_LINK_DIR.mkdir(parents=True, exist_ok=True)

        conflicts = [
            name
            for name in desired
            if (BIN_LINK_DIR / name).exists() or (BIN_LINK_DIR / name).is_symlink()
            if not _is_managed_symlink(BIN_LINK_DIR / name)
        ]

        for link_path in _managed_symlinks():
            if link_path.name not in desired or link_path.name not in conflicts:
                link_path.unlink()

        for name, target in desired.items():
            if name in conflicts:
                continue
            link_path = BIN_LINK_DIR / name
            if _is_managed_symlink(link_path):
                link_path.unlink()
            link_path.symlink_to(target)

        if conflicts:
            return SymlinkResult(
                False,
                conflicts=sorted(conflicts),
                error="Existing files were preserved: " + ", ".join(sorted(conflicts)),
            )
        return SymlinkResult(True)
    except OSError as error:
        return SymlinkResult(False, error=f"Could not update terminal links: {error}")


def manage_autostart(mode: str) -> bool:
    """
    Manages the llama-tray.desktop file in ~/.config/autostart.
    Modes: 'Disabled', 'Enabled', 'Enabled with Server'
    """
    try:
        if mode == "Disabled":
            if AUTOSTART_FILE.exists():
                AUTOSTART_FILE.unlink()
            return True

        # Ensure directory exists
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)

        exec_cmd = (
            "llama-tray --autostart" if mode == "Enabled with Server" else "llama-tray"
        )

        content = [
            "[Desktop Entry]",
            "Type=Application",
            "Name=Llama Tray",
            f"Exec=bash -c '~/.local/bin/{exec_cmd}'",
            "Icon=llama-tray-icon",
        ]

        AUTOSTART_FILE.write_text("\n".join(content) + "\n")
        return True
    except Exception as e:
        print(f"Error managing autostart: {e}")
        return False


def prepare_download(
    tag_name: str, backend: str, releases_list: list[dict]
) -> tuple[Optional[tuple[str, str, str]], Optional[str]]:
    """
    Resolves the necessary metadata for a download.
    Returns ((version_id, download_url, expected_sha256), error_msg) or (None, error_msg).
    """
    release_obj = next(
        (r for r in releases_list if r.get("tag_name") == tag_name), None
    )
    if not release_obj:
        return None, "Could not find metadata for this version."

    asset_info = get_asset_for_backend(release_obj, backend)
    if not asset_info:
        return (
            None,
            f"Could not find a compatible binary for '{backend}' in release {tag_name}.",
        )

    asset_name, download_url, expected_sha256 = asset_info
    version_id = get_version_id(tag_name, backend)

    return (version_id, download_url, expected_sha256), None


def _archive_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive path: {name!r}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        raise ValueError("Archive member has an empty path.")
    return parts


def _archive_entries(
    members: list[tarfile.TarInfo],
) -> list[tuple[tarfile.TarInfo, tuple[str, ...]]]:
    entries = [(member, _archive_parts(member.name)) for member in members]
    top_levels = {parts[0] for _, parts in entries}
    prefix = next(iter(top_levels)) if len(top_levels) == 1 else None

    normalized = []
    for member, parts in entries:
        effective_parts = parts[1:] if prefix and parts[0] == prefix else parts
        if effective_parts:
            normalized.append((member, effective_parts))
    return normalized


def _ensure_safe_parent(root: Path, destination: Path) -> None:
    parent = destination.parent
    parent.relative_to(root)
    current = root
    for part in parent.relative_to(root).parts:
        current /= part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError(f"Unsafe archive parent: {current}")
        current.mkdir(exist_ok=True)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _link_destination(root: Path, destination: Path, link_name: str) -> Path:
    link_path = PurePosixPath(link_name)
    if link_path.is_absolute():
        raise ValueError(f"Absolute symlink target is not allowed: {link_name!r}")
    target = (destination.parent / Path(*link_path.parts)).resolve(strict=False)
    if not _is_within(target, root):
        raise ValueError(f"Symlink target escapes installation: {link_name!r}")
    return target


def _hard_link_destination(root: Path, link_name: str, prefix: Optional[str]) -> Path:
    parts = _archive_parts(link_name)
    if prefix and parts[0] == prefix:
        parts = parts[1:]
    if not parts:
        raise ValueError(f"Invalid hard link target: {link_name!r}")
    target = root.joinpath(*parts)
    if not _is_within(target, root):
        raise ValueError(f"Hard link target escapes installation: {link_name!r}")
    return target


def validate_installation(install_dir: Path) -> None:
    """Validate only that extracted symlinks remain internal and resolvable."""
    for path in install_dir.rglob("*"):
        if path.is_symlink():
            target = path.resolve(strict=False)
            if not _is_within(target, install_dir) or not target.exists():
                raise RuntimeError(f"Invalid internal symlink: {path.name}")


def extract_archive_safely(
    archive_path: Path,
    destination: Path,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Extract an official llama.cpp archive without allowing path escapes."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            entries = _archive_entries(archive.getmembers())
            top_levels = {
                parts[0]
                for member, parts in [
                    (m, _archive_parts(m.name)) for m in archive.getmembers()
                ]
            }
            prefix = next(iter(top_levels)) if len(top_levels) == 1 else None

            directories = []
            regular_files = []
            symlinks = []
            hard_links = []
            for member, parts in entries:
                if member.isdir():
                    directories.append((member, parts))
                elif member.isreg():
                    regular_files.append((member, parts))
                elif member.issym():
                    symlinks.append((member, parts))
                elif member.islnk():
                    hard_links.append((member, parts))
                else:
                    raise ValueError(
                        f"Unsupported archive member type: {member.name!r}"
                    )

            for member, parts in directories:
                if should_cancel and should_cancel():
                    return
                destination_path = root.joinpath(*parts)
                _ensure_safe_parent(root, destination_path)
                destination_path.mkdir(exist_ok=True)

            for member, parts in regular_files:
                if should_cancel and should_cancel():
                    return
                destination_path = root.joinpath(*parts)
                _ensure_safe_parent(root, destination_path)
                if destination_path.exists() or destination_path.is_symlink():
                    raise ValueError(f"Duplicate archive path: {member.name!r}")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Could not read archive member: {member.name!r}")
                with source, destination_path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(destination_path, member.mode & 0o777)

            for member, parts in symlinks:
                if should_cancel and should_cancel():
                    return
                destination_path = root.joinpath(*parts)
                _ensure_safe_parent(root, destination_path)
                if destination_path.exists() or destination_path.is_symlink():
                    raise ValueError(f"Duplicate archive path: {member.name!r}")
                _link_destination(root, destination_path, member.linkname)
                destination_path.symlink_to(member.linkname)

            for member, parts in hard_links:
                if should_cancel and should_cancel():
                    return
                destination_path = root.joinpath(*parts)
                _ensure_safe_parent(root, destination_path)
                if destination_path.exists() or destination_path.is_symlink():
                    raise ValueError(f"Duplicate archive path: {member.name!r}")
                target = _hard_link_destination(root, member.linkname, prefix)
                if target.is_symlink() or not target.is_file():
                    raise ValueError(f"Invalid hard link target: {member.linkname!r}")
                os.link(target, destination_path)
    except (OSError, tarfile.TarError, ValueError) as error:
        raise RuntimeError(f"Failed to extract archive: {error}") from error

    if should_cancel and should_cancel():
        return
    validate_installation(root)


def publish_installation(staging_dir: Path, target_dir: Path) -> None:
    """Atomically replace a version directory while retaining a recoverable backup."""
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = target_dir.with_name(f".{target_dir.name}.backup-{uuid.uuid4().hex}")
    moved_previous = False

    try:
        if target_dir.exists() or target_dir.is_symlink():
            os.replace(target_dir, backup_dir)
            moved_previous = True
        os.replace(staging_dir, target_dir)
    except OSError:
        if moved_previous and not (target_dir.exists() or target_dir.is_symlink()):
            os.replace(backup_dir, target_dir)
        raise
    else:
        if moved_previous:
            _remove_path(backup_dir)


class DownloadThread(threading.Thread):
    def __init__(
        self,
        tag_name: str,
        version_id: str,
        download_url: str,
        expected_sha256: str,
        on_progress: Optional[Callable[[str, float], None]],
        on_done: Callable[[str, str], None],
        on_error: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True)
        self.tag_name = tag_name
        self.version_id = version_id
        self.download_url = download_url
        self.expected_sha256 = expected_sha256
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_error = on_error
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _notify_progress(self, message: str, fraction: float) -> None:
        if self.on_progress:
            GLib.idle_add(self.on_progress, message, fraction)

    def run(self) -> None:
        temp_file_path: Optional[Path] = None
        staging_dir: Optional[Path] = None

        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                self.download_url, headers={"User-Agent": "llama.tray-updater"}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                sha256_hash = hashlib.sha256()
                fd, temp_file_path_str = tempfile.mkstemp(
                    dir=CACHE_DIR, suffix=".tar.gz"
                )
                temp_file_path = Path(temp_file_path_str)

                with os.fdopen(fd, "wb") as temp_file:
                    while not self._stop_event.is_set():
                        block = response.read(8192)
                        if not block:
                            break
                        temp_file.write(block)
                        sha256_hash.update(block)
                        downloaded += len(block)
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            self._notify_progress(
                                f"Downloading: {percent}%", percent / 100.0
                            )

            if self._stop_event.is_set():
                return

            calculated_sha = sha256_hash.hexdigest()
            if calculated_sha != self.expected_sha256:
                raise ValueError(
                    "Integrity check failed (Incorrect SHA256).\n"
                    f"Expected: {self.expected_sha256}\n"
                    f"Calculated: {calculated_sha}"
                )

            self._notify_progress("Extracting binaries...", 0.99)
            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(
                tempfile.mkdtemp(prefix=f".{self.version_id}.", dir=INSTALL_DIR)
            )
            extract_archive_safely(
                temp_file_path, staging_dir, should_cancel=self._stop_event.is_set
            )
            if self._stop_event.is_set():
                return

            target_dir = INSTALL_DIR / self.version_id
            publish_installation(staging_dir, target_dir)
            staging_dir = None
            self._notify_progress("Installation complete!", 1.0)
            GLib.idle_add(self.on_done, self.tag_name, str(target_dir))
        except Exception as error:
            if not self._stop_event.is_set():
                GLib.idle_add(self.on_error, str(error))
        finally:
            if temp_file_path and temp_file_path.exists():
                temp_file_path.unlink()
            if staging_dir and staging_dir.exists():
                _remove_path(staging_dir)
