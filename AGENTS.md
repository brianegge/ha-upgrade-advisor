# AGENTS.md - Upgrade Advisor Integration

## Project Overview

Custom Home Assistant integration (`upgrade_advisor`, distributed through the Marketplace, formerly HACS) that uses AI conversation agents to analyze HA and Marketplace (HACS) component release notes against the user's configuration, producing upgrade impact reports with risk assessments and breaking change identification.

## Directory Structure

```
ha-upgrade-advisor/
├── custom_components/
│   └── upgrade_advisor/
│       ├── __init__.py          # Core: UpgradeAdvisorCoordinator, setup, update listeners
│       ├── analyzer.py          # Prompt construction, AI interaction, response parsing
│       ├── config_flow.py       # Config flow: AI agent selection + options flow
│       ├── const.py             # Constants: DOMAIN, config keys, defaults
│       ├── event.py             # Event entity: fires on report generation
│       ├── github.py            # GitHub API client for release notes
│       ├── icons.json           # Per-state icons for sensors and events
│       ├── manifest.json        # Integration metadata
│       ├── sensor.py            # Sensor entities: status + risk level
│       ├── services.py          # Service actions: analyze, analyze_version
│       ├── services.yaml        # Service schema definitions
│       ├── strings.json         # UI localization: config, options, entities, services, issues
│       └── summarize.py         # Device/entity summarization for AI prompts
├── tests/
│   ├── conftest.py
│   ├── test_analyzer.py
│   ├── test_config_flow.py
│   ├── test_init.py
│   ├── test_sensor.py
│   ├── test_services.py
│   └── test_summarize.py
├── .github/workflows/
│   ├── tests.yml                # Unit tests + codecov
│   ├── release.yml              # Auto-versioning & GitHub release
│   ├── validate.yml             # HACS validation
│   ├── hassfest.yml             # HA manifest validation
│   └── lint.yml                 # Ruff linter
├── hacs.json                    # HACS repository metadata
├── SPEC.md                      # Design specification
├── README.md                    # User-facing documentation
├── AGENTS.md                    # This file
├── pyproject.toml               # Ruff/mypy configuration
├── pytest.ini                   # Pytest configuration
└── requirements.test.txt        # Test dependencies
```

## Key Classes

| Class | File | Purpose |
|-------|------|---------|
| `UpgradeAdvisorCoordinator` | `__init__.py` | Manages analysis state, orchestrates data gathering + AI interaction |
| `UpgradeAdvisorConfigFlow` | `config_flow.py` | Config flow for AI agent selection |
| `UpgradeAdvisorOptionsFlow` | `config_flow.py` | Options flow for scan preferences |
| `UpgradeAdvisorStatusSensor` | `sensor.py` | Enum sensor: idle/analyzing/report_ready/error |
| `UpgradeAdvisorRiskSensor` | `sensor.py` | Enum sensor: unknown/low/medium/high |
| `UpgradeAdvisorReportEvent` | `event.py` | Event entity that fires on report generation |
| `AnalysisResult` | `analyzer.py` | Dataclass holding analysis output |

## Architecture

### Analysis Pipeline

1. **Trigger** — update entity state change (HA core or Marketplace/HACS) or manual service call
2. **Fetch** — release notes from GitHub API (`github.py`)
3. **Gather** — installation context: integrations, devices (summarized), automations, add-ons (`summarize.py`)
4. **Prompt** — build structured prompt with release notes + context (`analyzer.py`)
5. **Analyze** — send to AI conversation agent via `conversation.async_converse` (`analyzer.py`)
6. **Parse** — extract risk level and breaking change count from response (`analyzer.py`)
7. **Output** — repair issues, persistent notification, event entity, sensor attributes (`__init__.py`)

### Entity Summarization

Entities are summarized at the device level to keep prompts compact:
- Grouped by integration + device model
- Deduplicated (100 identical Hue lights = one line with count)
- Orphan entities grouped by integration + domain

### Conversation Dependency

The `conversation` component is loaded lazily (import inside functions) to avoid test failures from `hassil` not being installed. The manifest uses `after_dependencies` instead of `dependencies`.

## Conventions

- Say "Marketplace (HACS)" in user-facing text: HA 2026.11 replaced HACS with the built-in Marketplace, but older installs still run HACS. The stored option key `scan_hacs_updates` (`CONF_SCAN_HACS`) keeps its name so existing config entries keep working.
- Read entity display names with `State.name`, not the `friendly_name` attribute (Home Assistant plans to deprecate it, announced with 2026.11).
- Follow Home Assistant custom component conventions.
- Use `hass.data[DOMAIN][entry_id]` for runtime data (coordinator, event entity).
- Use `has_entity_name = True` with `translation_key` for entity names.
- Icons defined in `icons.json`, not hardcoded.
- All user-facing strings in `strings.json`.
- Conversation/selector imports are lazy (inside functions) to avoid import-time failures.
- Config flow unique ID is `DOMAIN` (singleton — only one instance allowed).
- Services registered globally on first entry setup, unregistered on last entry unload.

## Testing

Run from repo root with Python 3.12 venv:
```bash
source .venv/bin/activate
pytest
pytest --cov=custom_components.upgrade_advisor --cov-report=term-missing
```

Test dependencies: `pip install -r requirements.test.txt` plus `hassil home-assistant-intents` for conversation component support.

## Dependencies

- No external Python packages (uses HA built-ins + aiohttp)
- Home Assistant >= 2024.7.0 (conversation API with agent selection)
