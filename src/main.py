import os
import sys
import json
import socket
import subprocess
import shlex
import threading
import signal
import ctypes
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, Gdk, GLib, AyatanaAppIndicator3, Notify
import updater

# Directory configurations
INSTALL_DIR = os.path.expanduser("~/.local/share/llama.tray/bin")
CONFIG_DIR = os.path.expanduser("~/.config/llama.tray")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.expanduser("~/.local/share/llama.tray")

# Resolve o caminho para a pasta data/tray de forma relativa ao main.py
# (Assumindo que main.py está em src/ e os ícones em data/tray/)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ICON_DIR = os.path.join(BASE_DIR, "data", "tray")
APP_ICON = os.path.join(BASE_DIR, "data", "llama-tray-icon.svg")

os.makedirs(INSTALL_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


class LlamaConfig:
    def __init__(self):
        self.data = {
            "current_version": "",
            "backend": "vulkan",
            "env_vars": "",
            "args": "--port 8080 --host 127.0.0.1"
        }
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
            except Exception as e:
                print(f"Error loading config: {e}", file=sys.stderr)

    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def get_data(self):
        return self.data

    def set(self, key, value):
        self.data[key] = value
        self.save()


class LlamaProcessManager:
    def __init__(self, config, on_unexpected_exit=None):
        self.config = config
        self.process = None
        self.log_file_path = os.path.join(LOG_DIR, "llama.log")
        self.on_unexpected_exit = on_unexpected_exit
        self.intentional_stop = False

    def is_running(self):
        if self.process is None:
            return False
        return self.process.poll() is None

    def rotate_logs(self):
        try:
            if os.path.exists(self.log_file_path):
                if os.path.getsize(self.log_file_path) > 10 * 1024 * 1024:
                    rotate_path = self.log_file_path + ".1"
                    if os.path.exists(rotate_path):
                        os.remove(rotate_path)
                    os.rename(self.log_file_path, rotate_path)
        except Exception as e:
            print(f"Error rotating logs: {e}", file=sys.stderr)

    def start(self):
        if self.is_running():
            return True, "O servidor já está a correr."

        config_data = self.config.get_data()
        version = config_data.get("current_version", "")
        if not version:
            return False, "Nenhuma versão ativa. Por favor, vá às Configurações e instale uma versão."

        backend = config_data.get("backend", "vulkan")
        version_id = updater.get_version_id(version, backend)
        
        version_dir = os.path.join(INSTALL_DIR, version_id)
        server_bin = os.path.join(version_dir, "llama-server")
        if not os.path.exists(server_bin):
            return False, f"Executável não encontrado para o backend '{backend}' em: {server_bin}"

        args_str = config_data.get("args", "")

        self.rotate_logs()

        env = os.environ.copy()
        env_vars_str = config_data.get("env_vars", "")
        for line in env_vars_str.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

        try:
            cmd = [server_bin] + shlex.split(args_str)
        except Exception as e:
            return False, f"Falha ao processar argumentos: {e}"

        def preexec():
            try:
                libc = ctypes.CDLL("libc.so.6")
                libc.prctl(1, 15)
            except Exception:
                pass

        try:
            log_file = open(self.log_file_path, "a")
            self.intentional_stop = False
            
            self.process = subprocess.Popen(
                cmd,
                cwd=version_dir,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                preexec_fn=preexec,
                start_new_session=True
            )
            log_file.close()
            
            threading.Thread(target=self._watch_process, daemon=True).start()
            
            return True, "Servidor iniciado."
        except Exception as e:
            return False, f"Falha ao iniciar processo: {e}"

    def _watch_process(self):
        if not self.process:
            return
            
        try:
            exit_code = self.process.wait()
        except Exception:
            exit_code = -1
            
        if not self.intentional_stop:
            self.process = None
            if self.on_unexpected_exit:
                GLib.idle_add(self.on_unexpected_exit, exit_code)

    def stop(self):
        if not self.is_running():
            return True

        self.intentional_stop = True
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None
            return True
        except Exception as e:
            return False, f"Erro ao parar processo: {e}"


class LogsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Logs do llama.cpp")
        self.app = app
        self.set_default_size(700, 450)
        self.get_style_context().add_class("logs-window")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        self.add(vbox)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        vbox.pack_start(scrolled, True, True, 0)

        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.add(self.text_view)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        vbox.pack_start(hbox, False, False, 0)

        clear_btn = Gtk.Button(label="Limpar Logs")
        clear_btn.get_style_context().add_class("destructive-action")
        clear_btn.connect("clicked", self.on_clear_clicked)
        hbox.pack_start(clear_btn, False, False, 0)

        close_btn = Gtk.Button(label="Fechar")
        close_btn.connect("clicked", lambda w: self.destroy())
        hbox.pack_end(close_btn, False, False, 0)

        self.log_file_path = self.app.process_manager.log_file_path
        self.file_pos = 0
        self.timer_id = None

        self.connect("destroy", self.on_destroy)
        self.load_initial_logs()
        self.timer_id = GLib.timeout_add(500, self.tail_logs)

    def load_initial_logs(self):
        if not os.path.exists(self.log_file_path):
            self.text_view.get_buffer().set_text("Nenhum log disponível.")
            return

        try:
            with open(self.log_file_path, "r", errors="replace") as f:
                lines = f.readlines()
                initial_content = "".join(lines[-250:])
            self.text_view.get_buffer().set_text(initial_content)
            self.file_pos = os.path.getsize(self.log_file_path)
            self.scroll_to_bottom()
        except Exception as e:
            self.text_view.get_buffer().set_text(f"Erro ao ler logs: {e}")

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
            self.text_view.get_buffer().set_text(f"Erro ao limpar logs: {e}")

    def on_destroy(self, widget):
        if self.timer_id:
            GLib.source_remove(self.timer_id)
        self.app.logs_window = None


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Configurações do llama.tray")
        self.app = app
        self.set_default_size(550, 480)
        self.set_resizable(False)

        # Set main app icon for the window
        if os.path.exists(APP_ICON):
            self.set_icon_from_file(APP_ICON)

        self.download_thread = None
        self.releases_list = []
        self.online_releases_loaded = False
        self.fetch_error_msg = ""

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        self.add(vbox)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        vbox.pack_start(grid, True, True, 0)

        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span size='large' weight='bold'>Configurações do llama.cpp</span>")
        title_lbl.set_xalign(0.0)
        grid.attach(title_lbl, 0, 0, 2, 1)

        grid.attach(Gtk.Label(label="Aceleração (Backend):", xalign=0), 0, 1, 1, 1)
        self.backend_combo = Gtk.ComboBoxText()
        self.backend_combo.append("vulkan", "Vulkan (GPU)")
        self.backend_combo.append("cpu", "CPU (Padrão)")
        self.backend_combo.set_hexpand(True)
        self.backend_combo.connect("changed", self.on_backend_changed)
        grid.attach(self.backend_combo, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Versão (Release Tag):", xalign=0), 0, 2, 1, 1)
        
        version_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.version_combo = Gtk.ComboBoxText()
        self.version_combo.set_hexpand(True)
        self.version_combo.connect("changed", self.on_version_changed)
        version_hbox.pack_start(self.version_combo, True, True, 0)
        
        self.refresh_releases_btn = Gtk.Button()
        self.refresh_releases_btn.set_tooltip_text("Procurar atualizações no GitHub")
        refresh_img = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_releases_btn.set_image(refresh_img)
        self.refresh_releases_btn.connect("clicked", lambda w: self.load_releases(force=True))
        version_hbox.pack_start(self.refresh_releases_btn, False, False, 0)
        
        grid.attach(version_hbox, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Variáveis de Ambiente:\n(Antes do comando)", xalign=0), 0, 3, 1, 1)
        scrolled_env = Gtk.ScrolledWindow()
        scrolled_env.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_env.set_size_request(-1, 80)
        self.env_view = Gtk.TextView()
        self.env_view.set_accepts_tab(False)
        scrolled_env.add(self.env_view)
        grid.attach(scrolled_env, 1, 3, 1, 1)

        grid.attach(Gtk.Label(label="Argumentos:\n(Depois do comando)", xalign=0), 0, 4, 1, 1)
        scrolled_args = Gtk.ScrolledWindow()
        scrolled_args.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_args.set_size_request(-1, 80)
        self.args_view = Gtk.TextView()
        self.args_view.set_accepts_tab(False)
        self.args_view.set_wrap_mode(Gtk.WrapMode.WORD)
        scrolled_args.add(self.args_view)
        grid.attach(scrolled_args, 1, 4, 1, 1)

        self.status_lbl = Gtk.Label(label="", xalign=0)
        vbox.pack_start(self.status_lbl, False, False, 0)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_no_show_all(True)
        self.progress_bar.hide()
        vbox.pack_start(self.progress_bar, False, False, 0)

        hbox_btn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        vbox.pack_start(hbox_btn, False, False, 0)

        self.action_btn = Gtk.Button(label="Guardar e Aplicar")
        self.action_btn.get_style_context().add_class("suggested-action")
        self.action_btn.connect("clicked", self.on_action_clicked)
        hbox_btn.pack_end(self.action_btn, False, False, 0)

        self.cancel_btn = Gtk.Button(label="Cancelar")
        self.cancel_btn.connect("clicked", lambda w: self.destroy())
        hbox_btn.pack_end(self.cancel_btn, False, False, 0)

        self.load_fields_from_config()
        self.connect("destroy", self.on_destroy)

        self.load_releases(force=False)

    def load_fields_from_config(self):
        config_data = self.app.config.get_data()
        backend = config_data.get("backend", "vulkan")
        self.backend_combo.set_active_id(backend)

        env_buffer = self.env_view.get_buffer()
        env_buffer.set_text(config_data.get("env_vars", ""))

        args_buffer = self.args_view.get_buffer()
        args_buffer.set_text(config_data.get("args", ""))

    def load_releases(self, force=False):
        self.version_combo.set_sensitive(False)
        self.refresh_releases_btn.set_sensitive(False)
        self.version_combo.remove_all()
        self.version_combo.append("loading", "A procurar versões no GitHub...")
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
        items = []
        
        online_tags = set()
        for r in self.releases_list:
            tag = r.get("tag_name", "")
            online_tags.add(tag)
            is_inst = updater.is_version_installed(tag, backend)
            status_text = " (Instalado)" if is_inst else " (Disponível)"
            items.append((tag, f"{tag}{status_text}"))
            
        installed_folders = updater.get_installed_versions()
        local_tags_added = set()
        for folder in installed_folders:
            tag, f_backend = updater.parse_version_id(folder)
            if tag not in online_tags and tag not in local_tags_added:
                local_tags_added.add(tag)
                is_inst = updater.is_version_installed(tag, backend)
                status_text = " (Instalado)" if is_inst else " (Disponível - Outro Backend)"
                items.append((tag, f"{tag}{status_text}"))
                
        if not items:
            self.version_combo.append("none", "Nenhuma versão encontrada")
            self.version_combo.set_active(0)
            self.status_lbl.set_text("Offline: Nenhuma versão local ou remota encontrada.")
            self.version_combo.set_sensitive(True)
            self.refresh_releases_btn.set_sensitive(True)
            self.version_combo.connect("changed", self.on_version_changed)
            return

        for tag, display in items:
            self.version_combo.append(tag, display)

        active_index = 0
        current_version = self.app.config.get_data().get("current_version", "")
        tag_to_select = selected_tag if selected_tag and selected_tag not in ("loading", "none") else current_version
        
        if tag_to_select:
            for idx, item in enumerate(items):
                if item[0] == tag_to_select:
                    active_index = idx
                    break
        self.version_combo.set_active(active_index)
        
        self.version_combo.set_sensitive(True)
        self.refresh_releases_btn.set_sensitive(True)
        
        if not self.online_releases_loaded:
            self.status_lbl.set_text(f"Offline: a mostrar apenas versões locais. (Erro: {self.fetch_error_msg})")
        else:
            self.status_lbl.set_text("")
            
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
        if not selected_version or selected_version in ("loading", "none") or not backend:
            self.action_btn.set_label("Guardar e Aplicar")
            return
            
        is_inst = updater.is_version_installed(selected_version, backend)
        if is_inst:
            self.action_btn.set_label("Guardar e Aplicar")
        else:
            self.action_btn.set_label("Transferir, Instalar e Aplicar")

    def on_action_clicked(self, widget):
        selected_version = self.get_selected_version()
        backend = self.backend_combo.get_active_id()
        
        if not selected_version or selected_version in ("loading", "none"):
            self.status_lbl.set_markup("<span color='red'>Por favor, selecione uma versão válida do llama.cpp.</span>")
            return

        env_buf = self.env_view.get_buffer()
        env_vars = env_buf.get_text(env_buf.get_start_iter(), env_buf.get_end_iter(), True).strip()

        args_buf = self.args_view.get_buffer()
        args_str = args_buf.get_text(args_buf.get_start_iter(), args_buf.get_end_iter(), True).strip()

        self.app.config.set("backend", backend)
        self.app.config.set("env_vars", env_vars)
        self.app.config.set("args", args_str)

        if updater.is_version_installed(selected_version, backend):
            self.app.config.set("current_version", selected_version)
            self.app.show_notification("Configuração Guardada", f"Versão {selected_version} ({backend}) ativa.")
            
            if self.app.process_manager.is_running():
                self.app.restart_server()
            self.destroy()
        else:
            self.start_download(selected_version, backend)

    def start_download(self, tag_name, backend):
        release_obj = None
        for r in self.releases_list:
            if r.get("tag_name") == tag_name:
                release_obj = r
                break
                
        if not release_obj:
            self.status_lbl.set_markup("<span color='red'>Erro: Não foi possível encontrar metadados para transferir esta versão offline.</span>")
            return

        asset_info = updater.get_asset_for_backend(release_obj, backend)
        if not asset_info:
            self.status_lbl.set_markup(f"<span color='red'>Erro: Não encontrámos um binário compatível com '{backend}' no lançamento {tag_name}.</span>")
            return

        asset_name, download_url, expected_sha256 = asset_info

        self.set_sensitive_inputs(False)
        self.status_lbl.set_text("A iniciar transferência...")
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.show()
        
        self.app.set_updating_state(True)

        version_id = updater.get_version_id(tag_name, backend)
        self.download_thread = updater.DownloadThread(
            tag_name=tag_name,
            version_id=version_id,
            download_url=download_url,
            expected_sha256=expected_sha256,
            on_progress=self.on_download_progress,
            on_done=self.on_download_done,
            on_error=self.on_download_error
        )
        self.download_thread.start()

    def set_sensitive_inputs(self, sensitive):
        self.backend_combo.set_sensitive(sensitive)
        self.version_combo.set_sensitive(sensitive)
        self.refresh_releases_btn.set_sensitive(sensitive)
        self.env_view.set_sensitive(sensitive)
        self.args_view.set_sensitive(sensitive)
        self.action_btn.set_sensitive(sensitive)
        self.cancel_btn.set_sensitive(sensitive)

    def on_download_progress(self, message, fraction):
        self.status_lbl.set_text(message)
        self.progress_bar.set_fraction(fraction)

    def on_download_done(self, tag_name, target_dir):
        self.app.config.set("current_version", tag_name)
        
        self.app.show_notification(
            "Transferência Concluída",
            f"Versão {tag_name} instalada com sucesso!"
        )
        
        self.app.set_updating_state(False)
        
        if self.app.process_manager.is_running():
            self.app.restart_server()
            
        self.destroy()

    def on_download_error(self, err_msg):
        self.set_sensitive_inputs(True)
        self.progress_bar.hide()
        self.status_lbl.set_markup(f"<span color='red'>Falha na instalação: {err_msg}</span>")
        self.app.set_updating_state(False)
        self.app.show_notification("Erro na Instalação", f"Ocorreu um erro: {err_msg}", "error")

    def on_destroy(self, widget):
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.stop()
        self.app.set_updating_state(False)
        self.app.settings_window = None


class LlamaTrayApp:
    def __init__(self):
        Notify.init("llama.tray")

        self.config = LlamaConfig()
        
        self.process_manager = LlamaProcessManager(
            self.config,
            on_unexpected_exit=self.on_server_crashed
        )

        self.settings_window = None
        self.logs_window = None
        self.is_updating = False

        # Apontamos o AppIndicator para ler diretamente da pasta ICON_DIR resolvida (data/tray)
        # O nome que passamos aqui é SEM a extensão .svg
        self.indicator = AyatanaAppIndicator3.Indicator.new_with_path(
            "llama.tray",
            "llama-tray-stopped-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            ICON_DIR
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)
        self.update_menu()

        signal.signal(signal.SIGINT, self.quit)
        signal.signal(signal.SIGTERM, self.quit)

    def show_notification(self, title, message, icon_type="info"):
        icon_path = APP_ICON
        try:
            notification = Notify.Notification.new(title, message, icon_path)
            notification.show()
        except Exception as e:
            print(f"Error showing notification: {e}", file=sys.stderr)

    def on_server_crashed(self, exit_code):
        self.update_icon()
        self.update_menu()
        self.show_notification(
            "Servidor Parou Inesperadamente", 
            f"O llama-server encerrou com erro (Código: {exit_code}). Verifique os logs para mais detalhes.", 
            "error"
        )

    def update_menu(self):
        for child in self.menu.get_children():
            self.menu.remove(child)

        if self.is_updating:
            start_stop_item = Gtk.MenuItem(label="A atualizar binários...")
            start_stop_item.set_sensitive(False)
        else:
            if self.process_manager.is_running():
                start_stop_item = Gtk.MenuItem(label="Parar")
                start_stop_item.connect("activate", lambda w: self.stop_server())
            else:
                start_stop_item = Gtk.MenuItem(label="Iniciar")
                start_stop_item.connect("activate", lambda w: self.start_server())
        self.menu.append(start_stop_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        update_item = Gtk.MenuItem(label="Verificar atualizações")
        update_item.connect("activate", self.check_updates_from_menu)
        update_item.set_sensitive(not self.is_updating)
        self.menu.append(update_item)

        settings_item = Gtk.MenuItem(label="Configurações")
        settings_item.connect("activate", self.open_settings)
        settings_item.set_sensitive(not self.is_updating)
        self.menu.append(settings_item)

        logs_item = Gtk.MenuItem(label="Logs")
        logs_item.connect("activate", self.open_logs)
        self.menu.append(logs_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        exit_item = Gtk.MenuItem(label="Sair")
        exit_item.connect("activate", lambda w: self.quit())
        self.menu.append(exit_item)

        self.menu.show_all()

    def set_updating_state(self, updating):
        self.is_updating = updating
        if updating:
            self.indicator.set_icon_full("llama-tray-updating-symbolic", "A atualizar llama.cpp")
        else:
            self.update_icon()
        self.update_menu()

    def update_icon(self):
        if self.process_manager.is_running():
            self.indicator.set_icon_full("llama-tray-running-symbolic", "Servidor a correr")
        else:
            self.indicator.set_icon_full("llama-tray-stopped-symbolic", "Servidor parado")

    def start_server(self):
        success, msg = self.process_manager.start()
        if success:
            self.show_notification("Llama Server", "Servidor iniciado com sucesso!", "success")
            self.update_icon()
            self.update_menu()
        else:
            self.show_notification("Erro ao Iniciar", msg, "error")

    def stop_server(self):
        success = self.process_manager.stop()
        if success:
            self.show_notification("Llama Server", "Servidor parado.", "info")
            self.update_icon()
            self.update_menu()
        else:
            self.show_notification("Erro ao Parar", "Não foi possível finalizar o processo.", "error")

    def restart_server(self):
        self.stop_server()
        GLib.timeout_add(1000, self.start_server)

    def check_updates_from_menu(self, widget):
        self.show_notification("Llama.tray", "A procurar atualizações...", "info")
        
        def run_check():
            try:
                releases = updater.get_releases(force_check=True)
                if not releases:
                    GLib.idle_add(self.show_notification, "Llama.tray", "Nenhum lançamento encontrado no GitHub.", "info")
                    return
                    
                latest_tag = releases[0].get("tag_name", "")
                current_active = self.config.get_data().get("current_version", "")
                
                if latest_tag == current_active:
                    GLib.idle_add(self.show_notification, "Llama.tray", f"Já está na versão mais recente ({latest_tag})!", "info")
                else:
                    GLib.idle_add(self.notify_new_version, latest_tag)
            except Exception as e:
                GLib.idle_add(self.show_notification, "Erro ao Procurar", str(e), "error")

        threading.Thread(target=run_check, daemon=True).start()

    def notify_new_version(self, latest_tag):
        self.show_notification(
            "Nova Versão Disponível!",
            f"A versão {latest_tag} está disponível. Abra as Configurações para atualizar.",
            "info"
        )
        
        GLib.idle_add(self.open_settings, None)

    def open_settings(self, widget):
        if self.settings_window is not None:
            self.settings_window.present()
        else:
            self.settings_window = SettingsWindow(self)
            self.settings_window.show_all()

    def open_logs(self, widget):
        if self.logs_window is not None:
            self.logs_window.present()
        else:
            self.logs_window = LogsWindow(self)
            self.logs_window.show_all()

    def quit(self, *args):
        self.process_manager.stop()
        try:
            Notify.uninit()
        except Exception:
            pass
        Gtk.main_quit()
        sys.exit(0)


def main():
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, Gtk.main_quit)
    app = LlamaTrayApp()
    Gtk.main()


if __name__ == "__main__":
    main()