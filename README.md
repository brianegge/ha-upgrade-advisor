# Upgrade Advisor

[![HACS Validate](https://github.com/brianegge/ha-upgrade-advisor/actions/workflows/validate.yml/badge.svg)](https://github.com/brianegge/ha-upgrade-advisor/actions/workflows/validate.yml)
[![Tests](https://github.com/brianegge/ha-upgrade-advisor/actions/workflows/tests.yml/badge.svg)](https://github.com/brianegge/ha-upgrade-advisor/actions/workflows/tests.yml)

AI-powered upgrade analysis for Home Assistant. Analyzes release notes against your specific configuration and reports breaking changes, deprecations, new features, and risk level — using your configured AI conversation agent.

## Features

- Automatically analyzes available HA core updates
- Analyzes HACS component updates (with prerequisite detection)
- Uses any HA conversation agent (OpenAI, Google, Anthropic, Ollama, etc.)
- Creates repair issues for breaking changes
- Provides risk assessment (Low/Medium/High)
- Device-level summarization keeps analysis focused and accurate

## Requirements

- Home Assistant 2024.7.0 or newer
- A configured AI conversation agent (e.g., OpenAI Conversation, Google Generative AI)

## Installation

### HACS

1. Open HACS in your Home Assistant instance
2. Click the three dots menu and select "Custom repositories"
3. Add `https://github.com/brianegge/ha-upgrade-advisor` as an Integration
4. Click "Download" on the Upgrade Advisor card
5. Restart Home Assistant

### Manual

1. Copy `custom_components/upgrade_advisor/` to your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "Upgrade Advisor"
3. Select your AI conversation agent from the dropdown
4. Configure options (all optional):
   - **Analyze automatically when updates are available** (default: on)
   - **Analyze HACS component updates** (default: on)
   - **Create repair issues for breaking changes** (default: on)
   - **Include automations in analysis context** (default: on)
   - **Include add-ons in analysis context** (default: on)

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| Status | Sensor | `idle` / `analyzing` / `report_ready` / `error` |
| Risk level | Sensor | `unknown` / `low` / `medium` / `high` |
| Report | Event | Fires when a new report is generated |

### Status Sensor Attributes

- `current_version` — installed HA version
- `available_version` — target version
- `last_analysis` — timestamp of last report
- `breaking_change_count` — number of breaking changes found
- `report` — full AI-generated report (markdown)

## Services

| Service | Description |
|---------|-------------|
| `upgrade_advisor.analyze` | Manually trigger analysis for the available update |
| `upgrade_advisor.analyze_version` | Analyze a specific version's release notes |

## Dashboard Card

Display the full report on your dashboard with a Markdown card:

```yaml
type: markdown
content: >-
  {{ state_attr('sensor.upgrade_advisor_status', 'report') }}
```

## How It Works

1. When an update is available (HA core or HACS component), the integration fetches release notes from GitHub
2. It gathers your installation context: integrations, devices (summarized by model), automations, and add-ons
3. A structured prompt is sent to your configured AI conversation agent
4. The AI analyzes the release notes against your setup and produces a report with:
   - Breaking changes that affect your installation
   - Prerequisites (things to do before upgrading)
   - Deprecations to plan for
   - New features relevant to your integrations
   - Risk assessment with justification
5. Results are surfaced via repair issues, persistent notifications, and sensor attributes

## Automation Examples

### Send report to phone when ready

```yaml
automation:
  - alias: "Notify on upgrade report"
    trigger:
      - platform: state
        entity_id: sensor.upgrade_advisor_status
        to: "report_ready"
    action:
      - service: notify.mobile_app
        data:
          title: "Upgrade Advisor Report"
          message: >-
            Risk: {{ states('sensor.upgrade_advisor_risk_level') }}
            Breaking changes: {{ state_attr('sensor.upgrade_advisor_status', 'breaking_change_count') }}
```

### Analyze a specific version

```yaml
service: upgrade_advisor.analyze_version
data:
  version: "2024.12.0"
```

## Known Limitations

- Advisory only — does not perform upgrades
- AI analysis quality depends on the conversation agent used
- GitHub API rate limit: 60 requests/hour unauthenticated
- Very large installations may produce long prompts; entities are summarized at device level to mitigate this
- HACS component detection relies on `release_url` attribute containing a GitHub URL
