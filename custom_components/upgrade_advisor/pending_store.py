"""Persistent storage for pending pre-upgrade analyses.

The coordinator records the check plan produced before an upgrade so that
after the upgrade lands (typically after an HA restart) we can re-run the
same checks and report a post-upgrade diff.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import PENDING_RETENTION_DAYS, PENDING_STORAGE_KEY, PENDING_STORAGE_VERSION


@dataclass
class PendingAnalysis:
    """A pre-upgrade analysis waiting to be verified after the upgrade lands."""

    upgrade_type: str
    component_name: str
    entity_id: str
    from_version: str
    target_version: str
    created_at: str
    check_tasks: list[dict] = field(default_factory=list)
    pre_results: list[dict] = field(default_factory=list)

    def key(self) -> tuple[str, str]:
        """Return a unique key used for upsert."""
        return (self.entity_id, self.target_version)

    def age_days(self, now: datetime | None = None) -> float:
        """Return the age of this pending entry in days."""
        created = datetime.fromisoformat(self.created_at)
        reference = now or datetime.now(tz=UTC)
        return (reference - created).total_seconds() / 86400.0


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class PendingStore:
    """Thin wrapper over Store that keeps an in-memory cache of pending entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store: Store = Store(hass, PENDING_STORAGE_VERSION, PENDING_STORAGE_KEY)
        self._entries: list[PendingAnalysis] = []
        self._loaded = False

    async def async_load(self) -> list[PendingAnalysis]:
        """Load entries from disk (or return the cached list)."""
        if self._loaded:
            return list(self._entries)

        raw = await self._store.async_load()
        entries: list[PendingAnalysis] = []
        if isinstance(raw, dict):
            valid_fields = {f.name for f in fields(PendingAnalysis)}
            for item in raw.get("pending", []):
                if not isinstance(item, dict):
                    continue
                try:
                    entries.append(PendingAnalysis(**{k: v for k, v in item.items() if k in valid_fields}))
                except (TypeError, ValueError):
                    continue
        self._entries = entries
        self._loaded = True
        return list(self._entries)

    async def async_save(self) -> None:
        """Persist the current entries to disk."""
        data: dict[str, Any] = {"pending": [asdict(e) for e in self._entries]}
        await self._store.async_save(data)

    async def async_upsert(self, entry: PendingAnalysis) -> None:
        """Insert or replace a pending entry keyed by (entity_id, target_version)."""
        await self.async_load()
        key = entry.key()
        self._entries = [e for e in self._entries if e.key() != key]
        self._entries.append(entry)
        await self.async_save()

    async def async_remove(self, entity_id: str, target_version: str) -> None:
        """Remove a pending entry by key."""
        await self.async_load()
        key = (entity_id, target_version)
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.key() != key]
        if len(self._entries) != before:
            await self.async_save()

    async def async_prune_stale(self, retention_days: int = PENDING_RETENTION_DAYS) -> int:
        """Drop entries older than retention_days. Returns number removed."""
        await self.async_load()
        cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
        fresh: list[PendingAnalysis] = []
        removed = 0
        for entry in self._entries:
            try:
                created = datetime.fromisoformat(entry.created_at)
            except ValueError:
                removed += 1
                continue
            if created < cutoff:
                removed += 1
                continue
            fresh.append(entry)
        if removed:
            self._entries = fresh
            await self.async_save()
        return removed

    def make_entry(
        self,
        *,
        upgrade_type: str,
        component_name: str,
        entity_id: str,
        from_version: str,
        target_version: str,
        check_tasks: list[dict],
        pre_results: list[dict],
    ) -> PendingAnalysis:
        """Construct a PendingAnalysis with the current timestamp."""
        return PendingAnalysis(
            upgrade_type=upgrade_type,
            component_name=component_name,
            entity_id=entity_id,
            from_version=from_version,
            target_version=target_version,
            created_at=_now_iso(),
            check_tasks=check_tasks,
            pre_results=pre_results,
        )
