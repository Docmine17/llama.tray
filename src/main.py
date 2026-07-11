#!/usr/bin/env python3
import signal
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except ValueError:
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator  # noqa: E402
    except ValueError:
        AppIndicator = None

gi.require_version("Notify", "0.7")
from gi.repository import Gio, GLib, Gtk, Notify  # noqa: E402

import updater
from config import LlamaConfig
from process_manager import LlamaProcessManager
from profiles import LlamaProfilesManager
from ui_logs import LogsWindow
from ui_settings import SettingsWindow


class LlamaTrayApp(Gtk.Application):
    def __init__(self, autostart=False):
        super().__init__(
            application_id="com.github.llamatray",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
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
            # No active version means no terminal links should remain.
            updater.manage_symlinks(None, False)

        # Sync autostart configuration
        autostart_mode = self.config.get("autostart", "Disabled")
        updater.manage_autostart(autostart_mode)

        self.hold()

        if AppIndicator is None:
            raise RuntimeError(
                "Neither AyatanaAppIndicator3 nor AppIndicator3 is available."
            )
        self.indicator = AppIndicator.Indicator.new(
            "llama-tray",
            "llama-tray-stopped-symbolic",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

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
            self.update_icon()
            self.update_menu()
        else:
            self.show_notification("Error Starting", msg, "error")

    def stop_server(self, widget=None):
        success, err_msg = self.process_manager.stop()
        if success:
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
