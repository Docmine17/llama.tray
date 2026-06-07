#!/bin/bash

# Default user directories in Linux (XDG standard)
BIN_DIR="$HOME/.local/bin"
SHARE_DIR="$HOME/.local/share/llama-tray"
SRC_DIR="$SHARE_DIR/src"
APPS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor"

# Uninstall function
uninstall() {
    echo "Removing Llama Tray..."
    
    rm -f "$BIN_DIR/llama-tray"
    rm -rf "$SRC_DIR"
    rm -f "$APPS_DIR/llama-tray.desktop"
    
    # Remove icons matching the application name
    find "$ICONS_DIR" -name "llama-tray*.svg" -type f -delete

    # Note: We do not delete the entire ~/.local/share/llama-tray directory
    # by default to preserve the downloaded llama-server binaries and logs.

    echo "Updating system cache..."
    update-desktop-database "$APPS_DIR" 2>/dev/null
    gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null

    echo "Uninstallation complete!"
    exit 0
}

# Check if the argument is --uninstall
if [ "$1" == "--uninstall" ]; then
    uninstall
fi

# ==========================================
# Install function
# ==========================================

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
echo "Make sure '$BIN_DIR' is in your PATH."
echo "You can launch the app from your system menu or by running 'llama-tray' in the terminal."
