#!/usr/bin/env python3
"""Scroll registry — stores message sequence from channel scan for two-pass deletion."""

import json
import os
from datetime import datetime

DATA_FILE = "scroll_registry.json"


class ScrollRegistry:
    def __init__(self):
        self.messages: list[dict] = []
        self.created_at: str = ""
        self.total: int = 0

    def save(self, path: str = DATA_FILE):
        data = {
            "created_at": self.created_at,
            "total": len(self.messages),
            "messages": self.messages,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str = DATA_FILE) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.messages = data.get("messages", [])
            self.created_at = data.get("created_at", "")
            self.total = data.get("total", 0)
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    def from_messages(self, messages: list[dict]):
        self.messages = messages
        self.created_at = datetime.now().isoformat()
        self.total = len(messages)
        self.save()

    def find_target_texts(self, incomplete_items: list[dict]) -> set[str]:
        texts: set[str] = set()
        for item in incomplete_items:
            text_idx = item.get("text_idx")
            if text_idx is not None and 0 <= text_idx < len(self.messages):
                t = self.messages[text_idx].get("text", "").strip()
                if t:
                    texts.add(t)
            for fidx in item.get("file_idxs", []):
                if 0 <= fidx < len(self.messages):
                    t = self.messages[fidx].get("text", "").strip()
                    if t:
                        texts.add(t)
        return texts
