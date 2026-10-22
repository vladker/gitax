#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared utility functions for the archiver project."""

import getpass
import sys
import warnings


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


def prompt_secret(prompt: str = "Password: ") -> str:
    """Read a secret from the terminal without echoing it.

    Uses getpass so the value never reaches stdout and therefore never lands in
    the captured session log (see logging_config.SessionCapture) or in logs.
    Falls back to plain input() when stdin is not an interactive console.

    Returns the stripped value, or "" if the user cancelled.
    """
    text = f"{prompt.rstrip()} "
    if not sys.stdin or not sys.stdin.isatty():
        try:
            return input(text).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            value = getpass.getpass(text)
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:
        return input(text).strip()
    return value.strip()


def format_file_size(size_bytes: int) -> str:
    """Форматировать размер файла в человекочитаемый вид."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
