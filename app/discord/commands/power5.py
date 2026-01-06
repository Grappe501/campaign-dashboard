from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypedDict

import discord
from discord import app_commands

from .shared import api_request, ensure_person_by_discord, format_api_error

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


def _as_int(x: Any) -> Optional[int]:
    try:
        if isinstance(x, int):
            return x
        if isinstance(x, str) and x.strip().isdigit():
            return int(x.strip())
    except Exception:
        return None
    return None


def _clean_destination(dest: str, max_len: int = 200) -> str:
    s = (dest or "").strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


# -----------------------------------------------------------------------------
# Power of 5 — Private Flow (Trust-Safe UX + Action Capture, in-memory)
# -----------------------------------------------------------------------------

class _P5TeamMember(TypedDict, total=False):
    name: str
    relationship: str
    status_check_state: str  # not_started|in_progress|confirmed


class _P5Registration(TypedDict, total=False):
    registration_type: str  # new|update|status_check
    linked_member_index: Optional[int]  # 1-based index or None
    notes: str


class _P5State(TypedDict, total=False):
    step: int
    self_status_checked: bool
    team_members: List[_P5TeamMember]
    registrations: List[_P5Registration]
    invite_token: str


def _get_p5_store(bot: discord.Client) -> Dict[int, _P5State]:
    """
    Minimal in-memory persistence keyed by Discord user id.

    Why in-memory (for now):
      - Ships the private, trust-safe UX + action capture without requiring DB schema.
      - Avoids exposing IDs or requiring team_id to start.
      - Next step will persist deeper detail via dashboard API once endpoints are confirmed.

    Note:
      - This survives within a running process but not a restart.
    """
    store = getattr(bot, "_p5_state_store", None)
    if not isinstance(store, dict):
        store = {}
        setattr(bot, "_p5_state_store", store)
    return store  # type: ignore[return-value]


def _state_get(bot: discord.Client, user_id: int) -> _P5State:
    store = _get_p5_store(bot)
    st = store.get(user_id) or {}
    # Normalize defaults
    if "step" not in st:
        st["step"] = 1
    if "self_status_checked" not in st:
        st["self_status_checked"] = False
    if "team_members" not in st or not isinstance(st.get("team_members"), list):
        st["team_members"] = []
    if "registrations" not in st or not isinstance(st.get("registrations"), list):
        st["registrations"] = []
    store[user_id] = st
    return st


def _state_save(bot: discord.Client, user_id: int, st: _P5State) -> None:
    store = _get_p5_store(bot)
    store[user_id] = st


def _p5_step_total() -> int:
    return 8


def _p5_step_content(step: int) -> str:
    """
    User-facing copy. Private-only. No quota/requirement language.
    """
    step = max(1, min(_p5_step_total(), step))

    blocks: Dict[int, str] = {
        1: (
            "**Power of 5**\n\n"
            "This is a private space to organize voter registration support with people you already know.\n\n"
            "Nothing here is required.\n"
            "Nothing you enter is shared publicly.\n\n"
            "You can stop at any point."
        ),
        2: (
            "**Why Power of 5**\n\n"
            "Most people protect the vote by helping a small circle they already trust.\n\n"
            "Power of 5 is a way to:\n"
            "• Make sure voter registration actually goes through\n"
            "• Stay with people through Election Day\n"
            "• Help your community understand the system\n\n"
            "You decide how far to go and when."
        ),
        3: (
            "**First step: Check voter registration**\n\n"
            "Before anything else, we focus on making sure voter registration is accurate.\n\n"
            "Many registrations fail or get delayed without notice.\n\n"
            "Most people start by helping:\n"
            "• themselves\n"
            "• a small group they already know\n\n"
            "We’ll start there."
        ),
        4: (
            "**Your Power Team**\n\n"
            "These are people you know personally and trust.\n\n"
            "Most people start with **five**, because it’s a manageable number to stay connected with.\n\n"
            "For each person, the goal is simple:\n"
            "✔ Check voter registration status\n"
            "✔ Confirm it went through correctly\n\n"
            "This is about care, not pressure."
        ),
        5: (
            "**Staying with voters**\n\n"
            "When someone registers or checks their status, we collect email and phone so we can:\n"
            "1) Confirm their registration processed correctly\n"
            "2) Share trusted civic education\n"
            "3) Help them make a vote plan\n"
            "4) Support them through Election Day\n\n"
            "We don’t drop people after registration."
        ),
        6: (
            "**Registration support**\n\n"
            "You’ll see space to note up to **10 voter registrations** you help complete.\n\n"
            "This space exists so nothing gets lost.\n\n"
            "It’s not a limit.\n"
            "If you do more, the space grows quietly."
        ),
        7: (
            "**Inviting others**\n\n"
            "When someone from your Power Team wants to help others, you can invite them into this hub.\n\n"
            "They’ll go through the same process you did.\n\n"
            "This is how support spreads — without hierarchy."
        ),
        8: (
            "**You’re in control**\n\n"
            "You can pause, stop, or continue at any time.\n\n"
            "Helping one person matters.\n"
            "Helping many people matters.\n\n"
            "The pace is yours."
        ),
    }

    return blocks.get(step, blocks[1])


