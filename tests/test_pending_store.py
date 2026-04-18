"""Tests for the pending-analysis storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor.pending_store import PendingAnalysis, PendingStore


async def test_upsert_and_load_roundtrips(hass: HomeAssistant) -> None:
    """Upserting an entry makes it visible on load."""
    store = PendingStore(hass)

    with (
        patch.object(store._store, "async_load", new=AsyncMock(return_value=None)),
        patch.object(store._store, "async_save", new=AsyncMock()) as save,
    ):
        entry = store.make_entry(
            upgrade_type="Home Assistant Core",
            component_name="Home Assistant",
            entity_id="update.home_assistant_core_update",
            from_version="2026.4.2",
            target_version="2026.4.3",
            check_tasks=[{"check": "entity_count", "title": "t", "integration": "esphome"}],
            pre_results=[{"check_id": "entity_count", "title": "t", "passed": True, "detail": "ok"}],
        )
        await store.async_upsert(entry)

    save.assert_awaited()
    loaded = await store.async_load()
    assert len(loaded) == 1
    assert loaded[0].target_version == "2026.4.3"
    assert loaded[0].check_tasks[0]["integration"] == "esphome"


async def test_upsert_replaces_same_key(hass: HomeAssistant) -> None:
    """Upserting the same (entity_id, target_version) replaces the existing entry."""
    store = PendingStore(hass)

    with (
        patch.object(store._store, "async_load", new=AsyncMock(return_value=None)),
        patch.object(store._store, "async_save", new=AsyncMock()),
    ):
        for version_suffix in ("a", "b"):
            entry = store.make_entry(
                upgrade_type="core",
                component_name="Home Assistant",
                entity_id="update.ha",
                from_version="1.0",
                target_version="2.0",
                check_tasks=[{"check": "entity_count", "title": version_suffix}],
                pre_results=[],
            )
            await store.async_upsert(entry)

    assert len(store._entries) == 1
    assert store._entries[0].check_tasks[0]["title"] == "b"


async def test_prune_stale_drops_old_entries(hass: HomeAssistant) -> None:
    """Entries older than the retention window are pruned."""
    store = PendingStore(hass)
    old_iso = (datetime.now(tz=UTC) - timedelta(days=30)).isoformat()
    fresh_iso = datetime.now(tz=UTC).isoformat()
    store._entries = [
        PendingAnalysis(
            upgrade_type="core",
            component_name="Old",
            entity_id="update.old",
            from_version="1",
            target_version="2",
            created_at=old_iso,
        ),
        PendingAnalysis(
            upgrade_type="core",
            component_name="Fresh",
            entity_id="update.fresh",
            from_version="1",
            target_version="2",
            created_at=fresh_iso,
        ),
    ]
    store._loaded = True

    with patch.object(store._store, "async_save", new=AsyncMock()) as save:
        removed = await store.async_prune_stale(retention_days=14)

    assert removed == 1
    assert len(store._entries) == 1
    assert store._entries[0].component_name == "Fresh"
    save.assert_awaited_once()


async def test_remove_by_key(hass: HomeAssistant) -> None:
    """Removing by key drops the matching entry and persists."""
    store = PendingStore(hass)
    store._entries = [
        PendingAnalysis(
            upgrade_type="core",
            component_name="A",
            entity_id="update.a",
            from_version="1",
            target_version="2",
            created_at=datetime.now(tz=UTC).isoformat(),
        )
    ]
    store._loaded = True

    with patch.object(store._store, "async_save", new=AsyncMock()) as save:
        await store.async_remove("update.a", "2")

    assert store._entries == []
    save.assert_awaited_once()


async def test_load_tolerates_bad_entries(hass: HomeAssistant) -> None:
    """Non-dict items and items with missing fields are skipped silently."""
    store = PendingStore(hass)
    raw = {
        "pending": [
            "not a dict",
            {"upgrade_type": "core"},  # missing required fields
            {
                "upgrade_type": "core",
                "component_name": "Good",
                "entity_id": "update.good",
                "from_version": "1",
                "target_version": "2",
                "created_at": datetime.now(tz=UTC).isoformat(),
            },
        ]
    }
    with patch.object(store._store, "async_load", new=AsyncMock(return_value=raw)):
        loaded = await store.async_load()

    assert len(loaded) == 1
    assert loaded[0].component_name == "Good"
