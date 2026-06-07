# Llama.tray

`llama.tray` é uma aplicação leve para a bandeja do sistema (system tray) no Linux, construída em Python 3 e GTK 3, projetada para gerenciar, configurar, monitorar e atualizar instâncias do `llama-server` (`llama.cpp`).

## Funcionalidades

- **Controle Direto da Tray**: Iniciar e parar o servidor do llama.cpp com um clique. O ícone da bandeja muda de cor dinamicamente (Verde: rodando, Vermelho: parado, Azul: atualizando/baixando).
- **Gerenciador de Atualizações Integrado**:
  - Consulta as releases oficiais no repositório `ggml-org/llama.cpp`.
  - Exibe um dropdown ordenado com as versões disponíveis para download ou já instaladas localmente (cache local).
  - Faz o download automático e valida a integridade do pacote usando hashes **SHA256**.
  - Extrai e configura o executável automaticamente.
- **Painel de Configurações**:
  - Seleção de backend de aceleração (Vulkan para GPU ou CPU padrão).
  - Definição de variáveis de ambiente personalizadas.
  - Customização de argumentos para o processo (especificação de porta, caminhos de modelo, etc.).
- **Monitor de Logs em Tempo Real**:
  - Visualizador de logs com auto-scroll integrado ao GTK 3.
  - Rotação automática de logs (limite de 10 MB por arquivo para poupar disco).

## Estrutura de Diretórios Utilizados

- **Instalação dos binários do llama.cpp**: `~/.local/share/llama.tray/bin/<version>/`
- **Arquivo de Configuração (JSON)**: `~/.config/llama.tray/config.json`
- **Log do Servidor**: `~/.local/share/llama.tray/llama.log`
- **Cache de Atualizações**: `~/.cache/llama.tray/releases_cache.json`

## Pré-requisitos

Você precisará ter o Python 3, PyGObject e as bibliotecas do Ayatana AppIndicator instalados em seu sistema.
