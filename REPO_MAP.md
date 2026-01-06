# Repo Map — campaign-dashboard

## Tree
```text
campaign-dashboard/
  .env
  .env.example
  .gitignore
  LICENSE
  README.md
  REPO_MAP.json
  REPO_MAP.md
  campaign-dashboard_v0.4.0.zip
  fix_port_8010_to_8000.py
  repo_map.py
  requirements-lock.txt
  requirements.lock.txt
  requirements.txt
  run_api.py
  run_bot.py
  app/
    __init__.py
    config.py
    database.py
    main.py
    api/
      __init__.py
      approvals.py
      bootstrap.py
      counties.py
      events.py
      external.py
      impact.py
      people.py
      power5.py
      teams.py
      training.py
      voters.py
    config/
      settings.py
    data/
      ar_counties.json
      Release_notes/
        v0.2.3.md
        v0.3.0-ms-baseline
    discord/
      COMMAND_RULES.md
      __init__.py
      bot.py
      help.py
      commands/
        __init__.py
        _me.py
        access.py
        approvals.py
        core.py
        external.py
        impact.py
        onboarding.py
        power5.py
        role_sync.py
        shared.py
        training.py
      config/
        __init__.py
        settings.py
    models/
      __init__.py
      alice_county.py
      approval_request.py
      county.py
      county_snapshot.py
      event.py
      impact_action.py
      impact_reach_snapshot.py
      impact_rule.py
      person.py
      power5_invite.py
      power5_link.py
      power_team.py
      training_completion.py
      training_module.py
      voter.py
    scripts/
      migrate_people_stage_fields.py
      seed_counties.py
      seed_training.py
    services/
      ai.py
      bls.py
      census.py
      impact_engine.py
      stage_engine.py
  backend/
    data/
      app.db
  data/
    .gitkeep
    campaign.sqlite
```

## Inventory Summary

- Files: **80**
- Python: **62**
- API modules: **12**
- Discord modules: **17**
- Model modules: **16**

## API Endpoints (discovered)

| Method | Path | File |
|---|---|---|
| GET | `/approvals/` | `app/api/approvals.py` |
| GET | `/approvals/pending` | `app/api/approvals.py` |
| POST | `/approvals/request` | `app/api/approvals.py` |
| POST | `/approvals/sync_roles` | `app/api/approvals.py` |
| GET | `/approvals/{approval_id}` | `app/api/approvals.py` |
| POST | `/approvals/{approval_id}/review` | `app/api/approvals.py` |
| POST | `/bootstrap/power5_team` | `app/api/bootstrap.py` |
| POST | `/bootstrap/rules` | `app/api/bootstrap.py` |
| GET | `/counties/` | `app/api/counties.py` |
| GET | `/counties/by-name/{name}` | `app/api/counties.py` |
| POST | `/counties/refresh-snapshots` | `app/api/counties.py` |
| GET | `/counties/{fips5}` | `app/api/counties.py` |
| GET | `/events/` | `app/api/events.py` |
| POST | `/events/` | `app/api/events.py` |
| GET | `/external/bls/series` | `app/api/external.py` |
| GET | `/external/census/county_population` | `app/api/external.py` |
| GET | `/external/census/county_snapshot` | `app/api/external.py` |
| POST | `/external/census/refresh_counties` | `app/api/external.py` |
| POST | `/impact/actions` | `app/api/impact.py` |
| GET | `/impact/reach/summary` | `app/api/impact.py` |
| GET | `/people/` | `app/api/people.py` |
| POST | `/people/` | `app/api/people.py` |
| GET | `/people/by_tracking/{tracking_number}` | `app/api/people.py` |
| POST | `/people/discord/upsert` | `app/api/people.py` |
| POST | `/people/onboard` | `app/api/people.py` |
| GET | `/people/{person_id}` | `app/api/people.py` |
| PATCH | `/people/{person_id}` | `app/api/people.py` |
| PUT | `/people/{person_id}` | `app/api/people.py` |
| GET | `/people/{person_id}/impact` | `app/api/people.py` |
| POST | `/power5/invites/claim` | `app/api/power5.py` |
| POST | `/power5/invites/consume` | `app/api/power5.py` |
| POST | `/power5/invites/create` | `app/api/power5.py` |
| POST | `/power5/teams/{team_id}/invites` | `app/api/power5.py` |
| POST | `/power5/teams/{team_id}/links` | `app/api/power5.py` |
| GET | `/power5/teams/{team_id}/stats` | `app/api/power5.py` |
| GET | `/power5/teams/{team_id}/tree` | `app/api/power5.py` |
| GET | `/teams/` | `app/api/teams.py` |
| POST | `/teams/` | `app/api/teams.py` |
| GET | `/teams/{team_id}` | `app/api/teams.py` |
| GET | `/teams/{team_id}/members` | `app/api/teams.py` |
| POST | `/teams/{team_id}/members` | `app/api/teams.py` |
| POST | `/training/complete` | `app/api/training.py` |
| GET | `/training/completions` | `app/api/training.py` |
| GET | `/training/modules` | `app/api/training.py` |
| GET | `/training/progress` | `app/api/training.py` |
| GET | `/voters/` | `app/api/voters.py` |
| POST | `/voters/` | `app/api/voters.py` |
| GET | `/voters/steps/all` | `app/api/voters.py` |
| GET | `/voters/{voter_id}` | `app/api/voters.py` |
| PATCH | `/voters/{voter_id}` | `app/api/voters.py` |

