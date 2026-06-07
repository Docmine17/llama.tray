class LlamaTray < Formula
  desc "Tray manager for llama-server (llama.cpp)"
  homepage "https://github.com/Docmine17/llama.tray"
  
  # Substitua pela URL do seu repositório ou de um arquivo tar.gz de uma Release no GitHub
  url "https://github.com/Docmine17/llama.tray/archive/refs/tags/v1.0.0.tar.gz"
  # Para gerar o hash de um arquivo baixado, rode no terminal: shasum -a 256 arquivo.tar.gz
  sha256 "INSIRA_O_HASH_SHA256_AQUI"
  
  license "MIT" # Substitua se for outra licença

  # Dependências que o Homebrew vai garantir que estão instaladas
  depends_on "python@3.11"
  depends_on "gtk+3"
  depends_on "pygobject3" # Essencial para os bindings do GTK no Python

  def install
    # 1. O Homebrew cria um diretório isolado para o seu app: /home/linuxbrew/.linuxbrew/share/llama-tray
    # Aqui copiamos o código fonte e os assets para lá.
    pkgshare.install "src", "data"

    # 2. Cria o executável "llama-tray" que ficará disponível no terminal do usuário (/home/linuxbrew/.linuxbrew/bin)
    (bin/"llama-tray").write <<~EOS
      #!/bin/bash
      # Chama o interpretador Python do sistema apontando para o arquivo no share do Homebrew
      exec python3 "#{pkgshare}/src/main.py" "$@"
    EOS

    # 3. Instala o ícone vetorial na pasta de ícones do Homebrew
    icon_dir = share/"icons/hicolor/scalable/apps"
    icon_dir.mkpath
    icon_dir.install "data/llama-tray-icon.svg"

    # 4. Instala o arquivo .desktop no sistema de aplicações do Homebrew
    desktop_dir = share/"applications"
    desktop_dir.mkpath
    desktop_dir.install "llama-tray.desktop"
  end

  def caveats
    <<~EOS
      Llama Tray foi instalado com sucesso! 🦙

      Como o Linuxbrew instala arquivos de atalho (.desktop) fora do padrão da sua pasta pessoal,
      talvez seja necessário criar um link simbólico para que o atalho apareça no menu Iniciar/Atividades:

        mkdir -p ~/.local/share/applications
        ln -sf #{share}/applications/llama-tray.desktop ~/.local/share/applications/

      Ou certifique-se de que a variável $XDG_DATA_DIRS inclui os caminhos do Homebrew.
    EOS
  end
end