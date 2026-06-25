#!/usr/bin/env python
import copy
import ctypes
import ctypes.util
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "3.0")

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3
except ValueError:
    print("Warning: AyatanaAppIndicator3 not found, trying fallback", file=sys.stderr)

gi.require_version("Notify", "0.7")
from gi.repository import Gio, GLib, Gtk, Notify

import updater

# The directory configurations are now centralized in updater.py
# to ensure consistency between the updater and the main app.


class LlamaProfilesManager:
    def __init__(self) -> None:
        self.profiles = []
        self.load()

    def load(self) -> None:
        if os.path.exists(updater.PROFILES_FILE):
            try:
                with open(updater.PROFILES_FILE, "r") as f:
                    self.profiles = json.load(f)
            except Exception as e:
                print(f"Error loading profiles: {e}", file=sys.stderr)

        # Ensure there is always at least one profile
        if not self.profiles:
            self.profiles = [
                {
                    "name": "Default",
                    "env_vars": "",
                    "args": "--port 8080 --host 127.0.0.1",
                }
            ]
            self.save()

    def save(self) -> None:
        try:
            os.makedirs(updater.CONFIG_DIR, exist_ok=True)
            with open(updater.PROFILES_FILE, "w") as f:
                json.dump(self.profiles, f, indent=4)
        except Exception as e:
            print(f"Error saving profiles: {e}", file=sys.stderr)

    def get_profile(self, name: str) -> Optional[dict]:
        for p in self.profiles:
            if p["name"] == name:
                return p
        return None