## Discord Commands (discovered)

| Name | Kind | File |
|---|---|---|
| `approvals_pending` | tree | `app/discord/commands/approvals.py` |
| `approve` | tree | `app/discord/commands/approvals.py` |
| `bls` | tree | `app/discord/commands/external.py` |
| `census` | tree | `app/discord/commands/external.py` |
| `config` | tree | `app/discord/commands/core.py` |
| `help` | tree | `app/discord/help.py` |
| `links` | tree | `app/discord/help.py` |
| `log` | tree | `app/discord/commands/impact.py` |
| `my_next` | tree | `app/discord/commands/impact.py` |
| `my_trainings` | tree | `app/discord/commands/training.py` |
| `p5_invite` | tree | `app/discord/commands/power5.py` |
| `p5_link` | tree | `app/discord/commands/power5.py` |
| `p5_stats` | tree | `app/discord/commands/power5.py` |
| `p5_tree` | tree | `app/discord/commands/power5.py` |
| `ping` | tree | `app/discord/commands/core.py` |
| `power_of_5` | tree | `app/discord/commands/power5.py` |
| `reach` | tree | `app/discord/commands/impact.py` |
| `request_team_access` | tree | `app/discord/commands/approvals.py` |
| `start` | tree | `app/discord/commands/onboarding.py` |
| `sync_me` | tree | `app/discord/commands/_me.py` |
| `sync_me` | tree | `app/discord/commands/access.py` |
| `sync_me` | tree | `app/discord/commands/role_sync.py` |
| `training_complete` | tree | `app/discord/commands/training.py` |
| `trainings` | tree | `app/discord/commands/training.py` |
| `whoami` | tree | `app/discord/commands/onboarding.py` |
| `wins_help` | tree | `app/discord/commands/core.py` |
| `x` | decorator | `repo_map.py` |
| `x` | tree | `repo_map.py` |
| `x` | tree | `repo_map.py` |

## SQLModel Tables (discovered)

| Table Class | File |
|---|---|
| `AliceCounty` | `app/models/alice_county.py` |
| `ApprovalRequest` | `app/models/approval_request.py` |
| `County` | `app/models/county.py` |
| `CountySnapshot` | `app/models/county_snapshot.py` |
| `Event` | `app/models/event.py` |
| `ImpactAction` | `app/models/impact_action.py` |
| `ImpactReachSnapshot` | `app/models/impact_reach_snapshot.py` |
| `ImpactRule` | `app/models/impact_rule.py` |
| `Person` | `app/models/person.py` |
| `Power5Invite` | `app/models/power5_invite.py` |
| `Power5Link` | `app/models/power5_link.py` |
| `PowerTeam` | `app/models/power_team.py` |
| `PowerTeamMember` | `app/models/power_team.py` |
| `TrainingCompletion` | `app/models/training_completion.py` |
| `TrainingModule` | `app/models/training_module.py` |
| `VoterContact` | `app/models/voter.py` |

## Settings keys referenced (scan)
```text
admin_roles_raw
app_version
bls_api_key
census_api_key
cors_allow_origins
dashboard_api_base
discord_bot_token
discord_guild_id
discord_help_url
discord_sync_guild_only
enable_role_sync
enable_training_system
enable_wins_automation
env
first_actions_channel_name
host
http_timeout_s
http_user_agent
lead_roles_raw
log_level
onboarding_url
openai_api_key
port
public_api_base
py
redacted_dict
reload
resolved_database_url
role_admin
role_fundraising
role_leader
role_team
sqlite_auto_migrate
validate
volunteer_form_url
wins_channel_name
wins_require_channel
wins_trigger_emoji
```

## Env vars referenced via os.getenv (scan)
```text
DASHBOARD_DOTENV_DISABLE
DASHBOARD_DOTENV_PATH
```
