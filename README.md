# Campaign Dashboard (Local-First) — Milestone 1

This repository is the **local-first backend** for your campaign operating system:
- FastAPI API server (local)
- SQLite database (local)
- Discord bot that calls the local API (local)
- Optional: Census + BLS lookups via Discord (keys remain local)

> **IMPORTANT:** Your `.env` file is local-only and must never be committed.

---

## ✅ What’s included in Milestone 1

### API Server
- `GET /health` → confirms server is running
- People / Power Teams / Voters (basic CRUD)
- Impact Reach calculation (downstream people + downstream voters)
- Census + BLS query endpoints (only if keys present)

### Discord Bot
Slash commands:
- `/ping` → sanity check
- `/impact person_id:<id>` → shows Impact Reach for a person
- `/census county_pop state:<AR> county_fips:<###>` → example Census query (population)
- `/bls series series_id:<id>` → example BLS query (series data)

---

## 1) ACTION: Create the repository in GitHub

1. In GitHub: create a new repo named: `campaign-dashboard`
2. Clone it locally (or create locally then set remote)

---

## 2) ACTION: Create a local virtual environment

In PowerShell, from the repo folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3) ACTION: Create your local `.env`

1. Copy `.env.example` to `.env`
2. Fill in keys locally

```powershell
copy .env.example .env
notepad .env
```

---

## 4) ACTION: Create a Discord Application + Bot (first-time, explicit)

### Step A — Create the Application
1. Go to the Discord Developer Portal.
2. Click **New Application**
3. Name it (e.g., `KellySOS Dashboard Bot`) → Create

### Step B — Create the Bot
1. In the left sidebar, click **Bot**
2. Click **Add Bot**
3. Under **Privileged Gateway Intents**, turn ON:
   - Presence Intent (optional)
   - Server Members Intent (optional)
   - Message Content Intent (recommended if later you want message-based features)
4. Click **Reset Token** (or View Token) and copy it
5. Paste it into your `.env` as:
   - `DISCORD_BOT_TOKEN=...`

### Step C — Invite the Bot to your server
1. Left sidebar → **OAuth2** → **URL Generator**
2. Under **SCOPES**, check:
   - `bot`
   - `applications.commands`
3. Under **BOT PERMISSIONS**, check:
   - Send Messages
   - Read Message History
   - Manage Threads (optional)
   - Create Public Threads (optional)
   - Use Slash Commands (sometimes auto-included)
4. Copy the generated URL and open it in your browser
5. Select your server → Authorize

---

## 5) ACTION: Run the API (Terminal 1)

```powershell
.\.venv\Scripts\Activate.ps1
python .\run_api.py
```

You should see:
- Database created at `./data/campaign.sqlite`
- API running at http://127.0.0.1:8000
- Docs at http://127.0.0.1:8000/docs

---

## 6) ACTION: Run the Discord bot (Terminal 2)

```powershell
.\.venv\Scripts\Activate.ps1
python .\run_bot.py
```

In Discord, try:
- `/ping`

---

## 7) Milestone discipline (your workflow)

At the end of each milestone:
1. Run tests / basic checks
2. Git commit + push
3. Zip the repo and store it
4. Start a new ChatGPT thread
5. Use the migration sentence at the bottom of the assistant message

---

## Notes on Keys (Local Only)
- `.env` stays on your machine
- No keys are ever shipped to Netlify
- Discord bot calls your local API, which calls Census/BLS/OpenAI

---

## Next Milestones (preview)
- Milestone 2: Full Power of 5 workflows + voter pipeline + event model
- Milestone 3: Dashboard UI scaffold (Netlify-safe, no secrets)
- Milestone 4: Messaging router (Substack → Discord → teams)

MASTER SYSTEM ANALYSIS & BUILD MAP

Campaign Operating System (Discord-Centered Control Plane)
Locked Release: v0.4.0
Continuity phrase: The purple atlas organizes eight rivers beneath the central star.
Build Law: Checklist first. System map first. No code before Definition of Done.

1. Executive Orientation (Why This Exists)

This system is not a collection of tools. It is a campaign operating system that:

Uses Discord as the human coordination layer

Uses a Dashboard API + Database as the system of record

Runs multiple campaign lanes (Field, VR, Training, Comms, Fundraising) on shared infrastructure

Enforces discipline through checklists, gates, and phase locks

The addition of a Statewide Voter Registration Campaign creates a parallel campaign lane that is:

Operationally independent

