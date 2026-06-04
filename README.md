# Llama.tray

`llama.tray` é uma aplicação leve para a bandeja do sistema (system tray) no Linux, construída em Python 3 e GTK 3, projetada para gerenciar, configurar, monitorar e atualizar instâncias do `llama-server` (`llama.cpp`).

## Funcionalidades

- **Controle Direto da Tray**: Iniciar e parar o servidor do llama.cpp com um clique. O ícone da bandeja muda de cor dinamicamente (Verde: rodando, Cinza: parado, Azul: atualizando/baixando).
- **Gerenciador de Atualizações Integrado**:
  - Consulta as releases oficiais no repositório `ggml-org/llama.cpp`.
  - Exibe um dropdown ordenado com as versões disponíveis para download ou já instaladas localmente (cache local).
  - Faz o download automático e valida a integridade do pacote usando hashes **SHA256** oficiais fornecidos na própria API de releases do GitHub.
  - Extrai e configura o executável automaticamente.
- **Painel de Configurações**:
  - Seleção de backend de aceleração (Vulkan para GPU ou CPU padrão).
  - Definição de variáveis de ambiente personalizadas.
  - Customização de argumentos para o processo (especificação de porta, caminhos de modelo, etc.).
- **Monitor de Logs em Tempo Real**:
  - Visualizador de logs com auto-scroll integrado ao GTK 3.
  - Rotação automática de logs (limite de 10 MB por arquivo para poupar disco).
- **Segurança e Robustez**:
  - Prevenção de processos órfãos (zombies) usando `prctl` (sinal de término automático do processo filho caso o processo principal Python morra).
  - Prevenção de injeção de comandos (execução via `shell=False` com argumentos divididos de forma segura via `shlex.split`).
  - Verificação de porta ocupada antes de iniciar o processo para evitar falhas silenciosas.
  - Cache local das releases do GitHub por 1 hora para evitar rate limiting.

## Estrutura de Diretórios Utilizados

- **Instalação dos binários**: `~/.local/share/llama.tray/bin/<version>/`
- **Arquivo de Configuração (JSON)**: `~/.config/llama.tray/config.json`
- **Log do Servidor**: `~/.local/share/llama.tray/llama.log`
- **Cache de Atualizações**: `~/.cache/llama.tray/releases_cache.json`

## Pré-requisitos (Debian/Ubuntu/Fedora)

Você precisará ter o Python 3, PyGObject, Cairo e as bibliotecas do Ayatana AppIndicator instalados em seu sistema.

### Debian / Ubuntu
```bash
sudo apt install python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 libnotify-bin
```

### Fedora
```bash
sudo dnf install python3-gobject python3-cairo libayatana-appindicator-gtk3 libnotify
```

### Arch Linux
```bash
sudo pacman -S python-gobject python-cairo libayatana-appindicator3 libnotify
```

## Como Executar

### 1. Execução Direta
Você pode rodar diretamente o script a partir do diretório raiz do projeto:

```bash
python3 src/main.py
```

### 2. Instalação em Modo Desenvolvimento (Editable)
```bash
pip install -e .
llama-tray
```
