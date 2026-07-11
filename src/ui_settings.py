import copy
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import updater  # noqa: E402
from ui_base import LlamaWindow  # noqa: E402


class SettingsWindow(LlamaWindow):
    def __init__(self, gtk_app, logic_app):
        super().__init__(gtk_app, "llama.tray Settings", 600, 900)
        self.logic_app = logic_app
        self.set_resizable(True)

        self.download_thread = None
        self.pending_apply = None
        self.releases_list = []
        self.online_releases_loaded = False
        self.fetch_error_msg = ""

        self.profiles_manager = self.logic_app.profiles_manager
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
            label="Add llama.cpp binaries to ~/.local/bin"
        )
        conf_grid.attach(
            Gtk.Label(label="Terminal Integration:", xalign=0.0), 0, 2, 1, 1
        )
        conf_grid.attach(self.term_check, 1, 2, 1, 1)

        self.autostart_combo = Gtk.ComboBoxText()
        self.autostart_combo.append("Disabled", "Disabled")
        self.autostart_combo.append("Enabled", "Enabled")
        self.autostart_combo.append("Enabled with Server", "Enabled with Server")
        self.autostart_combo.set_hexpand(True)
        conf_grid.attach(Gtk.Label(label="Autostart:", xalign=0.0), 0, 3, 1, 1)
        conf_grid.attach(self.autostart_combo, 1, 3, 1, 1)

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
        self.connect("delete-event", self.on_delete_event)
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

        self._updating_profile_ui = False

        if row_to_select:
            self.profiles_listbox.select_row(row_to_select)
        else:
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

        prof = next(
            (p for p in self.local_profiles if p["name"] == self.current_profile_name),
            None,
        )
        if not prof:
            return

        if not new_name:
            self.set_status_message(
                "<span color='red'>Profile name cannot be empty.</span>",
                is_markup=True,
            )
            return

        if new_name != prof["name"]:
            if any(p["name"] == new_name for p in self.local_profiles if p != prof):
                self.set_status_message(
                    f"<span color='red'>Profile '{new_name}' already exists.</span>",
                    is_markup=True,
                )
                return

            self.set_status_message("")
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

        prof = next(
            (p for p in self.local_profiles if p["name"] == self.current_profile_name),
            None,
        )
        if not prof:
            return

        has_custom_data = bool(
            prof.get("env_vars", "").strip()
            or prof.get("args", "").strip() not in ("", "--port 8080 --host 127.0.0.1")
        )

        if has_custom_data:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Confirm Deletion",
            )
            dialog.format_secondary_text(
                f"The profile '{prof['name']}' has custom configurations. Are you sure you want to delete it?"
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.YES:
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
        self.autostart_combo.set_active_id(config_data.get("autostart", "Disabled"))

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

    def _build_pending_apply(self, version, backend):
        return {"version": version, "backend": backend}

    def _save_independent_settings(self, autostart_mode):
        self.profiles_manager.profiles = copy.deepcopy(self.local_profiles)
        self.profiles_manager.save()
        terminal_integration = self.term_check.get_active()
        self.logic_app.config.set_bulk(
            {
                "terminal_integration": terminal_integration,
                "current_profile": self.current_profile_name,
                "autostart": autostart_mode,
            }
        )

        current_version = self.logic_app.config.get("current_version")
        current_backend = self.logic_app.config.get("backend", "vulkan")
        version_id = (
            updater.get_version_id(current_version, current_backend)
            if current_version
            else None
        )
        link_result = updater.manage_symlinks(version_id, terminal_integration)
        if not link_result and (version_id or not terminal_integration):
            self.logic_app.show_notification(
                "Terminal Integration",
                link_result.error or "Could not update links.",
                "error",
            )
        if not updater.manage_autostart(autostart_mode):
            self.logic_app.show_notification(
                "Autostart", "Could not update autostart configuration.", "error"
            )

    def _apply_pending_settings(self, pending):
        self.logic_app.config.set_bulk(
            {
                "current_version": pending["version"],
                "backend": pending["backend"],
            }
        )

        version_id = updater.get_version_id(pending["version"], pending["backend"])
        link_result = updater.manage_symlinks(
            version_id, self.logic_app.config.get("terminal_integration", False)
        )
        if not link_result:
            self.logic_app.show_notification(
                "Terminal Integration",
                link_result.error or "Could not update links.",
                "error",
            )

    def on_action_clicked(self, widget):
        selected_version = self.get_selected_version()
        backend = self.backend_combo.get_active_id()
        autostart_mode = self.autostart_combo.get_active_id()

        if (
            not selected_version
            or selected_version in ("loading", "none")
            or not backend
        ):
            self.set_status_message(
                "<span color='red'>Please select a valid llama.cpp version.</span>",
                is_markup=True,
            )
            return

        seen_names = set()
        for p in self.local_profiles:
            name = p["name"].strip()
            if not name:
                self.set_status_message(
                    "<span color='red'>Cannot save: one or more profiles have an empty name.</span>",
                    is_markup=True,
                )
                return
            if name in seen_names:
                self.set_status_message(
                    f"<span color='red'>Cannot save: duplicate profile name '{name}'.</span>",
                    is_markup=True,
                )
                return
            seen_names.add(name)

        self._save_independent_settings(autostart_mode)
        pending = self._build_pending_apply(selected_version, backend)
        if updater.is_version_installed(selected_version, backend):
            self._apply_pending_settings(pending)
            if self.logic_app.process_manager.is_running():
                self.logic_app.restart_server()
            self.destroy()
            return

        self.pending_apply = pending
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
        pending = self.pending_apply
        if not pending or pending["version"] != tag_name:
            self.on_download_error(
                "Downloaded version no longer matches pending settings."
            )
            return

        self._apply_pending_settings(pending)
        self.pending_apply = None
        self.logic_app.show_notification(
            "Download Complete", f"Version {tag_name} installed successfully!"
        )

        self.logic_app.set_updating_state(False)

        if self.logic_app.process_manager.is_running():
            self.logic_app.restart_server()

        self.destroy()

    def on_download_error(self, err_msg):
        self.pending_apply = None
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

    def on_delete_event(self, widget, event):
        if self.download_thread and self.download_thread.is_alive():
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Cancel Download?",
            )
            dialog.format_secondary_text(
                "A version download is currently in progress. Closing this window will cancel the download. Do you want to cancel and exit?"
            )
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.YES:
                return False  # Let the window destroy
            else:
                return True  # Prevent the window from destroying
        return False
