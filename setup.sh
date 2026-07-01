#!/bin/bash

# Default user directories (XDG standard)
BIN_DIR="$HOME/.local/bin"
SHARE_DIR="$HOME/.local/share/llama-tray"
SRC_DIR="$SHARE_DIR/src"
APPS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor"
CONFIG_DIR="$HOME/.config/llama-tray"
CACHE_DIR="$HOME/.cache/llama-tray"
AUTOSTART_FILE="$HOME/.config/autostart/llama-tray.desktop"

# Function to check if ~/.local/bin is in PATH
check_path() {
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo "----------------------------------------------------------------------"
        echo "WARNING: $BIN_DIR is NOT in your PATH."
        echo "To run 'llama-tray' from the terminal, add this line to your .bashrc or .zshrc:"
        echo "export PATH=\"\$PATH:$BIN_DIR\""
        echo "----------------------------------------------------------------------"
    fi
}

# Function to remove terminal integration symlinks
remove_symlinks() {
    echo "Removing terminal integration symlinks..."
    # Find and delete any symlink in BIN_DIR that points to the llama-tray share folder
    find "$BIN_DIR" -lname "*$SHARE_DIR*" -type l -delete
}

# Uninstall function (Standard)
uninstall() {
    echo "Removing Llama Tray..."

    # 1. Remove the app wrapper
    rm -f "$BIN_DIR/llama-tray"

    # 2. Remove the source code
    rm -rf "$SRC_DIR"

    # 3. Remove Desktop entry and Autostart
    rm -f "$APPS_DIR/llama-tray.desktop"
    rm -f "$AUTOSTART_FILE"

    # 4. Remove icons
    find "$ICONS_DIR" -name "llama-tray*.svg" -type f -delete

    # 5. Remove terminal integration
    remove_symlinks

    echo "Updating system cache..."
    update-desktop-database "$APPS_DIR" 2>/dev/null
    gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null

    echo "Uninstallation complete! (Binaries and configs preserved)"
}

# Full Cleanup function
full_cleanup() {
    echo "Performing FULL CLEANUP..."
    
    # First, do standard uninstall
    uninstall

    # Then, wipe all data directories
    echo "Deleting binaries, configs and cache..."
    rm -rf "$SHARE_DIR"
    rm -rf "$CONFIG_DIR"
    rm -rf "$CACHE_DIR"

    echo "Full cleanup complete! Everything has been removed."
}

# Install function
install() {
    echo "Installing Llama Tray..."

    # 1. Create necessary directories
    mkdir -p "$BIN_DIR"
    mkdir -p "$SRC_DIR"
    mkdir -p "$APPS_DIR"
    mkdir -p "$ICONS_DIR/scalable/apps"
    mkdir -p "$ICONS_DIR/scalable/status"

    # 2. Copy Python source code
    cp src/*.py "$SRC_DIR/"

    # 3. Copy Desktop entry and Icons
    cp data/applications/llama-tray.desktop "$APPS_DIR/"
    cp data/icons/hicolor/scalable/apps/*.svg "$ICONS_DIR/scalable/apps/"
    cp data/icons/hicolor/scalable/status/*.svg "$ICONS_DIR/scalable/status/"

    # 4. Create the executable wrapper in ~/.local/bin
    cat << EOF > "$BIN_DIR/llama-tray"
#!/usr/bin/env bash
# Wrapper script to start Llama Tray
exec python3 "$SRC_DIR/main.py" "\$@"
EOF

    # 5. Set execution permissions
    chmod +x "$BIN_DIR/llama-tray"
    chmod +x "$SRC_DIR/main.py"

    # 6. Update desktop database and icon cache
    echo "Updating system cache..."
    update-desktop-database "$APPS_DIR" 2>/dev/null
    gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null

    echo ""
    echo "Installation completed successfully!"
    check_path
    echo "You can launch the app from your system menu or by running 'llama-tray' in the terminal."
}

# ==========================================
# MAIN MENU
# ==========================================

echo "========================================"
echo "      LLAMA.TRAY SETUP MANAGER          "
echo "========================================"
echo "1) Install"
echo "2) Uninstall"
echo "3) Full Cleanup (Uninstall + Remove all data)"
echo "4) Exit"
echo "----------------------------------------"
read -p "Choose an option [1-4]: " choice

case $choice in
    1)
        install
        ;;
    2)
        uninstall
        ;;
    3)
        full_cleanup
        ;;
    4)
        echo "Exiting..."
        exit 0
        ;;
    *)
        echo "Invalid option. Please run the script again."
        exit 1
        ;;
esac