def _p5_footer(step: int) -> str:
    return f"\n\n—\nStep **{step}** of **{_p5_step_total()}**"


def _p5_progress_summary(st: _P5State) -> str:
    checked = "✅" if st.get("self_status_checked") else "—"
    tm = st.get("team_members") or []
    regs = st.get("registrations") or []
    team_count = len(tm)
    reg_count = len(regs)

    team_space = f"{team_count} noted (space for 5)"
    reg_space = f"{reg_count} noted (space for 10)"

    return (
        "**Your private notes (not shared publicly):**\n"
        f"- Your voter status checked: {checked}\n"
        f"- Power Team: {team_space}\n"
        f"- Registration support: {reg_space}"
    )


def _fmt_team_members(st: _P5State) -> str:
    tm = st.get("team_members") or []
    if not tm:
        return "No Power Team members noted yet."

    lines: List[str] = []
    for i, m in enumerate(tm[:50], start=1):
        name = (m.get("name") or "").strip() or f"Member {i}"
        rel = (m.get("relationship") or "").strip()
        state = (m.get("status_check_state") or "not_started").strip()
        suffix = []
        if rel:
            suffix.append(rel)
        if state:
            suffix.append(state)
        meta = f" — {', '.join(suffix)}" if suffix else ""
        lines.append(f"{i}) {name}{meta}")

    if len(tm) > 50:
        lines.append("…(truncated)")
    return "\n".join(lines)


def _fmt_registrations(st: _P5State) -> str:
    regs = st.get("registrations") or []
    if not regs:
        return "No registration support noted yet."

    lines: List[str] = []
    for i, r in enumerate(regs[:50], start=1):
        rtype = (r.get("registration_type") or "new").strip()
        idx = r.get("linked_member_index")
        who = f" (Power Team #{idx})" if isinstance(idx, int) and idx >= 1 else ""
        notes = (r.get("notes") or "").strip()
        if notes:
            notes = notes if len(notes) <= 70 else (notes[:67] + "…")
            lines.append(f"{i}) {rtype}{who} — {notes}")
        else:
            lines.append(f"{i}) {rtype}{who}")

    if len(regs) > 50:
        lines.append("…(truncated)")
    return "\n".join(lines)