Strategically central

Technically integrated

2. System Architecture (Macro Map)
Discord (Volunteer + Operator UX)
│
├── Core Commands (Shared)
│   ├── Onboarding
│   ├── /log (impact)
│   ├── /request_team_access
│   └── Approvals & routing
│
├── Campaign Lanes (UX + Logic)
│   ├── Statewide Voter Registration (NEW)
│   ├── Field Operations
│   ├── Training & Education
│   ├── Communications
│   └── Fundraising (future)
│
├── Dashboard API (Single System of Record)
│   ├── People (canonical identity)
│   ├── Counties & Geo Snapshots
│   ├── Impact & KPIs
│   ├── Events / Drives
│   └── Targets & Planning
│
└── Database
    ├── People
    ├── Counties
    ├── Impact Logs
    ├── (Planned) VR Targets, Drives, Coverage

3. Current Locked State (Phase 4 Complete)

Verified in v0.4.0:

Canonical people model

Discord onboarding + requests stabilized

Impact logging pathways exist

Counties and snapshots exist

Error handling + config discipline enforced

Repo is reproducible (tag + zip + dependency lock)

No further Phase 4 changes allowed.

4. Campaign Lanes Overview (Macro)
Lane	Purpose	Status
Core Ops	Identity, roles, approvals	✅ Active
Statewide Voter Registration	Registration drives, targets, coverage	🟡 Planned (Phase 6)
Training	Education + compliance	🟡 Planned
Field Ops	Volunteer activation	🟡 Planned
Communications	Content + approvals	🟡 Planned
5. NEW: STATEWIDE VOTER REGISTRATION CAMPAIGN (DEDICATED LANE)
5.1 Strategic Framing

The VR campaign is treated as:

A campaign within the campaign

With its own UX, KPIs, maps, and planning

Sharing people, approvals, teams, and infrastructure

This allows:

Separate focus

Separate pacing

Unified accountability

5.2 HARD-WIRED TOP 15 COUNTY FOCUS (INITIAL TARGET SET)

These counties are first-class objects in the VR lane from day one:

Pulaski

Washington

Faulkner

Craighead

Crittenden

Jefferson

Sebastian

Benton

Union

Clark

Garland

Pope

Saline

Miller

White

Design Implications (Locked):

All VR dashboards default to this list

KPIs roll up per county and statewide

Coverage gaps are calculated first against these counties

Expansion beyond Top 15 is Phase-gated (not ad hoc)

5.3 VR Lane Core Objects (What the System Will Track)
Structural Objects

VR Target

County

Goal number

Deadline

Priority tier (Top 15 vs Expansion)

VR Drive

Location / County

Date/time

Team / owner

Staffing plan

Materials checklist

VR Activity

Registrations started

Registrations submitted

Registrations verified

5.4 VR Lane KPIs (Planning-Level, Not Messaging)

(Exact numbers filled later; structure locked now)

Statewide

Total registrations submitted

Verified registrations

Pace vs time

County-Level (Top 15)

Target vs actual

Coverage %

Drives executed

Active volunteers

Operational Quality

Conversion rate (contact → submitted)

Verification rate

Training completion %

5.5 VR Lane UX Separation
Volunteer UX

Clear “next action” prompts

Simple logging tied to drives/counties

Training-gated access

No exposure to planning complexity

Operator UX

County dashboard (Top 15 front-loaded)

Drive calendar + staffing gaps

Quality/verification queues

Approval flows for new drives/materials

Leadership UX

Statewide progress snapshot

County gap analysis

Resource reallocation signals

6. MASTER PHASE ROADMAP (Updated)
Phase 5 — Stabilization & Control (DOCS FIRST)

Goal: Governance, clarity, zero looping.

Deliverables

Master checklist tracker (single source of truth)

System map (this document formalized)

Lane definitions + ownership

Definition of Done for all future phases

Operator runbook

Phase 6 — Statewide Voter Registration Lane (BUILD)

Goal: Stand up VR campaign UX + planning + KPIs.

Exit Gate

Top 15 counties fully represented

VR drives can be planned, staffed, logged, reported

Statewide + county dashboards functional

Phase 7 — Training & Compliance Lane

Training catalog

Completion gating

Assignment + tracking

Phase 8 — Field Scale & Delegation

Volunteer lifecycle states

Team leads

Delegated approvals

Phase 9 — GOTV / Voter Ops

Segmentation

Execution timelines

Daily checklists

