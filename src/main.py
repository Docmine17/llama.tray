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
import cairo
import updater

# Directory configurations
INSTALL_DIR = os.path.expanduser("~/.local/share/llama.tray/bin")
CONFIG_DIR = os.path.expanduser("~/.config/llama.tray")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.expanduser("~/.local/share/llama.tray")
ICON_DIR = os.path.expanduser("~/.local/share/llama.tray")

os.makedirs(INSTALL_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


# Llama Icon Drawing helper using Cairo
def draw_llama_icon(output_path, color_rgb):
    """Draws a neat modern stylized llama icon and saves to PNG."""
    # 64x64 pixel canvas for high-DPI scaling
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
    ctx = cairo.Context(surface)
    
    # Clear transparent
    ctx.set_operator(cairo.OPERATOR_CLEAR)
    ctx.paint()
    ctx.set_operator(cairo.OPERATOR_OVER)
    
    # Stylized drawing details
    ctx.set_source_rgb(*color_rgb)
    ctx.set_line_width(4.0)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    
    # Head and Ears geometry (scaled to fit nicely in 64x64)
    # Neck back
    ctx.move_to(22, 58)
    ctx.line_to(22, 34)
    
    # Left Ear
    ctx.line_to(14, 12)
    ctx.line_to(20, 12)
    ctx.line_to(24, 26)
    
    # Right Ear
    ctx.line_to(26, 8)
    ctx.line_to(32, 8)
    ctx.line_to(34, 26)
    
    # Snout
    ctx.line_to(48, 26)
    ctx.line_to(48, 36)
    ctx.line_to(38, 36)
    ctx.line_to(34, 42)
    
    # Neck front
    ctx.line_to(34, 58)
    ctx.stroke()
    
    # Cute dot eye
    ctx.arc(33, 22, 2.5, 0, 2 * 3.14159)
    ctx.fill()
    
    surface.write_to_png(output_path)
    surface.finish()


def ensure_icons():
    """Generates the three state icons if missing or needed."""
    try:
        draw_llama_icon(os.path.join(ICON_DIR, "llama_stopped.png"), (0.55, 0.58, 0.64))  # Sleek Gray
        draw_llama_icon(os.path.join(ICON_DIR, "llama_running.png"), (0.18, 0.8, 0.44))    # Emerald Green
        draw_llama_icon(os.path.join(ICON_DIR, "llama_updating.png"), (0.2, 0.6, 1.0))     # Dodger Blue
    except Exception as e:
        print(f"Error drawing icons: {e}", file=sys.stderr)


# Configuration Manager
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


def get_configured_port(args_str, default=8080):
    """Safely extracts port from arguments."""
    try:
        parts = shlex.split(args_str)
        for i, part in enumerate(parts):
            if part in ("-p", "--port") and i + 1 < len(parts):
                return int(parts[i+1])
    except Exception:
        pass
    return default


# Subprocess Process Manager
class LlamaProcessManager:
    def __init__(self, config):
        self.config = config
        self.process = None
        self.log_file_path = os.path.join(LOG_DIR, "llama.log")

    def is_running(self):
        if self.process is None:
            return False
        return self.process.poll() is None

    def check_port_free(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def rotate_logs(self):
        try:
            if os.path.exists(self.log_file_path):
                if os.path.getsize(self.log_file_path) > 10 * 1024 * 1024:  # 10MB limit
                    rotate_path = self.log_file_path + ".1"
                    if os.path.exists(rotate_path):
                        os.remove(rotate_path)
                    os.rename(self.log_file_path, rotate_path)
        except Exception as e:
            print(f"Error rotating logs: {e}", file=sys.stderr)

    def start(self):
        if self.is_running():
            return True, "Servidor já está rodando."

        config_data = self.config.get_data()
        version = config_data.get("current_version", "")
        if not version:
            return False, "Nenhuma versão ativa. Por favor, vá em Configurações e instale uma versão."

        version_dir = os.path.join(INSTALL_DIR, version)
        server_bin = os.path.join(version_dir, "llama-server")
        if not os.path.exists(server_bin):
            return False, f"Executável não encontrado em: {server_bin}"

        args_str = config_data.get("args", "")
        port = get_configured_port(args_str)
        if not self.check_port_free(port):
            return False, f"Porta {port} ocupada. Altere nas configurações ou feche o outro serviço."

        self.rotate_logs()

        # Build environments
        env = os.environ.copy()
        env_vars_str = config_data.get("env_vars", "")
        for line in env_vars_str.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

        # Parse command list safely (prevent injection)
        try:
            cmd = [server_bin] + shlex.split(args_str)
        except Exception as e:
            return False, f"Falha ao processar argumentos: {e}"

        # Prevent Zombie / Orphan process by registering PR_SET_PDEATHSIG (Linux only)
        def preexec():
            try:
                libc = ctypes.CDLL("libc.so.6")
                # PR_SET_PDEATHSIG = 1, SIGTERM = 15
                libc.prctl(1, 15)
            except Exception:
                pass

        try:
            log_file = open(self.log_file_path, "a")
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
            return True, "Servidor iniciado."
        except Exception as e:
            return False, f"Falha ao iniciar processo: {e}"

    def stop(self):
        if not self.is_running():
            return True

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


# Logs Viewer GTK 3 Window
class LogsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Logs do llama.cpp")
        self.app = app
        self.set_default_size(700, 450)
        
        # Apply dark custom CSS class
        self.get_style_context().add_class("logs-window")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        self.add(vbox)

        # Scrolled View
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

        # Bottom Bar
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
        
        # Poll logs every 500ms
        self.timer_id = GLib.timeout_add(500, self.tail_logs)

    def load_initial_logs(self):
        if not os.path.exists(self.log_file_path):
            self.text_view.get_buffer().set_text("Nenhum log disponível.")
            return

        try:
            with open(self.log_file_path, "r", errors="replace") as f:
                lines = f.readlines()
                initial_content = "".join(lines[-250:]) # display last 250 lines
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
            # If log rotated
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


# Settings GTK 3 Window
class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Configurações do llama.tray")
        self.app = app
        self.set_default_size(550, 480)
        self.set_resizable(False)

        # Local variables to track downloading state
        self.download_thread = None

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        self.add(vbox)

        # Form layout
        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        vbox.pack_start(grid, True, True, 0)

        # Header Title
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span size='large' weight='bold'>Configurações do llama.cpp</span>")
        title_lbl.set_xalign(0.0)
        grid.attach(title_lbl, 0, 0, 2, 1)

        # 1. Backend Acceleration Selection
        grid.attach(Gtk.Label(label="Aceleração (Backend):", xalign=0), 0, 1, 1, 1)
        self.backend_combo = Gtk.ComboBoxText()
        self.backend_combo.append("vulkan", "Vulkan (GPU)")
        self.backend_combo.append("cpu", "CPU (Padrão)")
        self.backend_combo.set_hexpand(True)
        self.backend_combo.connect("changed", self.on_backend_changed)
        grid.attach(self.backend_combo, 1, 1, 1, 1)

        # 2. Release Version Selection
        grid.attach(Gtk.Label(label="Versão (Release Tag):", xalign=0), 0, 2, 1, 1)
        
        version_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.version_combo = Gtk.ComboBoxText()
        self.version_combo.set_hexpand(True)
        self.version_combo.connect("changed", self.on_version_changed)
        version_hbox.pack_start(self.version_combo, True, True, 0)
        
        self.refresh_releases_btn = Gtk.Button()
        self.refresh_releases_btn.set_tooltip_text("Buscar atualizações do GitHub")
        # Use standard GTK sync/refresh icon
        refresh_img = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_releases_btn.set_image(refresh_img)
        self.refresh_releases_btn.connect("clicked", lambda w: self.load_releases(force=True))
        version_hbox.pack_start(self.refresh_releases_btn, False, False, 0)
        
        grid.attach(version_hbox, 1, 2, 1, 1)

        # 3. Environment Variables Text Area
        grid.attach(Gtk.Label(label="Variáveis de Ambiente:\n(Antes do comando)", xalign=0), 0, 3, 1, 1)
        scrolled_env = Gtk.ScrolledWindow()
        scrolled_env.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_env.set_size_request(-1, 80)
        self.env_view = Gtk.TextView()
        self.env_view.set_accepts_tab(False)
        scrolled_env.add(self.env_view)
        grid.attach(scrolled_env, 1, 3, 1, 1)

        # 4. Process Arguments Text Area
        grid.attach(Gtk.Label(label="Argumentos:\n(Depois do comando)", xalign=0), 0, 4, 1, 1)
        scrolled_args = Gtk.ScrolledWindow()
        scrolled_args.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_args.set_size_request(-1, 80)
        self.args_view = Gtk.TextView()
        self.args_view.set_accepts_tab(False)
        self.args_view.set_wrap_mode(Gtk.WrapMode.WORD)
        scrolled_args.add(self.args_view)
        grid.attach(scrolled_args, 1, 4, 1, 1)

        # 5. Progress Bar and Status Label (Initially Hidden/Empty)
        self.status_lbl = Gtk.Label(label="", xalign=0)
        vbox.pack_start(self.status_lbl, False, False, 0)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_no_show_all(True)
        self.progress_bar.hide()
        vbox.pack_start(self.progress_bar, False, False, 0)

        # Action Buttons
        hbox_btn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        vbox.pack_start(hbox_btn, False, False, 0)

        self.action_btn = Gtk.Button(label="Salvar e Aplicar")
        self.action_btn.get_style_context().add_class("suggested-action")
        self.action_btn.connect("clicked", self.on_action_clicked)
        hbox_btn.pack_end(self.action_btn, False, False, 0)

        self.cancel_btn = Gtk.Button(label="Cancelar")
        self.cancel_btn.connect("clicked", lambda w: self.destroy())
        hbox_btn.pack_end(self.cancel_btn, False, False, 0)

        # Populate Fields from Config
        self.load_fields_from_config()
        self.connect("destroy", self.on_destroy)

        # Load Releases List in Background
        self.releases_list = []
        self.online_releases_loaded = False
        self.load_releases(force=False)

    def load_fields_from_config(self):
        config_data = self.app.config.get_data()
        
        # Set Backend
        backend = config_data.get("backend", "vulkan")
        self.backend_combo.set_active_id(backend)

        # Set Env Vars
        env_buffer = self.env_view.get_buffer()
        env_buffer.set_text(config_data.get("env_vars", ""))

        # Set Args
        args_buffer = self.args_view.get_buffer()
        args_buffer.set_text(config_data.get("args", ""))

    def load_releases(self, force=False):
        """Fetch releases in background thread."""
        self.version_combo.set_sensitive(False)
        self.refresh_releases_btn.set_sensitive(False)
        self.version_combo.remove_all()
        self.version_combo.append("loading", "Buscando versões do GitHub...")
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
        self.version_combo.remove_all()
        self.releases_list = releases
        self.online_releases_loaded = success
        
        # Check locally installed versions
        installed = updater.get_installed_versions()
        current_version = self.app.config.get_data().get("current_version", "")
        
        # Combine online and local lists
        # We store tuples: (tag_name, display_name, is_installed, release_object_or_none)
        items = []
        
        # First, add online releases
        online_tags = set()
        for r in releases:
            tag = r.get("tag_name", "")
            online_tags.add(tag)
            is_inst = tag in installed
            status_text = " (Instalado)" if is_inst else " (Disponível)"
            items.append((tag, f"{tag}{status_text}", is_inst, r))
            
        # Add any locally installed versions that were not returned by the API
        for inst_tag in installed:
            if inst_tag not in online_tags:
                items.append((inst_tag, f"{inst_tag} (Instalado Localmente)", True, None))
                
        # If no items found
        if not items:
            self.version_combo.append("none", "Nenhuma versão encontrada")
            self.version_combo.set_active(0)
            self.status_lbl.set_text("Offline: Nenhuma versão local ou remota encontrada.")
            self.version_combo.set_sensitive(True)
            self.refresh_releases_btn.set_sensitive(True)
            return

        # Populate combobox
        for tag, display, is_inst, r in items:
            self.version_combo.append(tag, display)

        # Select current version if valid, otherwise select the first item
        active_index = 0
        if current_version:
            for idx, item in enumerate(items):
                if item[0] == current_version:
                    active_index = idx
                    break
        self.version_combo.set_active(active_index)
        
        self.version_combo.set_sensitive(True)
        self.refresh_releases_btn.set_sensitive(True)
        
        if not success:
            self.status_lbl.set_text(f"Offline: mostrando apenas versões locais. (Erro: {err_msg})")
        else:
            self.status_lbl.set_text("")

    def on_backend_changed(self, combo):
        self.update_action_button_label()

    def on_version_changed(self, combo):
        self.update_action_button_label()

    def get_selected_version(self):
        return self.version_combo.get_active_id()

    def update_action_button_label(self):
        selected_version = self.get_selected_version()
        if not selected_version or selected_version in ("loading", "none"):
            self.action_btn.set_label("Salvar e Aplicar")
            return
            
        is_inst = updater.is_version_installed(selected_version)
        if is_inst:
            self.action_btn.set_label("Salvar e Aplicar")
        else:
            self.action_btn.set_label("Baixar, Instalar e Aplicar")

    def on_action_clicked(self, widget):
        selected_version = self.get_selected_version()
        if not selected_version or selected_version in ("loading", "none"):
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Versão Inválida",
            )
            dialog.format_secondary_text("Por favor, selecione uma versão válida do llama.cpp.")
            dialog.run()
            dialog.destroy()
            return

        # Get Text Buffers
        env_buf = self.env_view.get_buffer()
        env_vars = env_buf.get_text(env_buf.get_start_iter(), env_buf.get_end_iter(), True).strip()

        args_buf = self.args_view.get_buffer()
        args_str = args_buf.get_text(args_buf.get_start_iter(), args_buf.get_end_iter(), True).strip()

        # Update settings data
        backend = self.backend_combo.get_active_id()
        self.app.config.set("backend", backend)
        self.app.config.set("env_vars", env_vars)
        self.app.config.set("args", args_str)

        # Check if version is already installed
        if updater.is_version_installed(selected_version):
            # Version already installed, just save and switch version
            self.app.config.set("current_version", selected_version)
            self.app.show_notification("Configuração Salva", f"Versão {selected_version} ativa.")
            
            # Restart server if it was running
            if self.app.process_manager.is_running():
                self.app.restart_server()
                
            self.destroy()
        else:
            # Must download and install release
            self.start_download(selected_version, backend)

    def start_download(self, tag_name, backend):
        # Find the release object
        release_obj = None
        for r in self.releases_list:
            if r.get("tag_name") == tag_name:
                release_obj = r
                break
                
        if not release_obj:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Erro ao Localizar Release",
            )
            dialog.format_secondary_text("Não foi possível encontrar metadados para baixar essa versão offline.")
            dialog.run()
            dialog.destroy()
            return

        # Find the asset for backend
        asset_info = updater.get_asset_for_backend(release_obj, backend)
        if not asset_info:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Asset Não Disponível",
            )
            dialog.format_secondary_text(f"Não encontramos um binário Linux x64 compatível com '{backend}' na release {tag_name}.")
            dialog.run()
            dialog.destroy()
            return

        asset_name, download_url, expected_sha256 = asset_info

        # Disable fields
        self.set_sensitive_inputs(False)
        self.status_lbl.set_text("Iniciando download...")
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.show()
        
        # Change tray status
        self.app.set_updating_state(True)

        # Launch download thread
        self.download_thread = updater.DownloadThread(
            tag_name=tag_name,
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
        # Update active version
        self.app.config.set("current_version", tag_name)
        
        self.app.show_notification(
            "Download Concluído",
            f"Versão {tag_name} do llama.cpp foi instalada com sucesso!"
        )
        
        # Clean state
        self.app.set_updating_state(False)
        
        # Restart server if it was running
        if self.app.process_manager.is_running():
            self.app.restart_server()
            
        self.destroy()

    def on_download_error(self, err_msg):
        self.set_sensitive_inputs(True)
        self.progress_bar.hide()
        self.status_lbl.set_text("Falha na instalação.")
        self.app.set_updating_state(False)

        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Erro na Instalação",
        )
        dialog.format_secondary_text(f"Ocorreu um erro ao instalar o llama.cpp:\n\n{err_msg}")
        dialog.run()
        dialog.destroy()

    def on_destroy(self, widget):
        # Stop download thread if running
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.stop()
        self.app.set_updating_state(False)
        self.app.settings_window = None


