import os
import sys
import json
import time
import urllib.request
import urllib.error
import hashlib
import tarfile
import tempfile
import threading
from gi.repository import GLib

# Directory setup
CACHE_DIR = os.path.expanduser("~/.cache/llama.tray")
INSTALL_DIR = os.path.expanduser("~/.local/share/llama.tray/bin")
CACHE_FILE = os.path.join(CACHE_DIR, "releases_cache.json")
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(INSTALL_DIR, exist_ok=True)


def get_releases(force_check=False):
    """
    Fetches releases from GitHub API with a 1-hour cache.
    Returns a list of releases (dict) or raises an exception.
    """
    now = time.time()
    
    # Check cache first
    if not force_check and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cached = json.load(f)
            if now - cached.get("timestamp", 0) < CACHE_EXPIRY_SECONDS:
                return cached.get("releases", [])
        except Exception:
            # Ignore cache read errors and refetch
            pass

    # Fetch from GitHub
    url = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "llama.tray-updater"}
    )
    
    try:
        # Timeout after 10 seconds
        with urllib.request.urlopen(req, timeout=10) as response:
            releases = json.loads(response.read().decode())
            
        # Write to cache
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"timestamp": now, "releases": releases}, f)
        except Exception:
            pass
            
        return releases
    except urllib.error.URLError as e:
        raise RuntimeError(f"Erro de conexão ao buscar atualizações: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Erro ao processar atualizações: {e}")


def get_asset_for_backend(release, backend):
    """
    Finds the correct asset in the release based on backend.
    Supported backends: 'vulkan', 'cpu' (or other strings).
    Returns (asset_name, download_url, expected_sha256) or None.
    """
    assets = release.get("assets", [])
    
    # We look for files matching llama-<tag>-bin-ubuntu-<backend>-x64.tar.gz
    # Standard format: llama-b9496-bin-ubuntu-vulkan-x64.tar.gz
    # or llama-b9496-bin-ubuntu-x64.tar.gz (for CPU)
    for asset in assets:
        name = asset.get("name", "")
        if not (name.startswith("llama-") and name.endswith(".tar.gz")):
            continue
        if "bin-ubuntu" not in name:
            continue
        
        # Check architecture (limit to x64 for now, but can be made flexible)
        if "x64" not in name:
            continue
            
        if backend == "vulkan":
            if "vulkan" in name:
                digest = asset.get("digest", "")
                sha256 = digest.split("sha256:")[-1] if "sha256:" in digest else None
                return name, asset.get("browser_download_url"), sha256
        else: # CPU backend
            if "vulkan" not in name and "rocm" not in name and "openvino" not in name and "s390x" not in name and "arm64" not in name:
                digest = asset.get("digest", "")
                sha256 = digest.split("sha256:")[-1] if "sha256:" in digest else None
                return name, asset.get("browser_download_url"), sha256
                
    return None


def is_version_installed(tag_name):
    """
    Checks if a release version is already downloaded and extracted.
    """
    version_dir = os.path.join(INSTALL_DIR, tag_name)
    server_bin = os.path.join(version_dir, "llama-server")
    return os.path.exists(server_bin) and os.path.isfile(server_bin)


def get_installed_versions():
    """
    Returns a list of tags that are currently installed/extracted in INSTALL_DIR.
    """
    if not os.path.exists(INSTALL_DIR):
        return []
    versions = []
    try:
        for item in os.listdir(INSTALL_DIR):
            item_path = os.path.join(INSTALL_DIR, item)
            if os.path.isdir(item_path):
                server_bin = os.path.join(item_path, "llama-server")
                if os.path.exists(server_bin) and os.path.isfile(server_bin):
                    versions.append(item)
    except Exception:
        pass
    return sorted(versions, reverse=True)


class DownloadThread(threading.Thread):
    def __init__(self, tag_name, download_url, expected_sha256, on_progress, on_done, on_error):
        super().__init__()
        self.tag_name = tag_name
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
        target_dir = os.path.join(INSTALL_DIR, self.tag_name)
        
        try:
            # 1. Download
            req = urllib.request.Request(
                self.download_url,
                headers={"User-Agent": "llama.tray-updater"}
            )
            
            with urllib.request.urlopen(req, timeout=15) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                block_size = 8192
                
                # Use a tempfile inside the user's cache dir to avoid writing to /tmp
                fd, temp_file_path = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tar.gz")
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
                        GLib.idle_add(self.on_progress, f"Baixando: {percent}%", percent / 100.0)

                temp_file.close()
                
                if self._stop_event.is_set():
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                    return
                
                # 2. Checksum validation
                calculated_sha = sha256_hash.hexdigest()
                if self.expected_sha256 and calculated_sha != self.expected_sha256:
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                    raise ValueError(
                        f"Falha na verificação de integridade (SHA256 incorreto).\n"
                        f"Esperado: {self.expected_sha256}\n"
                        f"Calculado: {calculated_sha}"
                    )

            # 3. Extraction
            GLib.idle_add(self.on_progress, "Extraindo binários...", 0.99)
            
            # Make sure target dir is clean
            if os.path.exists(target_dir):
                import shutil
                shutil.rmtree(target_dir)
            os.makedirs(target_dir, exist_ok=True)
            
            try:
                with tarfile.open(temp_file_path, "r:gz") as tar:
                    # Detect top-level directory inside the tar (e.g. "llama-b9495/")
                    members = tar.getmembers()
                    top_dirs = set()
                    for m in members:
                        parts = m.name.split("/")
                        if len(parts) > 1:
                            top_dirs.add(parts[0])
                    
                    # If there's a single top-level dir, strip it when extracting
                    if len(top_dirs) == 1:
                        strip_prefix = top_dirs.pop() + "/"
                        for member in members:
                            if member.name.startswith(strip_prefix):
                                member.name = member.name[len(strip_prefix):]
                                if member.name:  # skip the directory entry itself
                                    tar.extract(member, path=target_dir)
                    else:
                        tar.extractall(path=target_dir)
            except Exception as e:
                # Clean up half-extracted files
                if os.path.exists(target_dir):
                    import shutil
                    shutil.rmtree(target_dir)
                raise RuntimeError(f"Falha ao extrair o arquivo: {e}")
            finally:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)

            # 4. Verify extraction succeeded
            server_bin = os.path.join(target_dir, "llama-server")
            if not os.path.exists(server_bin):
                raise RuntimeError("O arquivo extraído não contém o executável 'llama-server'.")
            
            # Make sure it's executable
            os.chmod(server_bin, 0o755)
            
            GLib.idle_add(self.on_progress, "Instalação concluída!", 1.0)
            GLib.idle_add(self.on_done, self.tag_name, target_dir)

        except Exception as e:
            # Clean up target dir on error
            if os.path.exists(target_dir):
                import shutil
                shutil.rmtree(target_dir)
            GLib.idle_add(self.on_error, str(e))
