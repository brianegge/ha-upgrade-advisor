# ha-upgrade-advisor

A HACS custom integration that analyzes Home Assistant release notes against your
configuration and reports potential breaking changes, required actions, and
improvement opportunities — using your configured HA conversation/AI agent.

## Problem

Home Assistant releases frequently. Each release may contain breaking changes,
deprecations, or new features relevant to your specific setup. Today, users must
manually read release notes and cross-reference against their own integrations,
automations, and entities. This is tedious and error-prone.

## Prior Art

- **`custom-components/breaking_changes`** (archived 2022) — closest predecessor.
  Created sensors showing breaking changes filtered to installed integrations.
  Scraped HA blog posts via Cloudflare Worker. Dead since 2022. Users displayed
  results via markdown cards.

- **`ai_automation_suggester`** (709 stars, active) — best reference for AI-in-HA
  architecture. Scans entities/devices, sends to LLMs (OpenAI, Anthropic, Google,
  Ollama), outputs via persistent notifications + sensor attributes.

- **`ha-config-auditor`** (H.A.C.A) — config health scanner with dedicated sidebar
  panel (10 tabs), health score sensor, PDF/MD reports, D3.js dependency graph.
  Uses HA's native LLM API.

- **`frenck/spook`** — exposes HA Repairs API as automation-callable services.
  Relevant for programmatic repair issue creation.

