"""Device/entity summarization for compact AI prompts."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


@dataclass
class DeviceModel:
    """A unique device type within an integration."""

    model: str
    count: int = 0
    entity_domains: Counter[str] = field(default_factory=Counter)


def async_summarize_devices(hass: HomeAssistant) -> str:
    """Build a compact device-level summary grouped by integration.

    Groups entities by device, deduplicates by integration + model,
    and produces a text summary suitable for an AI prompt.
    """
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    # Map device_id → device entry
    devices = {d.id: d for d in device_reg.devices.values()}

    # Group entities by device, then by integration+model
    integration_models: dict[str, dict[str, DeviceModel]] = defaultdict(dict)
    orphan_domains: dict[str, Counter[str]] = defaultdict(Counter)

    for entity in entity_reg.entities.values():
        if entity.disabled:
            continue

        domain = entity.domain

        if entity.device_id and entity.device_id in devices:
            device = devices[entity.device_id]
            integration = entity.platform
            model_name = _get_model_name(device)

            if model_name not in integration_models[integration]:
                integration_models[integration][model_name] = DeviceModel(model=model_name)

            dm = integration_models[integration][model_name]
            dm.entity_domains[domain] += 1
        else:
            # Entity without a device
            integration = entity.platform
            orphan_domains[integration][domain] += 1

    # Count unique devices per integration+model
    device_integration_model: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for device in devices.values():
        for identifier in device.identifiers:
            integration = identifier[0] if len(identifier) > 1 else "unknown"
            model_name = _get_model_name(device)
            device_integration_model[integration][model_name].add(device.id)
            break
        if device.config_entries:
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry:
                    integration = entry.domain
                    model_name = _get_model_name(device)
                    device_integration_model[integration][model_name].add(device.id)
                    break

    # Build the text summary
    lines: list[str] = []

    # Sort integrations alphabetically
    all_integrations = sorted(set(list(integration_models.keys()) + list(device_integration_model.keys())))

    for integration in all_integrations:
        models = device_integration_model.get(integration, {})
        total_devices = sum(len(ids) for ids in models.values())
        if total_devices == 0 and integration not in integration_models:
            continue

        lines.append(f"\n### {integration} ({total_devices} device{'s' if total_devices != 1 else ''})")

        for model_name in sorted(models.keys()):
            count = len(models[model_name])
            entity_info = ""
            if integration in integration_models and model_name in integration_models[integration]:
                dm = integration_models[integration][model_name]
                entity_info = _format_entity_domains(dm.entity_domains)
            lines.append(f"- {model_name} ({count}x): {entity_info}" if entity_info else f"- {model_name} ({count}x)")

    # Orphan entities
    if orphan_domains:
        lines.append("\n### Entities without devices")
        for integration in sorted(orphan_domains.keys()):
            domains = orphan_domains[integration]
            parts = [f"{domain}: {count}" for domain, count in sorted(domains.items())]
            lines.append(f"- {integration}: {', '.join(parts)}")

    return "\n".join(lines)


def _get_model_name(device: dr.DeviceEntry) -> str:
    """Get a human-readable model name for a device."""
    parts: list[str] = []
    if device.manufacturer:
        parts.append(device.manufacturer)
    if device.model:
        parts.append(device.model)
    if not parts:
        return device.name or "Unknown device"
    return " ".join(parts)


def _format_entity_domains(domains: Counter[str]) -> str:
    """Format entity domain counts compactly."""
    parts: list[str] = []
    for domain, count in sorted(domains.items()):
        if count == 1:
            parts.append(domain)
        else:
            parts.append(f"{domain} x{count}")
    return ", ".join(parts)


def async_get_automation_summaries(hass: HomeAssistant) -> str:
    """Get a compact summary of automations."""
    automations = hass.states.async_all("automation")
    if not automations:
        return "No automations configured."

    lines: list[str] = []
    for state in automations:
        friendly_name = state.attributes.get("friendly_name", state.entity_id)
        current_state = state.state
        lines.append(f"- {friendly_name} ({current_state})")

    return "\n".join(lines)


def async_get_integration_list(hass: HomeAssistant) -> str:
    """Get a list of installed integrations."""
    entries = hass.config_entries.async_entries()
    integrations: dict[str, list[str]] = defaultdict(list)

    for entry in entries:
        integrations[entry.domain].append(entry.title)

    lines: list[str] = []
    for domain in sorted(integrations.keys()):
        titles = integrations[domain]
        if len(titles) == 1:
            lines.append(f"- {domain}: {titles[0]}")
        else:
            lines.append(f"- {domain}: {', '.join(titles)} ({len(titles)} instances)")

    return "\n".join(lines)


def async_get_addon_list(hass: HomeAssistant) -> str:
    """Get a list of installed add-ons (if Supervisor is available)."""
    # Add-on data comes from the Supervisor, accessed via hass.data
    # This is a best-effort approach; not all installations have Supervisor
    if "hassio" not in hass.data:
        return "Add-ons not available (not running Home Assistant OS/Supervised)."

    # The supervisor stores add-on info in hass.data["hassio"]
    # We'll access it through the REST API in the coordinator
    return ""


def build_installation_context(hass: HomeAssistant, include_automations: bool, include_addons: bool) -> dict[str, Any]:
    """Build the full installation context for the AI prompt."""
    context: dict[str, Any] = {
        "integrations": async_get_integration_list(hass),
        "devices": async_summarize_devices(hass),
    }

    if include_automations:
        context["automations"] = async_get_automation_summaries(hass)
    else:
        context["automations"] = "Automations excluded from analysis."

    if include_addons:
        context["addons"] = async_get_addon_list(hass)
    else:
        context["addons"] = "Add-ons excluded from analysis."

    return context
