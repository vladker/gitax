"""
Журнал отправленных NuGet (.NET) пакетов.

Хранится в nuget_journal.json.
Дедупликация: (name, version) — повторная отправка той же версии блокируется.
"""

from datetime import datetime
from shared_journal import BaseJournal, RuntimeJournalMixin


class NuGetJournal(RuntimeJournalMixin, BaseJournal):
    """Журнал отправленных NuGet пакетов"""

    def _create_empty(self) -> dict:
        return {"packages": []}

    def add(self, name: str, version: str, description: str,
            downloads: int, files: list[str]) -> bool:
        if self.exists(name, version):
            return False

        self.data.setdefault("packages", []).append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
            "files": files,
        })
        self.save()
        return True

    def mark_failed(self, name: str, version: str, description: str = "",
                    downloads: int = 0):
        self.data.setdefault("packages", []).append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "failed",
            "sent_at": datetime.now().isoformat(),
            "files": [],
        })
        self.save()

    def update(self, name: str, version: str, updates: dict) -> bool:
        for entry in self.data.setdefault("packages", []):
            if entry.get("name") == name and entry.get("version") == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False

    def exists(self, name: str, version: str) -> bool:
        for entry in self.data.get("packages", []):
            if entry.get("name") == name and entry.get("version") == version:
                return True
        return False

    def exists_by_name(self, name: str) -> bool:
        for entry in self.data.get("packages", []):
            if entry.get("name") == name:
                return True
        return False

    def get(self, name: str) -> dict | None:
        latest = None
        for entry in self.data.get("packages", []):
            if entry.get("name") == name:
                if latest is None:
                    latest = entry
                elif (entry.get("sent_at") or "") > (latest.get("sent_at") or ""):
                    latest = entry
        return latest

    def get_all(self) -> list[dict]:
        return list(self.data.get("packages", []))

    def get_count(self) -> int:
        return len(self.data.get("packages", []))

    def get_stats(self) -> dict:
        entries = self.data.get("packages", [])
        sent = len([e for e in entries if e.get("status") == "sent"])
        failed = len([e for e in entries if e.get("status") == "failed"])
        return {
            "total": len(entries),
            "sent": sent,
            "failed": failed,
        }