class AddTeamMemberModal(discord.ui.Modal, title="Add a Power Team member"):
    def __init__(self, bot: discord.Client, user_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

        self.member_name = discord.ui.TextInput(
            label="Name (first name or nickname)",
            placeholder="e.g., Maria",
            required=True,
            max_length=60,
        )
        self.relationship = discord.ui.TextInput(
            label="Relationship (optional)",
            placeholder="e.g., friend / cousin / neighbor",
            required=False,
            max_length=60,
        )
        self.add_item(self.member_name)
        self.add_item(self.relationship)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        st = _state_get(self.bot, self.user_id)
        tm = st.get("team_members") or []
        if len(tm) >= 100:
            await interaction.response.send_message(
                "You’ve noted a lot of people already. If you want help organizing, tell a leader.",
                ephemeral=True,
            )
            return

        name = str(self.member_name.value).strip()
        rel = str(self.relationship.value).strip()
        tm.append({"name": name, "relationship": rel, "status_check_state": "not_started"})
        st["team_members"] = tm
        _state_save(self.bot, self.user_id, st)

        await interaction.response.send_message(
            f"✅ Added **{name}** to your Power Team.\n\n" + _p5_progress_summary(st),
            ephemeral=True,
        )


class AddRegistrationModal(discord.ui.Modal, title="Note registration support"):
    def __init__(self, bot: discord.Client, user_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

        self.registration_type = discord.ui.TextInput(
            label="Type (new | update | status_check)",
            placeholder="new",
            required=True,
            max_length=20,
        )
        self.linked_member = discord.ui.TextInput(
            label="Power Team # (optional)",
            placeholder="e.g., 2",
            required=False,
            max_length=4,
        )
        self.notes = discord.ui.TextInput(
            label="Notes (optional)",
            placeholder="Anything you want to remember",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self.add_item(self.registration_type)
        self.add_item(self.linked_member)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        st = _state_get(self.bot, self.user_id)
        regs = st.get("registrations") or []
        if len(regs) >= 1000:
            await interaction.response.send_message(
                "You’ve noted a lot already. If you want help, tell a leader.",
                ephemeral=True,
            )
            return

        rtype = str(self.registration_type.value).strip().lower()
        if rtype not in ("new", "update", "status_check"):
            rtype = "new"

        idx_raw = str(self.linked_member.value or "").strip()
        idx: Optional[int] = None
        if idx_raw:
            try:
                maybe = int(idx_raw)
                if maybe >= 1:
                    idx = maybe
            except Exception:
                idx = None

        notes = str(self.notes.value or "").strip()

        regs.append({"registration_type": rtype, "linked_member_index": idx, "notes": notes})
        st["registrations"] = regs
        _state_save(self.bot, self.user_id, st)

        await interaction.response.send_message(
            "✅ Noted.\n\n" + _p5_progress_summary(st),
            ephemeral=True,
        )


class CreateInviteDestinationModal(discord.ui.Modal, title="Create an invite token"):
    def __init__(self, bot: discord.Client, user_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

        self.destination = discord.ui.TextInput(
            label="Destination (optional)",
            placeholder="email / phone / discord handle (or leave blank)",
            required=False,
            max_length=200,
        )
        self.add_item(self.destination)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(self.bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        pid, _, err = await ensure_person_by_discord(self.bot, interaction)
        if err or pid is None:
            await interaction.followup.send(
                "❌ I couldn’t link you to a person_id yet.\nTry again in a moment.",
                ephemeral=True,
            )
            return

        dest_raw = _clean_destination(str(self.destination.value or ""))
        # If not provided, default to internal trace value.
        dest = dest_raw if dest_raw else f"discord:{interaction.user.id}"

        payload = {
            "leader_person_id": int(pid),
            "channel": "discord",
            "destination": dest,
        }

        code, text, data = await api_request(api, "POST", "/power5/invites/create", json=payload, timeout=25)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        token = data.get("token")
        expires_at = data.get("expires_at")

        if not token:
            await interaction.followup.send("⚠️ Invite created, but no token returned by API.", ephemeral=True)
            return

        st = _state_get(self.bot, self.user_id)
        st["invite_token"] = str(token)
        _state_save(self.bot, self.user_id, st)

        await interaction.followup.send(
            "✅ **Invite token created (private).**\n\n"
            "Share this token directly with the person you’re inviting.\n"
            "They can join the hub and click **Claim invite** inside `/power_of_5`.\n\n"
            f"**Token:** `{token}`\n"
            f"**Expires:** {expires_at or '—'}\n\n"
            "We never DM invitees. We never auto-create Discord server invites.",
            ephemeral=True,
        )


class ClaimInviteTokenModal(discord.ui.Modal, title="Claim an invite token"):
    def __init__(self, bot: discord.Client, user_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

        self.token = discord.ui.TextInput(
            label="Invite token",
            placeholder="Paste the token you received",
            required=True,
            max_length=200,
        )
        self.add_item(self.token)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(self.bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        pid, _, err = await ensure_person_by_discord(self.bot, interaction)
        if err or pid is None:
            await interaction.followup.send(
                "❌ I couldn’t link you to a person_id yet.\nTry again in a moment.",
                ephemeral=True,
            )
            return

        raw = str(self.token.value or "").strip()
        if not raw:
            await interaction.followup.send("❌ Token is required.", ephemeral=True)
            return

        payload: Dict[str, Any] = {
            "token": raw,
            "invitee_person_id": int(pid),
            "status": "onboarded",
        }

        code, text, data = await api_request(api, "POST", "/power5/invites/claim", json=payload, timeout=25)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        inv = data.get("invite") or {}
        link = data.get("link") or {}

        team_id = inv.get("power_team_id")
        inviter = inv.get("invited_by_person_id")

        st = _state_get(self.bot, self.user_id)
        if "invite_token" in st:
            try:
                del st["invite_token"]
            except Exception:
                pass
        _state_save(self.bot, self.user_id, st)

        msg = (
            "✅ **Invite accepted.** You’re connected.\n\n"
            f"- invited_by_person_id: **{inviter}**\n"
            f"- power_team_id: **{team_id}**\n"
        )
        if isinstance(link, dict) and link.get("depth") is not None:
            msg += f"- link depth: **{link.get('depth')}**\n"

        msg += "\nNow run **`/power_of_5`** to start your private Power of 5 flow."
        await interaction.followup.send(msg, ephemeral=True)


class PowerOf5View(discord.ui.View):
    """
    Minimal navigation + action affordances for the Power of 5 flow.
    Private-only (ephemeral). No public posting. No auto-DMs.
    """

    def __init__(self, bot: discord.Client, user_id: int, *, step: int) -> None:
        super().__init__(timeout=900)  # 15 minutes
        self.bot = bot
        self.user_id = user_id
        self.step = max(1, min(_p5_step_total(), step))
        self._refresh_button_states()

    def _refresh_button_states(self) -> None:
        self.prev_button.disabled = self.step <= 1
        self.next_button.disabled = self.step >= _p5_step_total()

        self.mark_checked_button.disabled = self.step != 3
        self.add_member_button.disabled = self.step != 4
        self.view_team_button.disabled = self.step != 4
        self.add_registration_button.disabled = self.step != 6
        self.view_registrations_button.disabled = self.step != 6
        self.make_invite_button.disabled = self.step != 7

        # Claim invite should always be available
        self.claim_invite_button.disabled = False

    async def _update(self, interaction: discord.Interaction, new_step: int) -> None:
        st = _state_get(self.bot, self.user_id)
        st["step"] = new_step
        _state_save(self.bot, self.user_id, st)

        self.step = new_step
        self._refresh_button_states()

        content = _p5_step_content(self.step) + "\n\n" + _p5_progress_summary(st) + _p5_footer(self.step)
        await interaction.response.edit_message(content=content, view=self)

    def _guard_private(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return
        await self._update(interaction, max(1, self.step - 1))

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return
        await self._update(interaction, min(_p5_step_total(), self.step + 1))

    @discord.ui.button(label="I’ve checked my status", style=discord.ButtonStyle.success)
    async def mark_checked_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return

        st = _state_get(self.bot, self.user_id)
        st["self_status_checked"] = True
        _state_save(self.bot, self.user_id, st)

        self._refresh_button_states()
        content = _p5_step_content(self.step) + "\n\n" + _p5_progress_summary(st) + _p5_footer(self.step)
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Add Power Team member", style=discord.ButtonStyle.primary)
    async def add_member_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return

        st = _state_get(self.bot, self.user_id)
        tm = st.get("team_members") or []
        if len(tm) >= 100:
            await interaction.response.send_message(
                "You’ve already noted a lot of people. If you want help organizing, tell a leader.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AddTeamMemberModal(self.bot, self.user_id))

    @discord.ui.button(label="View my Power Team", style=discord.ButtonStyle.secondary)
    async def view_team_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return

        st = _state_get(self.bot, self.user_id)
        await interaction.response.send_message("**Your Power Team (private):**\n" + _fmt_team_members(st), ephemeral=True)

    @discord.ui.button(label="Note registration support", style=discord.ButtonStyle.primary)
    async def add_registration_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return
        await interaction.response.send_modal(AddRegistrationModal(self.bot, self.user_id))

    @discord.ui.button(label="View registrations", style=discord.ButtonStyle.secondary)
    async def view_registrations_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return

        st = _state_get(self.bot, self.user_id)
        await interaction.response.send_message(
            "**Registration support (private):**\n" + _fmt_registrations(st),
            ephemeral=True,
        )

    @discord.ui.button(label="Make invite token", style=discord.ButtonStyle.secondary)
    async def make_invite_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return

        api: Optional["httpx.AsyncClient"] = getattr(self.bot, "api", None)
        if api is None:
            await interaction.response.send_message("❌ Bot API client is not initialized.", ephemeral=True)
            return

        pid, _, err = await ensure_person_by_discord(self.bot, interaction)
        if err or pid is None:
            await interaction.response.send_message(
                "❌ I couldn’t link you to a person_id yet.\nTry again in a moment.",
                ephemeral=True,
            )
            return

        # Ask for an optional destination (email/phone/handle), but allow empty.
        await interaction.response.send_modal(CreateInviteDestinationModal(self.bot, interaction.user.id))

    @discord.ui.button(label="Claim invite", style=discord.ButtonStyle.primary)
    async def claim_invite_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        await interaction.response.send_modal(ClaimInviteTokenModal(self.bot, interaction.user.id))

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.success)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if not self._guard_private(interaction):
            await interaction.response.send_message("This flow is private to the person who started it.", ephemeral=True)
            return

        st = _state_get(self.bot, self.user_id)
        st["step"] = self.step
        _state_save(self.bot, self.user_id, st)

        await interaction.response.edit_message(
            content=(
                "✅ **Paused.**\n\n"
                "You can come back anytime by running **`/power_of_5`** again.\n\n"
                "No rush — the pace is yours."
            ),
            view=None,
        )


# -----------------------------------------------------------------------------
# Existing API-driven Power of 5 commands (admin/operator utilities)
# -----------------------------------------------------------------------------

def register(bot: discord.Client, tree: app_commands.CommandTree) -> None:
    """
    Power of 5 commands.

    Restores:
      - /power_of_5 (private Power of 5 flow + action capture; trust-safe)
      - /p5_stats   (GET  /power5/teams/{team_id}/stats)
      - /p5_invite  (POST /power5/teams/{team_id}/invites)
      - /p5_link    (POST /power5/teams/{team_id}/links)
      - /p5_tree    (GET  /power5/teams/{team_id}/tree)
    """

    @tree.command(
        name="power_of_5",
        description="Private: organize voter registration support with people you know.",
    )
    async def power_of_5(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Best-effort linking to dashboard person record when API is available.
        try:
            api = getattr(bot, "api", None)
            if api is not None:
                await ensure_person_by_discord(bot, interaction)
        except Exception:
            pass

        st = _state_get(bot, interaction.user.id)
        step = int(st.get("step") or 1)

        content = _p5_step_content(step) + "\n\n" + _p5_progress_summary(st) + _p5_footer(step)
        view = PowerOf5View(bot, interaction.user.id, step=step)
        await interaction.followup.send(content, view=view, ephemeral=True)

    @tree.command(name="p5_stats", description="Power of 5: show team stats (counts by depth/status).")
    @app_commands.describe(team_id="power_team_id (integer)")
    async def p5_stats(interaction: discord.Interaction, team_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        try:
            tid = int(team_id)
        except Exception:
            await interaction.followup.send("❌ team_id must be an integer.", ephemeral=True)
            return
        if tid < 1:
            await interaction.followup.send("❌ team_id must be >= 1.", ephemeral=True)
            return

        code, text, data = await api_request(api, "GET", f"/power5/teams/{tid}/stats", timeout=15)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        by_status = data.get("by_status", {}) or {}
        by_depth = data.get("by_depth", {}) or {}

        status_lines = [f"- {k}: {v}" for k, v in sorted(by_status.items(), key=lambda kv: str(kv[0]))]

        def _depth_key(item: Any) -> int:
            try:
                return int(item[0])
            except Exception:
                return 0

        depth_lines = [f"- depth {k}: {v}" for k, v in sorted(by_depth.items(), key=_depth_key)]

        msg = (
            f"🌟 Power of 5 stats — team_id={tid}\n"
            f"Leader person_id: {data.get('leader_person_id')}\n"
            f"Links total: {data.get('links_total')}\n\n"
            "Status counts:\n"
            + ("\n".join(status_lines) if status_lines else "- (none)")
            + "\n\nDepth counts:\n"
            + ("\n".join(depth_lines) if depth_lines else "- (none)")
        )
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(name="p5_invite", description="Power of 5: create an onboarding invite (returns token).")
    @app_commands.describe(
        team_id="power_team_id (integer)",
        invited_by_person_id="Optional: your person_id (defaults to your Discord-linked person_id)",
        channel="email|sms|discord",
        destination="email address or phone number or discord handle",
        invitee_person_id="optional existing person_id for the invitee",
    )
    async def p5_invite(
        interaction: discord.Interaction,
        team_id: int,
        channel: str,
        destination: str,
        invited_by_person_id: Optional[int] = None,
        invitee_person_id: Optional[int] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        try:
            tid = int(team_id)
        except Exception:
            await interaction.followup.send("❌ team_id must be an integer.", ephemeral=True)
            return
        if tid < 1:
            await interaction.followup.send("❌ team_id must be >= 1.", ephemeral=True)
            return

        ch = (channel or "").strip().lower()
        if ch not in ("email", "sms", "discord"):
            await interaction.followup.send("❌ channel must be `email`, `sms`, or `discord`.", ephemeral=True)
            return

        dest = _clean_destination(destination)
        if not dest:
            await interaction.followup.send("❌ destination is required.", ephemeral=True)
            return

        if invited_by_person_id is None:
            pid, _, err = await ensure_person_by_discord(bot, interaction)
            if err or pid is None:
                await interaction.followup.send(
                    "❌ I couldn't link you to a person_id in the dashboard yet.\n"
                    "Try again, or pass invited_by_person_id explicitly.",
                    ephemeral=True,
                )
                return
            invited_by_person_id = pid

        try:
            inviter_id = int(invited_by_person_id)
        except Exception:
            await interaction.followup.send("❌ invited_by_person_id must be an integer.", ephemeral=True)
            return
        if inviter_id < 1:
            await interaction.followup.send("❌ invited_by_person_id must be >= 1.", ephemeral=True)
            return

        params: Dict[str, Any] = {
            "invited_by_person_id": inviter_id,
            "channel": ch,
            "destination": dest,
        }

        if invitee_person_id is not None:
            try:
                invitee_i = int(invitee_person_id)
            except Exception:
                await interaction.followup.send("❌ invitee_person_id must be an integer.", ephemeral=True)
                return
            if invitee_i < 1:
                await interaction.followup.send("❌ invitee_person_id must be >= 1.", ephemeral=True)
                return
            params["invitee_person_id"] = invitee_i

        code, text, data = await api_request(
            api,
            "POST",
            f"/power5/teams/{tid}/invites",
            json=params,
            timeout=20,
        )
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        token = data.get("token")
        expires_at = data.get("expires_at")

        if not token:
            await interaction.followup.send(
                "⚠️ Invite created, but no token was returned by the API.",
                ephemeral=True,
            )
            return

        msg = (
            f"✅ Invite created — team_id={tid}\n"
            f"- invited_by_person_id: {inviter_id}\n"
            f"- channel: {ch}\n"
            f"- destination: {dest}\n"
            f"- expires_at: {expires_at}\n\n"
            "🔑 Token:\n"
            f"`{token}`\n\n"
            "Tip: share this token with the invitee to use during onboarding/claim."
        )
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="p5_link",
        description="Power of 5: link recruiter -> recruit inside a team (creates/updates a link).",
    )
    @app_commands.describe(
        team_id="power_team_id",
        parent_person_id="recruiter person_id",
        child_person_id="recruit person_id",
        status="invited|onboarded|active|churned (default invited)",
    )
    async def p5_link(
        interaction: discord.Interaction,
        team_id: int,
        parent_person_id: int,
        child_person_id: int,
        status: str = "invited",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        try:
            tid = int(team_id)
            parent_id = int(parent_person_id)
            child_id = int(child_person_id)
        except Exception:
            await interaction.followup.send(
                "❌ team_id, parent_person_id, and child_person_id must be integers.",
                ephemeral=True,
            )
            return

        if tid < 1 or parent_id < 1 or child_id < 1:
            await interaction.followup.send("❌ IDs must be >= 1.", ephemeral=True)
            return

        stt = (status or "").strip().lower()
        if stt not in ("invited", "onboarded", "active", "churned"):
            await interaction.followup.send(
                "❌ status must be `invited`, `onboarded`, `active`, or `churned`.",
                ephemeral=True,
            )
            return

        payload: Dict[str, Any] = {
            "power_team_id": tid,
            "parent_person_id": parent_id,
            "child_person_id": child_id,
            "status": stt,
        }

        code, text, data = await api_request(
            api,
            "POST",
            f"/power5/teams/{tid}/links",
            json=payload,
            timeout=20,
        )
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        msg = (
            "✅ Power of 5 link saved\n"
            f"- team_id: {data.get('power_team_id')}\n"
            f"- parent_person_id: {data.get('parent_person_id')}\n"
            f"- child_person_id: {data.get('child_person_id')}\n"
            f"- depth: {data.get('depth')}\n"
            f"- status: {data.get('status')}"
        )
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(name="p5_tree", description="Power of 5: show simple tree adjacency (compact).")
    @app_commands.describe(team_id="power_team_id")
    async def p5_tree(interaction: discord.Interaction, team_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        try:
            tid = int(team_id)
        except Exception:
            await interaction.followup.send("❌ team_id must be an integer.", ephemeral=True)
            return
        if tid < 1:
            await interaction.followup.send("❌ team_id must be >= 1.", ephemeral=True)
            return

        code, text, data = await api_request(api, "GET", f"/power5/teams/{tid}/tree", timeout=20)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        children = data.get("children", {}) or {}
        leader_id = data.get("leader_person_id")

        lines: List[str] = [f"Leader: {leader_id}"]
        shown = 0

        for parent, kids in (children.items() if isinstance(children, dict) else []):
            if shown >= 30:
                lines.append("…(truncated)")
                break

            kid_parts: List[str] = []
            if isinstance(kids, list):
                for k in kids:
                    if not isinstance(k, dict):
                        continue
                    cid = _as_int(k.get("child_person_id"))
                    depth = k.get("depth")
                    stx = k.get("status")
                    if cid is None:
                        continue
                    kid_parts.append(f"{cid} (d{depth},{stx})")
            else:
                kid_parts.append(str(kids))

            lines.append(f"{parent} -> " + (", ".join(kid_parts) if kid_parts else "(none)"))
            shown += 1

        await interaction.followup.send("🌳 Power of 5 Tree\n" + "\n".join(lines), ephemeral=True)