Phase 10 — Communications Engine

Content pipeline

Approvals

Metrics feedback

7. Phase 5 MICRO CHECKLIST (STRICT)
Phase 5.1 — Master Checklist + System Map (NOW)

 Lock Top 15 counties as VR priority set ✅

 Define all campaign lanes

 Define objects per lane

 Define KPIs per lane

 Write Definitions of Done (Phases 5–10)

 Create “stop-the-line” rules

No code allowed until all boxes are checked.

Phase 5.2 — Hardening (Targeted Code)

Permissions fail-closed

Approval tightening

Config immutability

Command scope discipline

Phase 5.3 — Operator Readiness

Admin runbook

Reset/resync procedures

Monitoring checklist

8. Governance Rules (Locked)

One checklist to rule the build

Every feature belongs to a lane

No lane without KPIs

No code before Definition of Done

Every phase ends with tag + zip + synopsis

PHASE 5.1 — MASTER CHECKLIST & SYSTEM MAP

Campaign Operating System
Locked Base Version: v0.4.0
Continuity Phrase: The purple atlas organizes eight rivers beneath the central star.

Build Rule:

No code, commands, or UX changes are allowed unless a checklist item exists and is checked.

SECTION 1 — SYSTEM PURPOSE & BOUNDARIES
1.1 What This System Is

A campaign operating system that:

Uses Discord as the human coordination layer

Uses a Dashboard API + Database as the system of record

Runs multiple campaign lanes on shared infrastructure

Enforces discipline through checklists, gates, and locks

1.2 What This System Is NOT

Not a single-purpose bot

Not ad-hoc tooling

Not message-first

Not build-as-you-go

SECTION 2 — CAMPAIGN LANES (LOCKED)

Every feature, command, or screen must belong to exactly one lane.

2.1 Core Ops Lane (Always-On)

Purpose: Shared foundation for everything else

Objects

Person (canonical identity)

Team

Role / permission

Approval

Impact log

County

Status: ✅ Active (v0.4.0)

2.2 Statewide Voter Registration Lane (DEDICATED)

Purpose: A campaign-within-the-campaign with its own UX, KPIs, and planning

Status: 🟡 Planned (Phase 6 build)

This lane shares infrastructure but has independent dashboards, pacing, and targets.

2.3 Training & Compliance Lane

Purpose: Ensure volunteers are prepared, compliant, and consistent

Status: 🟡 Planned

2.4 Field Operations Lane

Purpose: Volunteer activation, staffing, and delegation

Status: 🟡 Planned

2.5 Communications Lane

Purpose: Content workflows, approvals, cadence, metrics

Status: 🟡 Planned

SECTION 3 — STATEWIDE VOTER REGISTRATION LANE (SYSTEM MAP)
3.1 Strategic Position

The VR lane is:

Operationally independent

Strategically central

Technically integrated

It has:

Its own dashboards

Its own KPIs

Its own planning objects

Shared people, approvals, teams, and impact pathways

3.2 HARD-WIRED PRIORITY COUNTIES (LOCKED)

These counties are first-class objects in all VR views and reports:

Pulaski

Washington

Faulkner

Craighead

Crittenden

Jefferson

Sebastian

Benton

Union

Clark

Garland

Pope

Saline

Miller

White

Rules

VR dashboards default to these counties

All pacing and coverage calculations start here

Expansion beyond these counties requires a phase gate

3.3 VR Lane Core Objects (Defined, Not Built)
VR Target

County

Target registrations (PLACEHOLDER)

Deadline (PLACEHOLDER)

Priority tier (Top 15 / Expansion)

VR Drive

County

Location

Date / time

Owner

Team

Materials checklist

Staffing requirement

VR Activity

Registrations started

Registrations submitted

Registrations verified

3.4 VR KPIs (STRUCTURE ONLY — VALUES TBD)
Statewide KPIs

Total registrations submitted (TBD)

Verified registrations (TBD)

Pace vs timeline (TBD)

County-Level KPIs (Top 15)

Target vs actual (TBD)

Coverage % (TBD)

Number of drives

Active volunteers

Quality KPIs

Conversion rate (contact → submitted)

Verification rate

Training completion rate

3.5 VR UX SEPARATION (LOCKED)
Volunteer UX

“What do I do next?” prompts

Simple logging

Training-gated access

No exposure to targets or planning logic

Operator UX

County dashboards (Top 15 first)

Drive calendar + staffing gaps

Verification and quality queues

