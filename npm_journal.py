"""
NPM package journal for tracking uploaded packages.

Extends BaseJournal with NPM-specific methods for tracking
package uploads to MAX channels.
"""

from datetime import datetime
from shared_journal import BaseJournal


class NpmJournal(BaseJournal):
    """Journal for tracking NPM package uploads to MAX."""

    def _create_empty(self) -> dict:
        """Create empty journal structure."""
        return {"packages": []}

    def add(
        self,
        name: str,
        version: str,
        description: str = "",
        downloads: int = 0,
        files: list[str] | None = None,
    ) -> bool:
        """
        Add a package entry to the journal.

        Args:
            name: Package name
            version: Package version
            description: Package description
            downloads: Download count
            files: List of uploaded file paths

        Returns:
            True if added, False if duplicate
        """
        if self.exists(name, version):
            self.logger.debug(f"Duplicate: {name}@{version}")
            return False

        entry = {
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "files": files or [],
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
        }

        self.data["packages"].append(entry)
        self.save()
        self.logger.info(f"Added {name}@{version} to journal")
        return True

    def exists(self, name: str, version: str) -> bool:
        """Check if a package version exists in the journal."""
        for entry in self.data["packages"]:
            if entry["name"] == name and entry["version"] == version:
                return True
        return False

    def get(self, name: str) -> dict | None:
        """Get the latest entry for a package by name. Returns None if not found."""
        latest = None
        for entry in self.data["packages"]:
            if entry["name"] == name:
                if latest is None or entry["version"] > latest["version"]:
                    latest = entry
        return latest

    def get_all(self) -> list[dict]:
        """Return all package entries."""
        return self.data["packages"]

    def get_count(self) -> int:
        """Return total number of entries."""
        return len(self.data["packages"])

    def get_stats(self) -> dict:
        """Return journal statistics."""
        packages = self.data["packages"]
        return {
            "total": len(packages),
            "sent": sum(1 for p in packages if p.get("status") == "sent"),
            "failed": sum(1 for p in packages if p.get("status") == "failed"),
        }

    def update(self, name: str, version: str, updates: dict) -> bool:
        """
        Update an existing entry.

        Args:
            name: Package name
            version: Package version
            updates: Dict of fields to update

        Returns:
            True if updated, False if not found
        """
        for entry in self.data["packages"]:
            if entry["name"] == name and entry["version"] == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False

    def mark_failed(self, name: str, version: str, error: str = "") -> None:
        """Mark a package as failed."""
        self.data["packages"].append({
            "name": name,
            "version": version,
            "description": "",
            "downloads": 0,
            "files": [],
            "status": "failed",
            "error": error,
            "failed_at": datetime.now().isoformat(),
        })
        self.save()
