#!/usr/bin/env python3
"""Entry-point shim — symlink this to ~/.local/bin/nanoleaf-cli on the Pi.

    ln -s $HOME/nanoleafControlPanel/nanoleaf_cli.py $HOME/.local/bin/nanoleaf-cli
    chmod +x $HOME/.local/bin/nanoleaf-cli
"""
from nanoleaf_cli import main
main()
