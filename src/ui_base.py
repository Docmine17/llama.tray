import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


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