Approvals for drives/materials

Leadership UX

Statewide progress snapshot

County gap analysis

Resource reallocation signals

SECTION 4 — PHASE ROADMAP (LOCKED ORDER)
Phase 5 — Stabilization & Control

Goal: Zero looping, total clarity

Outputs

This document

Master checklist tracker

Definitions of Done

Operator runbook

Phase 6 — Statewide Voter Registration Build

Goal: VR lane fully operational

Exit Criteria

VR drives can be planned, staffed, logged, reported

Top 15 counties represented in dashboards

Statewide + county-level KPIs visible (with placeholders filled)

Phase 7 — Training & Compliance

Training catalog

Completion tracking

Assignment gating

Phase 8 — Field Scale & Delegation

Volunteer lifecycle states

Team leads

Delegated approvals

Phase 9 — GOTV / Voter Ops

Segmentation

Execution timelines

Daily checklists

Phase 10 — Communications Engine

Content pipeline

Approval workflows

Metrics feedback

SECTION 5 — MASTER CHECKLIST TRACKER (PHASE 5.1)
Phase 5.1 Checklist (NO CODE ALLOWED)

 Phase 4 locked (tag + zip)

 VR lane defined as dedicated campaign

 Top 15 counties locked

 Campaign lanes enumerated

 VR objects defined

 VR KPIs structured (placeholders only)

 Definitions of Done written for Phases 5–10

 Operator vs Volunteer vs Leadership boundaries finalized

 Stop-the-line rules written

 Phase 5 sign-off

SECTION 6 — STOP-THE-LINE RULES (NON-NEGOTIABLE)

Stop and return to this document if:

A feature doesn’t clearly belong to a lane

A KPI can’t be named

A permission boundary is unclear

A checklist item doesn’t exist

You feel “we’ve already done this once”

SECTION 7 — PHASE 5 EXIT CRITERIA

Phase 5 is complete only when:

This document is final

Definitions of Done are approved

A single master checklist exists

You can answer, at any moment:
“What phase are we in and why?”
DEFINITIONS OF DONE (DoD)

Campaign Operating System
Applies to: All future work after v0.4.0
Rule: If a box is not checked, the phase is not done.

PHASE 5 — STABILIZATION & CONTROL (FOUNDATION)
Purpose

Create governance, clarity, and control so the system never loops again.

Phase 5 is DONE when:

 Master Checklist + System Map document exists as the single source of truth

 All campaign lanes are named, scoped, and mutually exclusive

 Statewide Voter Registration lane is formally defined (objects, UX split, KPIs structured)

 Top 15 counties are locked as first-class VR priorities

 Definitions of Done for Phases 5–10 are written and approved (this document)

 Volunteer / Operator / Leadership boundaries are explicitly documented

 Stop-the-line rules are written and agreed to

 Operator runbook outline exists (even if empty sections remain)

 Phase is tagged, zipped, and logged with synopsis

Anti-Loop Guard

If any new feature is proposed without a checklist item → Phase 5 is not complete.

PHASE 6 — STATEWIDE VOTER REGISTRATION CAMPAIGN (DEDICATED LANE)
Purpose

Stand up the voter registration campaign as a first-class, dedicated UX lane using shared infrastructure.

Phase 6 is DONE when:

 VR lane has dedicated UX views (Volunteer / Operator / Leadership)

 Top 15 counties appear by default in all VR dashboards and reports

 VR Targets exist for each Top 15 county (values may be placeholders until data review is finalized)

 VR Drives can be created, scheduled, staffed, and approved

 VR Activity logging exists (started / submitted / verified)

 Statewide VR KPIs are visible (structure complete, values allowed to evolve)

 County-level VR KPIs roll up correctly

 Training gating exists for VR participation (cannot act without required training)

 VR lane reuses people, teams, approvals, and impact logging (no parallel systems)

 Operator can answer: “Which Top 15 county is behind pace today?”

 Phase is tagged, zipped, and logged with synopsis

Anti-Scope Guard

No GOTV, persuasion, or messaging logic is introduced in Phase 6.

PHASE 7 — TRAINING & COMPLIANCE LANE
Purpose

Ensure volunteers are prepared, compliant, and consistent before representing the campaign.

Phase 7 is DONE when:

 Training catalog exists with clear ownership

 Training lifecycle is defined (draft → review → publish)

 Completion tracking is visible to operators

 Training gating is enforced (cannot access certain actions without completion)

 Trainings can be assigned by role or lane

 VR-specific trainings are linked to VR permissions

 Operator can answer: “Who is trained and who is blocked?”

 Phase is tagged, zipped, and logged with synopsis