class LlamaConfig:
    def __init__(self) -> None:
        self.defaults: dict[str, Any] = {
            "current_version": "",
            "backend": "vulkan",
            "terminal_integration": False,
            "current_profile": "Default",
        }
        self.data: dict[str, Any] = self.defaults.copy()
        self.migration_needed = None
        self.load()

    def load(self) -> None:
        if os.path.exists(updater.CONFIG_FILE):
            try:
                with open(updater.CONFIG_FILE, "r") as f:
                    file_data = json.load(f)
                    self.data.update(file_data)

                    if "env_vars" in file_data or "args" in file_data:
                        self.migration_needed = {
                            "env_vars": file_data.get("env_vars", ""),
                            "args": file_data.get(
                                "args", "--port 8080 --host 127.0.0.1"
                            ),
                        }
                        if "env_vars" in self.data:
                            del self.data["env_vars"]
                        if "args" in self.data:
                            del self.data["args"]
                        self.save()
            except Exception as e:
                print(f"Error loading config: {e}", file=sys.stderr)

    def save(self) -> None:
        try:
            os.makedirs(updater.CONFIG_DIR, exist_ok=True)
            with open(updater.CONFIG_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def set_bulk(self, updates: dict[str, Any]) -> None:
        """Update multiple keys and save only once."""
        self.data.update(updates)
        self.save()


def setup_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("llama_server")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        # Create a rotating file handler: 10MB max size, keep 1 backup
        handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=1, encoding="utf-8"
        )
        formatter = logging.Formatter(
            "%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class LlamaProcessManager:
    def __init__(
        self,
        config: LlamaConfig,
        profiles_manager: LlamaProfilesManager,
        on_unexpected_exit: Optional[Callable] = None,
    ) -> None:
        self.config = config
        self.profiles_manager = profiles_manager
        self.process = None
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

        def preexec():
            try:
                libc_name = ctypes.util.find_library("c") or "libc.so.6"
                libc = ctypes.CDLL(libc_name)
                libc.prctl(1, 15)
            except Exception:
                pass

        try:
            self.intentional_stop = False

            # Start process with pipes for stdout/stderr
            self.process = subprocess.Popen(
                cmd,
                cwd=version_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=preexec,
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

        try:
            # Read stdout line by line as it comes
            for line in iter(proc.stdout.readline, ""):
                if line:
                    self.logger.info(line.strip())

            exit_code = proc.wait()
        except Exception:
            exit_code = -1

        if not self.intentional_stop:
            self.process = None
            if self.on_unexpected_exit:
                GLib.idle_add(self.on_unexpected_exit, exit_code)

    def stop(self) -> tuple[bool, str]:
        if not self.is_running():
            return True, ""

        self.intentional_stop = True
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None
            return True, ""
        except Exception as e:
            return False, f"Error stopping process: {e}"


class LlamaWindow(Gtk.ApplicationWindow):
    def __init__(self, gtk_app, title, width=700, height=450):
        super().__init__(application=gtk_app, title=title)
        self.set_icon_name("llama-tray-icon")
        self.set_default_size(width, height)

    def create_main_container(self, spacing=10, margin=12):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        vbox.set_margin_start(margin)
        vbox.set_margin_end(margin)
        vbox.set_margin_top(margin)
        vbox.set_margin_bottom(margin)
        self.add(vbox)
        return vbox


class LogsWindow(LlamaWindow):
    def __init__(self, gtk_app, logic_app):
        super().__init__(gtk_app, "llama.cpp Logs", 700, 450)
        self.logic_app = logic_app
        self.get_style_context().add_class("logs-window")

        vbox = self.create_main_container(spacing=10, margin=12)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        vbox.pack_start(scrolled, True, True, 0)

        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        scrolled.add(self.text_view)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        vbox.pack_start(hbox, False, False, 0)

        clear_btn = Gtk.Button(label="Clear Logs")
        clear_btn.get_style_context().add_class("destructive-action")
        clear_btn.connect("clicked", self.on_clear_clicked)
        hbox.pack_start(clear_btn, False, False, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: self.destroy())
        hbox.pack_end(close_btn, False, False, 0)

        self.log_file_path = self.logic_app.process_manager.log_file_path
        self.file_pos = 0
        self.timer_id = None

        self.connect("destroy", self.on_destroy)
        self.load_initial_logs()
        self.timer_id = GLib.timeout_add(500, self.tail_logs)

    def load_initial_logs(self):
        if not os.path.exists(self.log_file_path):
            self.text_view.get_buffer().set_text("No logs available.")
            return

        try:
            with open(self.log_file_path, "r", errors="replace") as f:
                lines = f.readlines()
                initial_content = "".join(lines[-250:])
            self.text_view.get_buffer().set_text(initial_content)
            self.file_pos = os.path.getsize(self.log_file_path)
            self.scroll_to_bottom()
        except Exception as e:
            self.text_view.get_buffer().set_text(f"Error reading logs: {e}")

    def tail_logs(self):
        if not os.path.exists(self.log_file_path):
            return True

        try:
            current_size = os.path.getsize(self.log_file_path)
            if current_size < self.file_pos:
                self.file_pos = 0
                self.text_view.get_buffer().set_text("")

            if current_size > self.file_pos:
                with open(self.log_file_path, "r", errors="replace") as f:
                    f.seek(self.file_pos)
                    new_content = f.read()
                    self.file_pos = f.tell()
                if new_content:
                    buffer = self.text_view.get_buffer()
                    end_iter = buffer.get_end_iter()
                    buffer.insert(end_iter, new_content)
                    self.scroll_to_bottom()
        except Exception as e:
            print(f"Error tailing logs: {e}", file=sys.stderr)
        return True

    def scroll_to_bottom(self):
        buffer = self.text_view.get_buffer()
        mark = buffer.get_insert()
        end_iter = buffer.get_end_iter()
        buffer.move_mark(mark, end_iter)
        self.text_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

    def on_clear_clicked(self, widget):
        try:
            with open(self.log_file_path, "w") as f:
                f.truncate(0)
            self.file_pos = 0
            self.text_view.get_buffer().set_text("")
        except Exception as e:
            self.text_view.get_buffer().set_text(f"Error clearing logs: {e}")

    def on_destroy(self, widget):
        if self.timer_id:
            GLib.source_remove(self.timer_id)
        self.logic_app.logs_window = None


class SettingsWindow(LlamaWindow):
    def __init__(self, gtk_app, logic_app):
        super().__init__(gtk_app, "llama.tray Settings", 600, -1)
        self.logic_app = logic_app
        self.set_resizable(True)

        self.download_thread = None
        self.releases_list = []
        self.online_releases_loaded = False
        self.fetch_error_msg = ""

        self.profiles_manager = self.logic_app.profiles_manager
        import copy

        self.local_profiles = copy.deepcopy(self.profiles_manager.profiles)
        self.current_profile_name = self.logic_app.config.get(
            "current_profile", "Default"
        )
        if not any(p["name"] == self.current_profile_name for p in self.local_profiles):
            self.current_profile_name = (
                self.local_profiles[0]["name"] if self.local_profiles else ""
            )

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(400)

        root_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root_vbox)
        root_vbox.pack_start(scrolled, True, True, 0)

        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        main_vbox.set_margin_start(24)
        main_vbox.set_margin_end(24)
        main_vbox.set_margin_top(24)
        main_vbox.set_margin_bottom(24)
        scrolled.add(main_vbox)

        # ==================== SECTION 1: PROFILES ====================
        prof_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        prof_title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        prof_title = Gtk.Label()
        prof_title.set_markup("<span size='large' weight='bold'>Profiles</span>")
        prof_title.set_xalign(0.0)
        prof_desc = Gtk.Label(
            label="Manage different sets of arguments and environment variables."
        )
        prof_desc.set_xalign(0.0)
        prof_desc.get_style_context().add_class("dim-label")
        prof_title_box.pack_start(prof_title, False, False, 0)
        prof_title_box.pack_start(prof_desc, False, False, 0)

        prof_header.pack_start(prof_title_box, True, True, 0)

        self.add_prof_btn = Gtk.Button()
        self.add_prof_btn.set_tooltip_text("Add new profile")
        add_img = Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        self.add_prof_btn.set_image(add_img)
        self.add_prof_btn.set_valign(Gtk.Align.CENTER)
        self.add_prof_btn.connect("clicked", self.on_add_profile_clicked)
        prof_header.pack_end(self.add_prof_btn, False, False, 0)

        main_vbox.pack_start(prof_header, False, False, 0)

        self.profiles_listbox = Gtk.ListBox()
        self.profiles_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.profiles_listbox.connect("row-selected", self.on_profile_row_selected)

        list_frame = Gtk.Frame()
        list_frame.add(self.profiles_listbox)
        main_vbox.pack_start(list_frame, False, False, 0)

        self.prof_details_frame = Gtk.Frame()
        details_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        details_grid.set_margin_start(12)
        details_grid.set_margin_end(12)
        details_grid.set_margin_top(12)
        details_grid.set_margin_bottom(12)
        self.prof_details_frame.add(details_grid)

        self.prof_name_entry = Gtk.Entry()
        self.prof_name_entry.set_hexpand(True)
        self.prof_name_entry.connect("changed", self.on_profile_name_changed)
        details_grid.attach(Gtk.Label(label="Profile Name:", xalign=0.0), 0, 0, 1, 1)
        details_grid.attach(self.prof_name_entry, 1, 0, 1, 1)

        env_lbl = Gtk.Label(label="Environment Variables:", xalign=0.0)
        env_lbl.set_valign(Gtk.Align.START)
        details_grid.attach(env_lbl, 0, 1, 1, 1)

        scrolled_env = Gtk.ScrolledWindow()
        scrolled_env.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_env.set_size_request(-1, 80)
        self.env_view = Gtk.TextView()
        self.env_view.set_accepts_tab(False)
        self.env_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.env_view.get_buffer().connect("changed", self.on_profile_data_changed)
        scrolled_env.add(self.env_view)
        details_grid.attach(scrolled_env, 1, 1, 1, 1)

        args_lbl = Gtk.Label(label="Arguments:", xalign=0.0)
        args_lbl.set_valign(Gtk.Align.START)
        details_grid.attach(args_lbl, 0, 2, 1, 1)

        scrolled_args = Gtk.ScrolledWindow()
        scrolled_args.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_args.set_size_request(-1, 80)
        self.args_view = Gtk.TextView()
        self.args_view.set_accepts_tab(False)
        self.args_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.args_view.get_buffer().connect("changed", self.on_profile_data_changed)
        scrolled_args.add(self.args_view)
        details_grid.attach(scrolled_args, 1, 2, 1, 1)

        self.del_prof_btn = Gtk.Button(label="Delete Profile")
        self.del_prof_btn.get_style_context().add_class("destructive-action")
        self.del_prof_btn.connect("clicked", self.on_delete_profile_clicked)
        self.del_prof_btn.set_halign(Gtk.Align.END)
        details_grid.attach(self.del_prof_btn, 1, 3, 1, 1)

        main_vbox.pack_start(self.prof_details_frame, False, False, 0)

        # ==================== SECTION 2: GLOBAL SETTINGS ====================
        conf_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        conf_title = Gtk.Label()
        conf_title.set_markup("<span size='large' weight='bold'>Global Settings</span>")
        conf_title.set_xalign(0.0)
        conf_desc = Gtk.Label(
            label="System-wide llama.cpp settings applied to all profiles."
        )
        conf_desc.set_xalign(0.0)
        conf_desc.get_style_context().add_class("dim-label")
        conf_header.pack_start(conf_title, False, False, 0)
        conf_header.pack_start(conf_desc, False, False, 0)
        main_vbox.pack_start(conf_header, False, False, 0)

        conf_frame = Gtk.Frame()
        conf_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        conf_grid.set_margin_start(12)
        conf_grid.set_margin_end(12)
        conf_grid.set_margin_top(12)
        conf_grid.set_margin_bottom(12)
        conf_frame.add(conf_grid)
        main_vbox.pack_start(conf_frame, False, False, 0)

        self.backend_combo = Gtk.ComboBoxText()
        self.backend_combo.append("vulkan", "Vulkan (GPU)")
        self.backend_combo.append("cpu", "CPU (Standard)")
        self.backend_combo.set_hexpand(True)
        self.backend_combo.connect("changed", self.on_backend_changed)
        conf_grid.attach(
            Gtk.Label(label="Acceleration (Backend):", xalign=0.0), 0, 0, 1, 1
        )
        conf_grid.attach(self.backend_combo, 1, 0, 1, 1)

        version_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.version_combo = Gtk.ComboBoxText()
        self.version_combo.set_hexpand(True)
        self.version_combo.connect("changed", self.on_version_changed)
        version_hbox.pack_start(self.version_combo, True, True, 0)

        self.refresh_releases_btn = Gtk.Button()
        self.refresh_releases_btn.set_tooltip_text("Check for updates on GitHub")
        refresh_img = Gtk.Image.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON
        )
        self.refresh_releases_btn.set_image(refresh_img)
        self.refresh_releases_btn.connect(
            "clicked", lambda w: self.load_releases(force=True)
        )
        version_hbox.pack_start(self.refresh_releases_btn, False, False, 0)
        conf_grid.attach(
            Gtk.Label(label="Version (Release Tag):", xalign=0.0), 0, 1, 1, 1
        )
        conf_grid.attach(version_hbox, 1, 1, 1, 1)

        self.term_check = Gtk.CheckButton(
            label="Add llama-server and llama-cli to ~/.local/bin"
        )
        conf_grid.attach(
            Gtk.Label(label="Terminal Integration:", xalign=0.0), 0, 2, 1, 1
        )
        conf_grid.attach(self.term_check, 1, 2, 1, 1)

        action_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        action_area.set_margin_start(24)
        action_area.set_margin_end(24)
        action_area.set_margin_top(12)
        action_area.set_margin_bottom(12)
        root_vbox.pack_end(action_area, False, False, 0)

        self.status_lbl = Gtk.Label(label="", xalign=0)
        self.status_lbl.set_no_show_all(True)
        self.status_lbl.hide()
        action_area.pack_start(self.status_lbl, False, False, 0)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_no_show_all(True)
        self.progress_bar.hide()
        action_area.pack_start(self.progress_bar, False, False, 0)

        hbox_btn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_area.pack_start(hbox_btn, False, False, 0)

        self.action_btn = Gtk.Button(label="Save and Apply")
        self.action_btn.get_style_context().add_class("suggested-action")
        self.action_btn.connect("clicked", self.on_action_clicked)
        hbox_btn.pack_end(self.action_btn, False, False, 0)

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.connect("clicked", lambda w: self.destroy())
        hbox_btn.pack_end(self.cancel_btn, False, False, 0)

        self._updating_profile_ui = False

        self.load_fields_from_config()
        self.populate_profiles_list()
        self.connect("destroy", self.on_destroy)
        self.load_releases(force=False)
        self.show_all()

    def set_status_message(self, text, is_markup=False):
        if not text:
            self.status_lbl.hide()
            self.status_lbl.set_text("")
        else:
            if is_markup:
                self.status_lbl.set_markup(text)
            else:
                self.status_lbl.set_text(text)
            self.status_lbl.show()

    def update_profiles_list_visuals(self):
        for row in self.profiles_listbox.get_children():
            name = getattr(row, "_profile_name", None)
            if not name:
                continue

            hbox = row.get_child()
            img, lbl = hbox.get_children()

            if name == self.current_profile_name:
                img.set_from_icon_name("object-select-symbolic", Gtk.IconSize.MENU)
                img.get_style_context().remove_class("dim-label")
                lbl.set_markup(f"<b>{GLib.markup_escape_text(name)}</b>")
            else:
                img.set_from_icon_name("user-info-symbolic", Gtk.IconSize.MENU)
                img.get_style_context().add_class("dim-label")
                lbl.set_text(name)

    def populate_profiles_list(self):
        self._updating_profile_ui = True
        for row in self.profiles_listbox.get_children():
            self.profiles_listbox.remove(row)

        row_to_select = None
        for p in self.local_profiles:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            hbox.set_margin_start(12)
            hbox.set_margin_end(12)
            hbox.set_margin_top(8)
            hbox.set_margin_bottom(8)

            icon_name = (
                "object-select-symbolic"
                if p["name"] == self.current_profile_name
                else "user-info-symbolic"
            )
            img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            if p["name"] != self.current_profile_name:
                img.get_style_context().add_class("dim-label")
            hbox.pack_start(img, False, False, 0)

            lbl = Gtk.Label(label=p["name"], xalign=0.0)
            if p["name"] == self.current_profile_name:
                lbl.set_markup(f"<b>{GLib.markup_escape_text(p['name'])}</b>")
            hbox.pack_start(lbl, True, True, 0)

            row.add(hbox)
            row._profile_name = p["name"]
            self.profiles_listbox.add(row)
            row.show_all()

            if p["name"] == self.current_profile_name:
                row_to_select = row

        self.del_prof_btn.set_sensitive(len(self.local_profiles) > 1)

        # Free the flag before selecting row so on_profile_row_selected can do its job
        self._updating_profile_ui = False

        if row_to_select:
            self.profiles_listbox.select_row(row_to_select)
        else:
            # If no row to select (e.g. list is empty), trigger manually to clear UI
            self.on_profile_row_selected(self.profiles_listbox, None)

    def on_profile_row_selected(self, listbox, row):
        if self._updating_profile_ui:
            return

        if not row:
            self.prof_details_frame.set_sensitive(False)
            return

        self.prof_details_frame.set_sensitive(True)
        self.current_profile_name = row._profile_name

        self._updating_profile_ui = True
        prof = next(
            (p for p in self.local_profiles if p["name"] == self.current_profile_name),
            None,
        )
        if prof:
            self.prof_name_entry.set_text(prof["name"])
            self.env_view.get_buffer().set_text(prof.get("env_vars", ""))
            self.args_view.get_buffer().set_text(prof.get("args", ""))
        self._updating_profile_ui = False

        self.update_profiles_list_visuals()

    def on_profile_name_changed(self, entry):
        if self._updating_profile_ui:
            return
        new_name = entry.get_text().strip()
        if not new_name:
            return

        prof = next(
            (p for p in self.local_profiles if p["name"] == self.current_profile_name),
            None,
        )
        if prof and prof["name"] != new_name:
            if any(p["name"] == new_name for p in self.local_profiles if p != prof):
                return
            prof["name"] = new_name
            self.current_profile_name = new_name

            row = self.profiles_listbox.get_selected_row()
            if row:
                row._profile_name = new_name
            self.update_profiles_list_visuals()

    def on_profile_data_changed(self, buffer):
        if self._updating_profile_ui:
            return
        prof = next(
            (p for p in self.local_profiles if p["name"] == self.current_profile_name),
            None,
        )
        if prof:
            env_buf = self.env_view.get_buffer()
            prof["env_vars"] = env_buf.get_text(
                env_buf.get_start_iter(), env_buf.get_end_iter(), True
            )

            args_buf = self.args_view.get_buffer()
            prof["args"] = args_buf.get_text(
                args_buf.get_start_iter(), args_buf.get_end_iter(), True
            )

    def on_add_profile_clicked(self, widget):
        base_name = "New Profile"
        name = base_name
        counter = 1
        while any(p["name"] == name for p in self.local_profiles):
            name = f"{base_name} {counter}"
            counter += 1

        new_prof = {
            "name": name,
            "env_vars": "",
            "args": "--port 8080 --host 127.0.0.1",
        }
        self.local_profiles.append(new_prof)
        self.current_profile_name = name
        self.populate_profiles_list()

    def on_delete_profile_clicked(self, widget):
        if len(self.local_profiles) <= 1:
            return
        self.local_profiles = [
            p for p in self.local_profiles if p["name"] != self.current_profile_name
        ]
        self.current_profile_name = self.local_profiles[0]["name"]
        self.populate_profiles_list()

    def load_fields_from_config(self):
        config_data = self.logic_app.config.data
        backend = config_data.get("backend", "vulkan")
        self.backend_combo.set_active_id(backend)
        self.term_check.set_active(config_data.get("terminal_integration", False))

    def load_releases(self, force=False):
        self.version_combo.set_sensitive(False)
        self.refresh_releases_btn.set_sensitive(False)
        self.version_combo.remove_all()
        self.version_combo.append("loading", "Fetching releases from GitHub...")
        self.version_combo.set_active(0)

        def run_fetch():
            try:
                releases = updater.get_releases(force_check=force)
                GLib.idle_add(self.on_releases_fetched, releases, True)
            except Exception as e:
                print(f"Error fetching releases: {e}", file=sys.stderr)
                GLib.idle_add(self.on_releases_fetched, [], False, str(e))

        threading.Thread(target=run_fetch, daemon=True).start()

    def on_releases_fetched(self, releases, success, err_msg=""):
        self.releases_list = releases
        self.online_releases_loaded = success
        self.fetch_error_msg = err_msg
        self.repopulate_version_combo()

    def repopulate_version_combo(self):
        selected_tag = self.version_combo.get_active_id()

        try:
            self.version_combo.disconnect_by_func(self.on_version_changed)
        except TypeError:
            pass

        self.version_combo.remove_all()
        backend = self.backend_combo.get_active_id() or "vulkan"

        items = updater.get_version_list(self.releases_list, backend)

        if not items:
            self.version_combo.append("none", "No version found")
            self.version_combo.set_active(0)
            self.set_status_message("Offline: No local or remote version found.")
            self.version_combo.set_sensitive(True)
            self.refresh_releases_btn.set_sensitive(True)
            self.version_combo.connect("changed", self.on_version_changed)
            return

        for tag, display in items:
            self.version_combo.append(tag, display)

        active_index = 0
        current_version = self.logic_app.config.data.get("current_version", "")
        tag_to_select = (
            selected_tag
            if selected_tag and selected_tag not in ("loading", "none")
            else current_version
        )

        if tag_to_select:
            for idx, item in enumerate(items):
                if item[0] == tag_to_select:
                    active_index = idx
                    break
        self.version_combo.set_active(active_index)

        self.version_combo.set_sensitive(True)
        self.refresh_releases_btn.set_sensitive(True)

        if not self.online_releases_loaded:
            self.set_status_message(
                f"Offline: showing local versions only. (Error: {self.fetch_error_msg})"
            )
        else:
            self.set_status_message("")

        self.version_combo.connect("changed", self.on_version_changed)

    def on_backend_changed(self, combo):
        self.repopulate_version_combo()
        self.update_action_button_label()

    def on_version_changed(self, combo):
        self.update_action_button_label()

    def get_selected_version(self):
        return self.version_combo.get_active_id()

    def update_action_button_label(self):
        selected_version = self.get_selected_version()
        backend = self.backend_combo.get_active_id()
        if (
            not selected_version
            or selected_version in ("loading", "none")
            or not backend
        ):
            self.action_btn.set_label("Save and Apply")
            return

        is_inst = updater.is_version_installed(selected_version, backend)
        if is_inst:
            self.action_btn.set_label("Save and Apply")
        else:
            self.action_btn.set_label("Download, Install and Apply")

    def on_action_clicked(self, widget):
        selected_version = self.get_selected_version()
        backend = self.backend_combo.get_active_id()

        if not selected_version or selected_version in ("loading", "none"):
            self.set_status_message(
                "<span color='red'>Please select a valid llama.cpp version.</span>",
                is_markup=True,
            )
            return

        self.profiles_manager.profiles = self.local_profiles
        self.profiles_manager.save()

        self.logic_app.config.set_bulk(
            {
                "backend": backend,
                "terminal_integration": self.term_check.get_active(),
                "current_profile": self.current_profile_name,
            }
        )

        version_id = updater.get_version_id(selected_version, backend)
        updater.manage_symlinks(version_id, self.term_check.get_active())

        if updater.is_version_installed(selected_version, backend):
            self.logic_app.config.set("current_version", selected_version)
            self.logic_app.show_notification(
                "Configuration Saved", f"Version {selected_version} ({backend}) active."
            )

            if self.logic_app.process_manager.is_running():
                self.logic_app.restart_server()
            self.destroy()
        else:
            self.start_download(selected_version, backend)

    def start_download(self, tag_name, backend):
        download_info, err_msg = updater.prepare_download(
            tag_name, backend, self.releases_list
        )
        if not download_info:
            self.set_status_message(
                f"<span color='red'>{err_msg}</span>", is_markup=True
            )
            return

        version_id, download_url, expected_sha256 = download_info

        self.set_sensitive_inputs(False)
        self.set_status_message("Starting download...")
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.show()

        self.logic_app.set_updating_state(True)

        self.download_thread = updater.DownloadThread(
            tag_name=tag_name,
            version_id=version_id,
            download_url=download_url,
            expected_sha256=expected_sha256,
            on_progress=self.on_download_progress,
            on_done=self.on_download_done,
            on_error=self.on_download_error,
        )
        self.download_thread.start()

    def set_sensitive_inputs(self, sensitive):
        self.backend_combo.set_sensitive(sensitive)
        self.version_combo.set_sensitive(sensitive)
        self.refresh_releases_btn.set_sensitive(sensitive)
        self.prof_name_entry.set_sensitive(sensitive)
        self.env_view.set_sensitive(sensitive)
        self.args_view.set_sensitive(sensitive)
        self.action_btn.set_sensitive(sensitive)
        self.cancel_btn.set_sensitive(sensitive)
        self.add_prof_btn.set_sensitive(sensitive)
        self.del_prof_btn.set_sensitive(sensitive)
        self.profiles_listbox.set_sensitive(sensitive)

    def on_download_progress(self, message, fraction):
        self.set_status_message(message)
        self.progress_bar.set_fraction(fraction)

    def on_download_done(self, tag_name, target_dir):
        self.logic_app.config.set("current_version", tag_name)

        backend = self.backend_combo.get_active_id() or "vulkan"
        version_id = updater.get_version_id(tag_name, backend)
        integration_enabled = self.logic_app.config.get("terminal_integration", False)
        updater.manage_symlinks(version_id, integration_enabled)

        self.logic_app.show_notification(
            "Download Complete", f"Version {tag_name} installed successfully!"
        )

        self.logic_app.set_updating_state(False)

        if self.logic_app.process_manager.is_running():
            self.logic_app.restart_server()

        self.destroy()

    def on_download_error(self, err_msg):
        self.set_sensitive_inputs(True)
        self.progress_bar.hide()
        self.set_status_message("")
        self.logic_app.set_updating_state(False)
        self.logic_app.show_notification(
            "Installation Error", f"An error occurred: {err_msg}", "error"
        )

    def on_destroy(self, widget):
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.stop()
        self.logic_app.set_updating_state(False)
        self.logic_app.settings_window = None


