"""nanoleaf_cli — CLI tool for the Nanoleaf sunrise/sunset controller."""

import sys
from typing import Optional

from dotenv import load_dotenv

from nanoleaf_cli._args import build_parser


def main(argv=None, now=None) -> None:
    """Entry point for the nanoleaf-cli command."""
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args, now=now)
