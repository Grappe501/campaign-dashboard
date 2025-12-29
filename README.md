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

