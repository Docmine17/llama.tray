import os
import json
import time
import urllib.request
import urllib.error
import hashlib
import tarfile
import tempfile
import threading
import shutil
import platform
from pathlib import Path
from gi.repository import GLib

# Directory setup
CACHE_DIR = Path("~/.cache/llama-tray").expanduser()
INSTALL_DIR = Path("~/.local/share/llama-tray/bin").expanduser()
CONFIG_DIR = Path("~/.config/llama-tray").expanduser()
LOG_DIR = Path("~/.local/share/llama-tray").expanduser()
CACHE_FILE = CACHE_DIR / "releases_cache.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

CACHE_DIR.mkdir(parents=True, exist_ok=True)
INSTALL_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

def get_system_arch():
    """
    Detects the system architecture and maps it to the naming convention 
    used by llama.cpp releases (x64 or arm64).
    """
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return "x64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    return "x64"  # Fallback to x64

def get_version_id(tag_name, backend):
    """Generates a unique folder identifier based on the tag and backend."""
    return tag_name if backend == "cpu" else f"{tag_name}-{backend}"


def parse_version_id(folder_name):
    """Inverse of get_version_id. Extracts the base tag and the backend."""
    if folder_name.endswith("-vulkan"):
        return folder_name[:-7], "vulkan"
    return folder_name, "cpu"


def get_releases(force_check=False):
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
            
        return releases
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error while fetching updates: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Error processing updates: {e}")


def get_asset_for_backend(release, backend):
    """Finds the correct asset in the release based on the specified backend."""
    assets = release.get("assets", [])
    system_arch = get_system_arch()
    
    for asset in assets:
        name = asset.get("name", "")
        if not (name.startswith("llama-") and name.endswith(".tar.gz")):
            continue
        
        if backend == "vulkan":
            # Must contain 'vulkan' and match the actual system architecture
            if "vulkan" in name and f"{system_arch}.tar.gz" in name:
                digest = asset.get("digest", "")
                sha256 = digest.split("sha256:")[-1] if "sha256:" in digest else None
                return name, asset.get("browser_download_url"), sha256
        else: 
            # CPU backend: Must match 'bin-ubuntu-<arch>.tar.gz'
            # This avoids picking up any other specialized backends (ROCm, OpenVINO, CUDA, etc.)
            if name.endswith(f"bin-ubuntu-{system_arch}.tar.gz"):
                digest = asset.get("digest", "")
                sha256 = digest.split("sha256:")[-1] if "sha256:" in digest else None
                return name, asset.get("browser_download_url"), sha256
                
    return None


def is_version_installed(tag_name, backend):
    """Checks if a release version combined with its backend is already extracted."""
    version_id = get_version_id(tag_name, backend)
    server_bin = INSTALL_DIR / version_id / "llama-server"
    return server_bin.exists() and server_bin.is_file()


def get_installed_versions():
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

def get_version_list(releases_list, backend):
    """
    Processes releases and installed versions to return a list of 
    (tag, display_text) for the UI combo box.
    """
    items = []
    online_tags = set()
    
    # Process remote releases
    for r in releases_list:
        tag = r.get("tag_name", "")
        if not tag: continue
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

def prepare_download(tag_name, backend, releases_list):
    """
    Resolves the necessary metadata for a download.
    Returns ((version_id, download_url, expected_sha256), error_msg) or (None, error_msg).
    """
    release_obj = next((r for r in releases_list if r.get("tag_name") == tag_name), None)
    if not release_obj:
        return None, "Could not find metadata for this version."

    asset_info = get_asset_for_backend(release_obj, backend)
    if not asset_info:
        return None, f"Could not find a compatible binary for '{backend}' in release {tag_name}."

    asset_name, download_url, expected_sha256 = asset_info
    version_id = get_version_id(tag_name, backend)
    
    return (version_id, download_url, expected_sha256), None


class DownloadThread(threading.Thread):
    def __init__(self, tag_name, version_id, download_url, expected_sha256, on_progress, on_done, on_error):
        super().__init__()
        self.tag_name = tag_name
        self.version_id = version_id
        self.download_url = download_url
        self.expected_sha256 = expected_sha256
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_error = on_error
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        temp_file = None
        target_dir = INSTALL_DIR / self.version_id
        
        try:
            req = urllib.request.Request(
                self.download_url,
                headers={"User-Agent": "llama.tray-updater"}
            )
            
            with urllib.request.urlopen(req, timeout=15) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                block_size = 8192
                
                fd, temp_file_path_str = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".tar.gz")
                temp_file_path = Path(temp_file_path_str)
                temp_file = os.fdopen(fd, "wb")
                
                sha256_hash = hashlib.sha256()
                
                while not self._stop_event.is_set():
                    block = response.read(block_size)
                    if not block:
                        break
                    temp_file.write(block)
                    sha256_hash.update(block)
                    downloaded += len(block)
                    
                    if total_size > 0 and self.on_progress:
                        percent = int((downloaded / total_size) * 100)
                        GLib.idle_add(self.on_progress, f"Downloading: {percent}%", percent / 100.0)

                temp_file.close()
                
                if self._stop_event.is_set():
                    if temp_file_path.exists():
                        temp_file_path.unlink()
                    return
                
                calculated_sha = sha256_hash.hexdigest()
                if self.expected_sha256 and calculated_sha != self.expected_sha256:
                    if temp_file_path.exists():
                        temp_file_path.unlink()
                    raise ValueError(
                        f"Integrity check failed (Incorrect SHA256).\n"
                        f"Expected: {self.expected_sha256}\n"
                        f"Calculated: {calculated_sha}"
                    )

            GLib.idle_add(self.on_progress, "Extracting binaries...", 0.99)
            
            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                with tarfile.open(temp_file_path, "r:gz") as tar:
                    members = tar.getmembers()
                    top_dirs = set()
                    for m in members:
                        parts = m.name.split("/")
                        if len(parts) > 1:
                            top_dirs.add(parts[0])
                    
                    if len(top_dirs) == 1:
                        strip_prefix = top_dirs.pop() + "/"
                        for member in members:
                            if member.name.startswith(strip_prefix):
                                member.name = member.name[len(strip_prefix):]
                                if member.name: 
                                    tar.extract(member, path=str(target_dir))
                    else:
                        tar.extractall(path=str(target_dir))
            except Exception as e:
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                raise RuntimeError(f"Failed to extract archive: {e}")
            finally:
                if temp_file_path.exists():
                    temp_file_path.unlink()

            server_bin = target_dir / "llama-server"
            if not server_bin.exists():
                raise RuntimeError("Extracted archive does not contain the 'llama-server' executable.")
            
            server_bin.chmod(0o755)
            
            GLib.idle_add(self.on_progress, "Installation complete!", 1.0)
            GLib.idle_add(self.on_done, self.tag_name, str(target_dir))

        except Exception as e:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            GLib.idle_add(self.on_error, str(e))