- **Community demand** — actively requested in HA community forums ("What-The-Heck Check for
  Breaking Changes before installing Updates", "More Repair Suggestions").

**Gap**: No existing project combines AI analysis with upgrade-specific release note
parsing. This integration fills that gap.

## Solution

An integration that:

1. Detects when a HA or HACS component update is available
2. Fetches the release notes from GitHub (core releases + HACS component repos)
3. Gathers context about the user's installation (integrations, devices, automations, add-ons)
4. Sends both to the user's configured AI conversation agent for analysis
5. Surfaces the report via HA repair issues (primary) + persistent notification (nudge)
6. Stores the full report on a sensor entity for dashboard markdown card display

## Architecture

### Integration Domain

`upgrade_advisor`

### Config Flow

**Step 1: Select AI Agent**

- Dropdown of available conversation agents (discovered via `conversation.async_get_agent_info()`)
- User picks which AI agent to use for analysis (e.g., OpenAI, Google Generative AI, Anthropic)

**Step 2: Options (Optional)**

- `scan_on_update_available` (bool, default: true) — automatically analyze when an update is detected
- `scan_hacs_updates` (bool, default: true) — also analyze HACS component updates
- `create_repair_issues` (bool, default: true) — create HA repair entries for breaking changes
- `include_automations` (bool, default: true) — include automation configs in context
- `include_addons` (bool, default: true) — include add-on list in context

### Data Gathering

When analysis is triggered, the integration collects:

| Data | Source | Purpose |
|------|--------|---------|
| HA release notes | GitHub API (`home-assistant/core` releases) | What changed in core |
| HACS component changelogs | GitHub API (per-repo releases) | What changed in HACS components |
| Installed integrations | `hass.config_entries.async_entries()` | What's affected |
| Device/entity summary | Device registry + entity registry | What hardware is in use (see below) |
| Automations | `automation` domain entities + config | What depends on what |
| Add-ons | Supervisor API (if available) | Add-on compatibility |
| HA version (current + target) | `update` entity attributes | Version context |
| HACS component versions | HACS `update` entities | Current vs available versions |

#### Entity Summarization Strategy

Sending every entity would blow up the AI context window and add noise. Instead,
summarize at the **device level**:

1. **Group entities by device** via the device registry
2. **Deduplicate by integration + device model** — 100 Hue bulbs are represented
   as "Philips Hue: 100x Extended Color Light" rather than listing each one
3. **Include one representative entity per device model** with its domain breakdown
   (e.g., "light, sensor(brightness), sensor(color_temp)")
4. **Orphan entities** (no device) are grouped by integration + domain with counts

This produces a compact summary like:

```
## Devices by Integration

### hue (23 devices)
- Extended Color Light (18x): light, sensor x3
- Hue Motion Sensor (3x): binary_sensor, sensor x2
- Hue Bridge (1x): binary_sensor
- Hue Smart Plug (1x): light, switch

### zwave_js (12 devices)
- Zooz ZEN77 Dimmer (6x): light, sensor x2, button x4
- Aeotec MultiSensor 7 (4x): binary_sensor x2, sensor x5
- ...

### Entities without devices
- automation: 47 entities
- script: 12 entities
- input_boolean: 8 entities
```

### AI Prompt Construction

The integration builds a structured prompt. For HA core upgrades and HACS component
upgrades, the same template is used with different release note sources.

```
You are a Home Assistant upgrade advisor. Analyze the following release notes
against this user's installation and produce a report.

## Upgrade
{upgrade_type}: {component_name} {current_version} → {target_version}

## Release Notes
{release_notes_markdown}

## Installed Integrations
{integration_list_with_versions}

## Devices by Integration
{device_summary}

## Automations ({count})
{automation_summaries}

## Add-ons
{addon_list}

## HACS Components
{hacs_component_list_with_versions}

## Instructions
Produce a report with these sections:
1. **Breaking Changes** — changes that WILL affect this installation, with specific
   entity/device/automation references where possible
2. **Prerequisites** — things that must be done BEFORE upgrading (especially for
   HACS components that document prerequisites in their release notes)
3. **Deprecations** — things that still work but should be migrated
4. **New Features** — relevant new capabilities for installed integrations
5. **Recommended Actions** — ordered checklist of what to do before/after upgrading
6. **Risk Assessment** — Low/Medium/High with brief justification

For each breaking change, include:
- Which integration/component is affected
- What specifically breaks
- What action the user must take
- Whether it must be done before or after the upgrade
```

### AI Communication

Uses the HA conversation API to send the prompt to the selected agent:

```python
from homeassistant.components.conversation import async_converse

response = await async_converse(
    hass=hass,
    text=prompt,
    conversation_id=None,
    agent_id=config_entry.data["agent_id"],
)
```

### Entities

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.upgrade_advisor_status` | Sensor (enum) | `idle` / `analyzing` / `report_ready` / `error` |
| `sensor.upgrade_advisor_risk` | Sensor (enum) | `unknown` / `low` / `medium` / `high` — last assessed risk |
| `event.upgrade_advisor_report` | Event | Fires when a new report is generated, with full report in event data |

**Sensor attributes (status):**

- `current_version` — installed HA version
- `available_version` — target version (if update available)
- `last_analysis` — ISO timestamp of last report
- `breaking_change_count` — number of breaking changes found
- `report` — full AI-generated report text

### Actions (Services)

| Action | Description |
|--------|-------------|
| `upgrade_advisor.analyze` | Manually trigger analysis for the available update |
| `upgrade_advisor.analyze_version` | Analyze a specific version (for pre-planning) |

### Triggers

The integration listens for state changes on `update` entities:

- **HA Core**: `update.home_assistant_core_update` — triggers when a new HA version
  is available
- **HACS components**: `update.*` entities created by HACS — triggers when any
  HACS component has an update available

When an update entity transitions to `on` and `scan_on_update_available` is enabled,
it automatically triggers analysis. HACS component analyses fetch release notes from
the component's GitHub repo (derived from HACS metadata).

### Output & Notifications

After analysis completes, results are surfaced through three channels:

1. **Repair issues** (primary) — one issue per breaking change, using
   `ir.async_create_issue()` with appropriate severity (WARNING/ERROR/CRITICAL).
   This is where HA core itself surfaces upgrade warnings, so users already look
   here. Each issue includes the affected integration, what breaks, and what to do.
   Enabled by default.

2. **Persistent notification** (nudge) — a single summary notification pointing
   users to Settings > Repairs and/or the dashboard card. Includes risk level
   and breaking change count.

3. **Sensor entity** (report storage) — the full AI-generated report is stored
   in the status sensor's `report` attribute, formatted as markdown. Users can
   display this on a dashboard using a standard Markdown card with a template:
   ```yaml
   type: markdown
   content: "{{ state_attr('sensor.upgrade_advisor_status', 'report') }}"
   ```
   This follows the same pattern as `ai_automation_suggester` and the archived
   `breaking_changes` component.

4. **Event fired** (`upgrade_advisor_report`) with full report data for automation
   use (e.g., forward to email, Slack, or mobile notification)

## File Structure

```
ha-upgrade-advisor/
  custom_components/upgrade_advisor/
    __init__.py          # Setup, coordinator, data gathering
    config_flow.py       # AI agent selection + options flow
    const.py             # Domain, defaults, prompt template
    sensor.py            # Status + risk sensors
    event.py             # Report event entity
    services.py          # analyze + analyze_version actions
    analyzer.py          # Prompt construction + AI interaction + response parsing
    manifest.json
    strings.json
    icons.json
    github.py            # GitHub API client (HA core + HACS repo release notes)
    summarize.py         # Device/entity summarization logic
    services.yaml        # Service schema definitions
  tests/
    __init__.py
    conftest.py
    test_init.py
    test_config_flow.py
    test_sensor.py
    test_analyzer.py
    test_services.py
    test_summarize.py
  hacs.json
  README.md
  AGENTS.md
  pyproject.toml
  pytest.ini
  requirements.test.txt
  .github/
    workflows/
      tests.yml
      release.yml
      validate.yml
      hassfest.yml
      lint.yml
```

## Sequence Diagram

```
Update Available          Integration              GitHub API         AI Agent
      |                       |                        |                  |
      |-- state change ------>|                        |                  |
      |                       |-- GET release notes -->|                  |
      |                       |<-- markdown -----------|                  |
      |                       |                        |                  |
      |                       |-- gather local config  |                  |
      |                       |   (integrations,       |                  |
      |                       |    entities,           |                  |
      |                       |    automations)        |                  |
      |                       |                        |                  |
      |                       |-- build prompt --------|----------------->|
      |                       |<-- analysis report ----|------------------|
      |                       |                        |                  |
      |                       |-- persistent_notification               |
      |                       |-- fire event                            |
      |                       |-- create repairs (optional)             |
```

## Key Design Decisions

1. **Uses HA conversation API, not direct LLM calls** — works with any AI agent
   the user has configured (OpenAI, Google, Anthropic, local Ollama, etc.)

2. **Does NOT perform the upgrade** — advisory only. The user decides whether
   and when to upgrade. This keeps the blast radius minimal.

3. **GitHub API for release notes** — no authentication required for public repos,
   rate limit is 60 req/hr unauthenticated (more than enough)

4. **Device-level summarization** — instead of listing every entity, group by
   integration and device model with counts. 100 identical Hue bulbs become one
   line: "Extended Color Light (100x): light, sensor x3". This keeps the prompt
   compact while preserving all integration/device-type information the AI needs.

5. **Event entity for report data** — allows users to build automations that
   forward the report (e.g., to email, Slack, or a dashboard markdown card)

## Dependencies

- `homeassistant >= 2024.7.0` (conversation API with agent selection)
- No external Python packages required (uses HA built-ins + `aiohttp` for GitHub)

## Resolved Decisions

1. **HACS component updates** — Yes. HACS components often have prerequisites in
   their release notes. The integration watches all HACS `update` entities and
   fetches release notes from each component's GitHub repo.

2. **Dashboard display** — No custom card or sidebar panel. Following the pattern
   established by `ai_automation_suggester` and `breaking_changes`: store the full
   report as a sensor attribute and let users display it via a standard Markdown
   card with `state_attr()` template. Repair issues handle the "you need to act"
   channel. This avoids frontend JS complexity and works with any dashboard.

3. **Entity summarization** — Summarize at the device level. Group by integration
   and device model, deduplicate identical devices (100 Hue bulbs = one line with
   count). Include one representative entity breakdown per device model. Orphan
   entities grouped by integration + domain with counts.

## Open Questions

- Should we rate-limit HACS analyses to avoid hammering the AI agent when multiple
  HACS components update at once? (e.g., batch into a single analysis after a
  5-minute debounce window)
- Should the report include links to the specific GitHub PRs/issues mentioned in
  release notes for breaking changes?