class LlamaTrayApp(Gtk.Application):
    def __init__(self, autostart=False):
        super().__init__(
            application_id="com.github.llamatray", flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.autostart_server = autostart
        Notify.init("llama-tray")

        self.config = LlamaConfig()
        self.profiles_manager = LlamaProfilesManager()

        if self.config.migration_needed is not None:
            mgr_data = self.config.migration_needed
            found = False
            for p in self.profiles_manager.profiles:
                if p["name"] == "Default":
                    p["env_vars"] = mgr_data["env_vars"]
                    p["args"] = mgr_data["args"]
                    found = True
                    break
            if not found:
                self.profiles_manager.profiles.insert(
                    0,
                    {
                        "name": "Default",
                        "env_vars": mgr_data["env_vars"],
                        "args": mgr_data["args"],
                    },
                )
            self.profiles_manager.save()
            self.config.set("current_profile", "Default")
            delattr(self.config, "migration_needed")

        self.process_manager = LlamaProcessManager(
            self.config,
            self.profiles_manager,
            on_unexpected_exit=self.on_server_crashed,
        )

        self.settings_window = None
        self.logs_window = None
        self.is_updating = False

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)

        # Sync terminal symlinks on startup
        current_version = self.config.get("current_version")
        backend = self.config.get("backend", "vulkan")
        integration_enabled = self.config.get("terminal_integration", False)

        if current_version:
            version_id = updater.get_version_id(current_version, backend)
            updater.manage_symlinks(version_id, integration_enabled)
        else:
            # Ensure no stray links if no version is active
            updater.manage_symlinks(None, integration_enabled)

        self.hold()

        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "llama-tray",
            "llama-tray-stopped-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)
        self.update_menu()

    def do_activate(self):
        if self.autostart_server:
            # Use GLib.idle_add to ensure the app is fully initialized
            # before starting the server
            GLib.idle_add(self.start_server)

    def show_notification(self, title, message, icon_type="info"):
        try:
            notification = Notify.Notification.new(title, message, "llama-tray-icon")
            notification.show()
        except Exception as e:
            print(f"Error showing notification: {e}", file=sys.stderr)

    def on_server_crashed(self, exit_code):
        self.update_icon()
        self.update_menu()
        self.show_notification(
            "Server Stopped Unexpectedly",
            f"llama-server exited with error (Code: {exit_code}). Check logs for details.",
            "error",
        )

    def update_menu(self):
        for child in self.menu.get_children():
            self.menu.remove(child)

        if self.is_updating:
            self._add_menu_item("Updating binaries...", None, False)
        else:
            is_running = self.process_manager.is_running()
            label = "Stop" if is_running else "Start"
            callback = self.stop_server if is_running else self.start_server
            self._add_menu_item(label, callback)

        self.menu.append(Gtk.SeparatorMenuItem())

        self._add_menu_item(
            "Check for updates", self.check_updates_from_menu, not self.is_updating
        )
        self._add_menu_item("Settings", self.open_settings, not self.is_updating)
        self._add_menu_item("Logs", self.open_logs)

        self.menu.append(Gtk.SeparatorMenuItem())
        self._add_menu_item("Quit", lambda w: self.quit_app())
        self.menu.show_all()

    def _add_menu_item(self, label, callback, sensitive=True):
        item = Gtk.MenuItem(label=label)
        if callback:
            item.connect("activate", callback)
        item.set_sensitive(sensitive)
        self.menu.append(item)
        return item

    def set_updating_state(self, updating):
        self.is_updating = updating
        if updating:
            self.indicator.set_icon_full(
                "llama-tray-updating-symbolic", "Updating llama.cpp"
            )
        else:
            self.update_icon()
        self.update_menu()

    def update_icon(self):
        if self.process_manager.is_running():
            self.indicator.set_icon_full(
                "llama-tray-running-symbolic", "Server running"
            )
        else:
            self.indicator.set_icon_full(
                "llama-tray-stopped-symbolic", "Server stopped"
            )

    def start_server(self, widget=None):
        success, msg = self.process_manager.start()
        if success:
            self.show_notification(
                "Llama Server", "Server started successfully!", "success"
            )
            self.update_icon()
            self.update_menu()
        else:
            self.show_notification("Error Starting", msg, "error")

    def stop_server(self, widget=None):
        success, err_msg = self.process_manager.stop()
        if success:
            self.show_notification("Llama Server", "Server stopped.", "info")
            self.update_icon()
            self.update_menu()
        else:
            self.show_notification(
                "Error Stopping", err_msg or "Could not terminate the process.", "error"
            )

    def restart_server(self):
        self.stop_server()
        GLib.timeout_add(1000, self.start_server)

    def check_updates_from_menu(self, widget):
        self.show_notification("Llama Tray", "Checking for updates...", "info")

        def run_check():
            try:
                releases = updater.get_releases(force_check=True)
                if not releases:
                    GLib.idle_add(
                        self.show_notification,
                        "Llama Tray",
                        "No release found on GitHub.",
                        "info",
                    )
                    return

                latest_tag = releases[0].get("tag_name", "")
                current_active = self.config.data.get("current_version", "")

                if latest_tag == current_active:
                    GLib.idle_add(
                        self.show_notification,
                        "Llama Tray",
                        f"You are already on the latest version ({latest_tag})!",
                        "info",
                    )
                else:
                    GLib.idle_add(self.notify_new_version, latest_tag)
            except Exception as e:
                GLib.idle_add(
                    self.show_notification, "Error Checking Updates", str(e), "error"
                )

        threading.Thread(target=run_check, daemon=True).start()

    def notify_new_version(self, latest_tag):
        self.show_notification(
            "New Version Available!",
            f"Version {latest_tag} is available. Open Settings to update.",
            "info",
        )

        GLib.idle_add(self.open_settings, None)

    def open_settings(self, widget):
        if self.settings_window is not None:
            self.settings_window.present()
        else:
            self.settings_window = SettingsWindow(self, self)
            self.settings_window.show_all()

    def open_logs(self, widget):
        if self.logs_window is not None:
            self.logs_window.present()
        else:
            self.logs_window = LogsWindow(self, self)
            self.logs_window.show_all()

    def quit_app(self):
        self.process_manager.stop()  # Return value intentionally ignored on exit
        try:
            Notify.uninit()
        except Exception:
            pass
        self.quit()


def print_help():
    help_text = """
Llama.tray - System Tray Manager for llama.cpp

Usage:
  llama-tray [options]

Options:
  --autostart    Start the llama-server automatically on launch.
  --help, -h     Show this help message and exit.

Example:
  llama-tray --autostart
    """
    print(help_text)


def main():
    GLib.set_prgname("llama-tray")
    GLib.set_application_name("Llama Tray")

    valid_args = {"--autostart", "--help", "-h"}
    autostart = False

    # Process arguments
    for arg in sys.argv[1:]:
        if arg in ("--help", "-h"):
            print_help()
            sys.exit(0)
        elif arg == "--autostart":
            autostart = True
        elif arg not in valid_args:
            print(f"Error: Invalid argument '{arg}'")
            print_help()
            sys.exit(1)

    # Ensure directories exist before setting up the logger or config
    updater.ensure_dirs()

    app = LlamaTrayApp(autostart=autostart)

    def on_sigint(signum, frame):
        app.quit_app()

    signal.signal(signal.SIGINT, on_sigint)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
