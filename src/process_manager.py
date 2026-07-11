import ctypes
import ctypes.util
import logging
import os
import shlex
import signal
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from typing import Callable, Optional

from gi.repository import GLib

import updater
from config import LlamaConfig
from profiles import LlamaProfilesManager

# Load libc once at module initialization (safe from forks/threads deadlock)
try:
    libc_name = ctypes.util.find_library("c") or "libc.so.6"
    libc = ctypes.CDLL(libc_name)
    # prctl(int option, unsigned long arg2, ...)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
except Exception:
    libc = None


def _preexec_safe() -> None:
    """Configures the child process to receive SIGTERM if the parent dies.
    Runs in the child process immediately after fork and before exec.
    Must not allocate memory or acquire locks.
    """
    if libc is not None:
        try:
            # PR_SET_PDEATHSIG = 1, SIGTERM = 15
            libc.prctl(1, 15, 0, 0, 0)
        except Exception:
            pass


def setup_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("llama_server")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        # Ensure log directory exists
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        # Create a rotating file handler: 10MB max size, keep 1 backup
        handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=1, encoding="utf-8"
        )
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class LlamaProcessManager:
    def __init__(
        self,
        config: LlamaConfig,
        profiles_manager: LlamaProfilesManager,
        on_unexpected_exit: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.config = config
        self.profiles_manager = profiles_manager
        self.process: Optional[subprocess.Popen[str]] = None
        self.log_file_path = os.path.join(updater.LOG_DIR, "llama.log")
        self.logger = setup_logger(self.log_file_path)
        self.on_unexpected_exit = on_unexpected_exit
        self.intentional_stop = False

    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def start(self) -> tuple[bool, str]:
        if self.is_running():
            return True, "Server is already running."

        config_data = self.config.data
        version = config_data.get("current_version", "")
        if not version:
            return (
                False,
                "No active version. Please go to Settings and install a version.",
            )

        backend = config_data.get("backend", "vulkan")
        version_id = updater.get_version_id(version, backend)

        version_dir = os.path.join(updater.INSTALL_DIR, version_id)
        server_bin = os.path.join(version_dir, "llama-server")
        if not os.path.exists(server_bin):
            return (
                False,
                f"Executable not found for backend '{backend}' at: {server_bin}",
            )

        args_str = ""
        env_vars_str = ""
        current_profile_name = config_data.get("current_profile", "Default")
        active_profile = self.profiles_manager.get_profile(current_profile_name)
        if active_profile:
            args_str = active_profile.get("args", "")
            env_vars_str = active_profile.get("env_vars", "")

        env = os.environ.copy()

        try:
            # Parse environment variables using shlex to correctly handle spaces and quotes
            env_tokens = shlex.split(env_vars_str)
            for token in env_tokens:
                if "=" in token:
                    k, v = token.split("=", 1)
                    env[k.strip()] = v.strip()
        except ValueError:
            # Fallback to simple line split if there's a syntax error like unbalanced quotes
            for line in env_vars_str.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

        try:
            cmd = [server_bin] + shlex.split(args_str)
        except Exception as e:
            return False, f"Failed to process arguments: {e}"

        try:
            self.intentional_stop = False

            # Start process with pipes for stdout/stderr
            self.process = subprocess.Popen(
                cmd,
                cwd=version_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=_preexec_safe,
                start_new_session=True,
                text=True,
                bufsize=1,
            )

            self.logger.info("-" * 60)
            self.logger.info("Server started")
            self.logger.info("-" * 60)

            threading.Thread(target=self._watch_process, daemon=True).start()

            return True, "Server started successfully."
        except Exception as e:
            return False, f"Failed to start process: {e}"

    def _watch_process(self) -> None:
        # Capture a local reference to avoid race with stop() setting self.process = None
        proc = self.process
        if not proc:
            return

        exit_code = -1
        try:
            stdout = proc.stdout
            if stdout is None:
                exit_code = proc.wait()
            else:
                # Read stdout line by line as it comes
                for line in iter(stdout.readline, ""):
                    if line:
                        self.logger.info(line.strip())

                exit_code = proc.wait()
        except Exception:
            pass
        finally:
            if proc and proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        if not self.intentional_stop:
            self.process = None
            if self.on_unexpected_exit:
                GLib.idle_add(self.on_unexpected_exit, exit_code)

    def stop(self) -> tuple[bool, str]:
        proc = self.process
        if proc is None or proc.poll() is not None:
            return True, ""

        self.intentional_stop = True
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
                proc.wait()
            if self.process is proc:
                self.process = None
            return True, ""
        except Exception as e:
            return False, f"Error stopping process: {e}"
