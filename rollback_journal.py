#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rollback journal.json: revert CLEANED entries that were wrongly set
to their original SENT status.
"""
import json
import logging
import os
import shutil
import tempfile

_logger = logging.getLogger("gitax")

JOURNAL_PATH = "journal.json"


def rollback():
    """Revert CLEANED entries back to SENT status with atomic write."""
    if not os.path.exists(JOURNAL_PATH):
        _logger.error(f"{JOURNAL_PATH} not found")
        return

    with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    repos = data.get("repositories", [])
    reverted = 0

    for repo in repos:
        if repo.get("status") == "cleaned" and not repo.get("restored_at"):
            repo["status"] = "sent"
            reverted += 1

    if reverted:
        data["total_sent"] = len([r for r in repos if r.get("status") == "sent"])
        data["total_incomplete"] = len([r for r in repos if r.get("status") == "incomplete"])
        data["total_restored"] = len([r for r in repos if r.get("status") == "restored"])
        data["total_failed"] = len([r for r in repos if r.get("status") == "failed"])

        # Atomic write: temp file → backup → replace (same pattern as journal.py)
        temp_fd, temp_path = tempfile.mkstemp(
            suffix='.json',
            dir=os.path.dirname(JOURNAL_PATH) or '.'
        )

        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            backup_path = f"{JOURNAL_PATH}.bak"
            shutil.copy2(JOURNAL_PATH, backup_path)

            os.replace(temp_path, JOURNAL_PATH)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        _logger.info(f"Откачено {reverted} записей: cleaned -> sent")
    else:
        _logger.info("Нет записей со статусом 'cleaned'")


if __name__ == "__main__":
    rollback()
