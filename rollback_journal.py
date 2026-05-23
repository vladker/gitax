#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rollback journal.json: revert CLEANED entries that were wrongly set
to their original SENT status.
"""
import json
import os

JOURNAL_PATH = "journal.json"

if not os.path.exists(JOURNAL_PATH):
    print(f"[ERROR] {JOURNAL_PATH} not found")
    exit(1)

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

    with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] Откачено {reverted} записей: cleaned -> sent")
else:
    print("  Нет записей со статусом 'cleaned'")
