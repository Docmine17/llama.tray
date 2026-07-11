import os
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ui_base import LlamaWindow  # noqa: E402


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
