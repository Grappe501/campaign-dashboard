# Discord Bot Build Rules (Campaign Dashboard)

This file is the **rules protocol** for adding/maintaining Discord bot features in this repo.
Goal: keep the core “sine” stable so we can safely add anything later without rewrites.

---

## 0) North Star

- **Bot is a control plane** for humans + volunteer ops.
- **Backend is the source of truth** for data and permission flags.
- Discord bot should be **best-effort** and **never crash** due to feature failures.
- Build should be **modular**, **dependency-light**, and **incremental**.

---

## 1) Architecture (Non-Negotiables)

### 1.1 Single entrypoint
- `app/discord/bot.py` is the only runtime entrypoint.
- It owns:
  - Discord intents
  - command registration
  - command sync
  - one shared `httpx.AsyncClient` stored on `bot.api`
  - event listeners (wins automation)

### 1.2 Single command registry
- `app/discord/commands/__init__.py` defines a single list `MODULES`.
- `register_all(bot, tree)` iterates `MODULES` and imports modules at runtime.
- Missing modules are allowed during incremental builds (log + skip).

### 1.3 Commands are split into modules
- Each file under `app/discord/commands/` exposes:
  - `def register(bot, tree) -> None`
- A module should contain only its domain logic (impact, approvals, training, access, etc.).
- Commands should be short and call helper functions where possible.

### 1.4 Single HTTP spine (no duplication)
- `app/discord/commands/shared.py` is the **single source of truth** for bot→backend HTTP behavior:
  - `api_request(...)`
  - `format_api_error(...)`
  - `ensure_person_by_discord(...)`
  - small primitives used by multiple command modules
- New command modules should use `api_request()` rather than rolling their own `httpx` calls.

---

## 2) Dependency / Import Rules

### 2.1 Keep shared.py dependency-light
- `commands/shared.py` must **not** import `discord`.
- It may import `httpx` and `settings`.
- If you need Discord types in shared helpers:
  - accept `Any` or protocols
  - keep it runtime-safe without importing Discord

### 2.2 Bot owns the httpx client
- `bot.api` is the only shared http client.
- Commands do **not** create new `httpx.AsyncClient` instances.

### 2.3 Settings are env-backed
- All config comes from `settings`:
  - API base
  - timeouts
  - user agent
  - role names
  - feature flags (wins automation, role sync, guild-only sync)

---

## 3) Feature Flags & Safety

### 3.1 Feature flags exist to prevent breakage
- Every optional subsystem must have a settings flag:
  - `enable_wins_automation`
  - `enable_role_sync`
  - etc.
- Behavior when disabled:
  - do nothing / return early
  - no errors, no side effects

### 3.2 “Never break the bot”
- Event handlers (like `on_message`) must catch exceptions and return.
- Command handlers must respond with a helpful message on failure:
  - never leak stack traces to users

### 3.3 Best-effort backend integration
- If backend endpoints are missing/not deployed yet:
  - treat `404`/`405` as “feature not available”
  - return gracefully with a human-friendly message (or silent ignore for automation)

---

## 4) Error Handling Standards

### 4.1 api_request contract
- Always use `api_request()` from `shared.py` for backend calls.
- It returns: `(status_code, response_text, json_dict_or_none)`

### 4.2 User-facing error format
- Use `format_api_error(code, text, data)` for consistent messages.

### 4.3 Timeouts
- Use explicit timeouts for each call site:
  - typical: 10–25 seconds depending on endpoint

---

## 5) Command Standards

### 5.1 Always defer for non-trivial commands
- Use `await interaction.response.defer(ephemeral=True)` for commands that:
  - call the backend
  - do network work
  - can take more than a moment

### 5.2 Ephemeral by default (for operational commands)
- Commands that handle access, approvals, onboarding, personal status:
  - ephemeral responses by default
- Public broadcast commands should be explicit and rare.

### 5.3 Command naming
- Keep names short and obvious:
  - `/start`, `/whoami`, `/log`, `/my_next`
  - `/request_team_access`, `/approvals_pending`, `/approve`
  - `/sync_me`

### 5.4 Stability rule
- Don’t change command names lightly once volunteers are trained on them.
- If a rename is required:
  - keep the old command temporarily if possible
  - provide a deprecation message

---

## 6) Discord Role Sync Rules

### 6.1 Bot permissions
- Role sync requires:
  - bot has Manage Roles permission
  - target roles exist
  - bot’s top role is above roles it edits
- Role sync code must clearly explain hierarchy failures to admins.

### 6.2 Source of truth
- Backend determines access flags.
- Bot applies roles best-effort:
  - `/sync_me` reads backend and aligns user roles
  - approvals flow may apply the role on approve (if enabled)

---

## 7) Wins Automation Rules

### 7.1 Trigger emoji
- If `wins_trigger_emoji` is empty or whitespace:
  - wins automation does nothing

### 7.2 Channel gate
- If `wins_require_channel` is enabled:
  - only ingest from `wins_channel_name` (case-insensitive compare)

### 7.3 Debounce / memory safety
- Debounce keys must have TTL and pruning logic.
- Cache must not grow unbounded (soft + hard limit is required).

### 7.4 No user spam
- Wins ingestion is silent:
  - no replies, no reactions unless explicitly designed later

---

## 8) Backend Endpoint Conventions

When adding a new feature, define backend endpoints with predictable patterns:
- `GET /<resource>/...` for reads
- `POST /<resource>/...` for writes/actions
- Return JSON dicts consistently:
  - include `"detail"` on error when possible (so bot can display it)

If an endpoint is not guaranteed to exist yet, the bot must treat `404`/`405` gracefully.

---

## 9) Adding a New Command Module (Step-by-step)

1) Create `app/discord/commands/<module>.py`
2) Implement:
   - `def register(bot, tree) -> None`
   - add `@tree.command(...)` inside register
3) Use `api_request()` for backend calls
4) Add module name to `MODULES` in `commands/__init__.py`
5) Smoke test in a guild-sync environment:
   - set `discord_sync_guild_only=True`
   - set `discord_guild_id=<test guild>`
6) Keep user messaging crisp and friendly
7) Only after it’s stable, consider expanding features

---

## 10) Phase 4 “Tight” Definition (Exit Criteria)

We consider Phase 4 “tight” when:
- Bot boots without errors
- Command sync works (guild or global)
- Basic commands respond: `/ping`, `/config` (if present), `/start`, `/whoami`
- Role sync works or fails with clear actionable messages
- Approvals flow works end-to-end or fails clearly if backend endpoints missing
- Wins automation does not crash bot, does not grow memory unbounded, and is best-effort if endpoint missing

---

## 11) Local Dev / Ops Notes (Quick)

- Prefer guild-only sync for iteration (fast):
  - `discord_sync_guild_only=True`
  - `discord_guild_id=<your test server id>`
- For production:
  - global sync is OK, but changes propagate slower
- Keep logs at INFO for normal ops; DEBUG for troubleshooting.

---

## 12) “Do Not” List

- Do not duplicate `httpx` request logic in each command module.
- Do not import `discord` inside `shared.py`.
- Do not crash `on_message` / event handlers.
- Do not add new features without adding a feature flag if it’s optional.
- Do not expand Phase scope until Phase 4 exit criteria is met.

---
