#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared utility functions for the archiver project."""


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


def format_file_size(size_bytes: int) -> str:
    """Форматировать размер файла в человекочитаемый вид."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