# Main Tray Application
class LlamaTrayApp:
    def __init__(self):
        # Init Notifications
        Notify.init("llama.tray")

        # Initialize configurations & manager
        self.config = LlamaConfig()
        self.process_manager = LlamaProcessManager(self.config)

        # Ensure directory icons exist
        ensure_icons()

        # Window trackers
        self.settings_window = None
        self.logs_window = None
        self.is_updating = False

        # Initialize AppIndicator
        self.indicator = AyatanaAppIndicator3.Indicator.new_with_path(
            "llama.tray",
            "llama_stopped",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            ICON_DIR
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        # Setup initial menu
        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)
        self.update_menu()

        # Setup exit signal handler
        signal.signal(signal.SIGINT, self.quit)
        signal.signal(signal.SIGTERM, self.quit)


    def show_notification(self, title, message, icon_type="info"):
        icon_name = "llama_running" if icon_type == "success" else "llama_stopped"
        icon_path = os.path.join(ICON_DIR, f"{icon_name}.png")
        try:
            notification = Notify.Notification.new(title, message, icon_path)
            notification.show()
        except Exception as e:
            print(f"Error showing notification: {e}", file=sys.stderr)

    def update_menu(self):
        # Clear menu
        for child in self.menu.get_children():
            self.menu.remove(child)

        # 1. Start/Stop Item
        if self.is_updating:
            start_stop_item = Gtk.MenuItem(label="Atualizando binários...")
            start_stop_item.set_sensitive(False)
        else:
            if self.process_manager.is_running():
                start_stop_item = Gtk.MenuItem(label="Parar")
                start_stop_item.connect("activate", lambda w: self.stop_server())
            else:
                start_stop_item = Gtk.MenuItem(label="Iniciar")
                start_stop_item.connect("activate", lambda w: self.start_server())
        self.menu.append(start_stop_item)

        # Divider
        self.menu.append(Gtk.SeparatorMenuItem())

        # 2. Check for Updates
        update_item = Gtk.MenuItem(label="Verificar atualizações")
        update_item.connect("activate", self.check_updates_from_menu)
        update_item.set_sensitive(not self.is_updating)
        self.menu.append(update_item)

        # 3. Settings Item
        settings_item = Gtk.MenuItem(label="Configurações")
        settings_item.connect("activate", self.open_settings)
        settings_item.set_sensitive(not self.is_updating)
        self.menu.append(settings_item)

        # 4. Logs Item
        logs_item = Gtk.MenuItem(label="Logs")
        logs_item.connect("activate", self.open_logs)
        self.menu.append(logs_item)

        # Divider
        self.menu.append(Gtk.SeparatorMenuItem())

        # 5. Exit Item
        exit_item = Gtk.MenuItem(label="Sair")
        exit_item.connect("activate", lambda w: self.quit())
        self.menu.append(exit_item)

        self.menu.show_all()

    def set_updating_state(self, updating):
        self.is_updating = updating
        if updating:
            self.indicator.set_icon_full("llama_updating", "Atualizando llama.cpp")
        else:
            self.update_icon()
        self.update_menu()

    def update_icon(self):
        if self.process_manager.is_running():
            self.indicator.set_icon_full("llama_running", "Servidor rodando")
        else:
            self.indicator.set_icon_full("llama_stopped", "Servidor parado")

    def start_server(self):
        success, msg = self.process_manager.start()
        if success:
            self.show_notification("Llama Server", "Servidor iniciado com sucesso!", "success")
            self.update_icon()
            self.update_menu()
        else:
            self.show_notification("Erro ao Iniciar", msg, "error")
            
            # Show standard GTK dialog to inform user
            dialog = Gtk.MessageDialog(
                transient_for=None,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Erro ao Iniciar llama.cpp",
            )
            dialog.format_secondary_text(msg)
            dialog.run()
            dialog.destroy()

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
        # Brief delay to let the socket release
        GLib.timeout_add(1000, self.start_server)

    def check_updates_from_menu(self, widget):
        # Notify user check is starting
        self.show_notification("Llama.tray", "Checando por atualizações...", "info")
        
        def run_check():
            try:
                releases = updater.get_releases(force_check=True)
                if not releases:
                    GLib.idle_add(self.show_notification, "Llama.tray", "Nenhuma release encontrada no GitHub.", "info")
                    return
                    
                latest_tag = releases[0].get("tag_name", "")
                current_active = self.config.get_data().get("current_version", "")
                
                if latest_tag == current_active:
                    GLib.idle_add(self.show_notification, "Llama.tray", f"Você já está na versão mais recente ({latest_tag})!", "info")
                else:
                    GLib.idle_add(self.notify_new_version, latest_tag)
            except Exception as e:
                GLib.idle_add(self.show_notification, "Erro ao Checar", str(e), "error")

        threading.Thread(target=run_check, daemon=True).start()

    def notify_new_version(self, latest_tag):
        self.show_notification(
            "Nova Versão Disponível!",
            f"A versão {latest_tag} está disponível. Abra as Configurações para atualizar.",
            "info"
        )
        # Ask user if they want to open Settings
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Nova Versão Disponível",
        )
        dialog.format_secondary_text(f"Deseja abrir as Configurações para instalar a versão {latest_tag}?")
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
            self.open_settings(None)

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
        # Shut down llama-server
        self.process_manager.stop()
        
        # Shutdown notify
        try:
            Notify.uninit()
        except Exception:
            pass
            
        Gtk.main_quit()
        sys.exit(0)


def main():
    # Allow python to process Ctrl+C signals normally in GTK
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, Gtk.main_quit)
    
    app = LlamaTrayApp()
    Gtk.main()


if __name__ == "__main__":
    main()