Anti-Risk Guard

No volunteer represents the campaign in public workflows without required training.

PHASE 8 — FIELD SCALE & DELEGATION
Purpose

Scale operations by introducing leadership layers without losing control or data integrity.

Phase 8 is DONE when:

 Volunteer lifecycle states are defined (new → active → lead → regional)

 Team leads can be designated and revoked

 Delegated approvals exist with escalation paths

 Metrics roll up by volunteer, team, and county

 Operators retain override authority at all times

 Operator can answer: “Who owns this problem at the local level?”

 Phase is tagged, zipped, and logged with synopsis

Anti-Chaos Guard

Delegation never bypasses approvals or logging.

PHASE 9 — VOTER OPS / GOTV EXECUTION
Purpose

Transition from preparation to execution with precision and discipline.

Phase 9 is DONE when:

 GOTV mode is explicitly defined and toggled

 Daily / weekly execution checklists exist

 County-level execution plans are visible

 Metrics update on execution cadence (daily minimum)

 Leadership has a single GOTV snapshot view

 Operator can answer: “What must happen today?”

 Phase is tagged, zipped, and logged with synopsis

Anti-Burnout Guard

No ad-hoc execution; everything runs through checklists.

PHASE 10 — COMMUNICATIONS ENGINE
Purpose

Create a disciplined communications pipeline with approvals and feedback loops.

Phase 10 is DONE when:

 Content pipeline exists (draft → review → approve → publish)

 Approval roles and escalation are enforced

 Content is tied to lanes (VR, Field, Training, etc.)

 Publishing cadence is visible

 Performance metrics feed back into planning

 Operator can answer: “What content is live, approved, and effective?”

 Phase is tagged, zipped, and logged with synopsis

Anti-Noise Guard

No content publishes without approval and attribution.

GLOBAL COMPLETION RULES (APPLY TO EVERY PHASE)

A phase is invalid if:

It lacks a checklist

It cannot be tagged and zipped

You cannot clearly state why it is complete

It introduces functionality outside its lane

A phase is successful if:

You can pause work for a week

Return

And immediately know exactly where you are

5.2.1 Permission & Role Guard Hardening

Goal: Nothing works unless it should.

 All role checks fail-closed (no implicit access)

 Explicit error messages for missing permissions (user-safe)

 No fallback to “admin” unless explicitly defined

 Approval-required actions cannot be executed without approval

 Role names / IDs resolved deterministically (no ambiguity)

Done when:
Operator can answer: “Why was this blocked?” immediately.

5.2.2 Approval Policy Tightening

Goal: One approval model, everywhere.

 All approval logic routed through a single shared mechanism

 Approval states are explicit (pending / approved / denied)

 No side effects before approval

 Clear operator override path

 Auditability: who approved, when, what

Done when:
You can reconstruct any approval decision after the fact.

5.2.3 Configuration Immutability & Validation

Goal: No silent misconfiguration.

 Settings validated at startup

 Required env vars enforced

 Invalid config fails fast (won’t boot)

 Phase locks respected (no Phase 6 features toggled early)

 Safe defaults documented

Done when:
Misconfiguration causes a clear startup failure, not weird behavior.

5.2.4 Command Scope Discipline

Goal: Predictable Discord behavior.

 Guild-scoped vs global commands explicitly controlled

 No duplicate or stale command registrations

 Command sync behavior documented

 Beta / production distinction respected

Done when:
You know exactly where and when a command appears.

5.2.5 Error Handling Consistency

Goal: Humans see clarity, not stack traces.

 All user-facing errors are friendly and actionable

 Internal errors are logged with context

 No raw exceptions leak to Discord

 Common failure cases documented in runbook

Done when:
A volunteer never sees developer noise.

PHASE 5.2 EXIT CRITERIA (NON-NEGOTIABLE)

Phase 5.2 is complete only when:

 All items in 5.2.1–5.2.5 are checked

 No new commands or UX added

 Existing flows behave deterministically

 Operator runbook sections for:

permissions

approvals

config

command sync
…are at least outlined

 Phase is tagged, zipped, and logged with synopsis

BUILD DISCIPLINE (REASSERTED)

One file at a time

Full-file replacement only

No patches

I direct file order

You paste the full current file

I return a full rewritten file

We track checklist status explicitly