from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from discord import app_commands

from .shared import (
    api_request,
    clamp_quantity,
    ensure_person_by_discord,
    format_api_error,
    infer_channel_from_action_type,
    next_step_for_stage,
    parse_iso_dt,
    wins_hint,
)

if TYPE_CHECKING:
    import discord
    import httpx

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    # Keep API payload timezone-naive for backend consistency
    return datetime.utcnow().replace(tzinfo=None)


def _clean_str(s: Optional[str], max_len: int = 500) -> Optional[str]:
    if s is None:
        return None
    v = str(s).strip()
    if not v:
        return None
    if len(v) > max_len:
        v = v[: max_len - 3] + "..."
    return v


def _safe_int(v: Optional[int]) -> Optional[int]:
    if v is None:
        return None
    try:
        i = int(v)
    except Exception:
        return None
    return i if i >= 0 else None


def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Impact (wins logging) commands.

    Restores:
      - /log     (POST /impact/actions)
      - /reach   (GET  /impact/reach/summary)
      - /my_next (local helper)
    """

    @tree.command(name="log", description="Log an impact action (call/text/door/event/etc).")
    @app_commands.describe(
        action_type="e.g., call, text, door, event_hosted, event_attended, post_shared, signup",
        quantity="how many (default 1)",
        actor_person_id="optional person_id who did it (defaults to you if omitted)",
        team_id="optional power_team_id",
        county_id="optional county_id",
        occurred_at="optional ISO datetime or YYYY-MM-DD (default now)",
        note="optional note (stored in meta)",
    )
    async def log_action(
        interaction: "discord.Interaction",
        action_type: str,
        quantity: int = 1,
        actor_person_id: Optional[int] = None,
        team_id: Optional[int] = None,
        county_id: Optional[int] = None,
        occurred_at: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        at = _clean_str(action_type, 80)
        if not at:
            await interaction.followup.send("❌ action_type is required (e.g., call, text, door).", ephemeral=True)
            return

        # Default actor_person_id to the Discord user if not supplied (best-effort).
        linked_person_id: Optional[int] = None
        if actor_person_id is None:
            pid, _, err = await ensure_person_by_discord(bot, interaction)
            if err is None and pid is not None:
                linked_person_id = pid
                actor_person_id = pid
            # If linking fails, proceed; backend may accept missing actor_person_id.

        dt, dt_ok = parse_iso_dt(occurred_at)
        forced_now = False
        if occurred_at and not dt_ok:
            # If user provided a date but we couldn't parse it, log as "now" explicitly.
            dt = _utcnow_naive()
            forced_now = True

        # If no occurred_at provided at all, let backend default to now by omitting occurred_at.
        qty, qty_warn = clamp_quantity(int(quantity or 1))

        guild_id = interaction.guild_id
        idem = f"discord:{guild_id}:{interaction.id}"

        meta: Dict[str, Any] = {
            "discord": {
                "guild_id": str(guild_id) if guild_id else None,
                "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
                "user_id": str(interaction.user.id) if interaction.user else None,
                "username": str(interaction.user) if interaction.user else None,
                "interaction_id": str(interaction.id),
            }
        }

        clean_note = _clean_str(note, 500)
        if clean_note:
            meta["note"] = clean_note

        payload: Dict[str, Any] = {
            "action_type": at,
            "quantity": qty,
            "actor_person_id": _safe_int(actor_person_id),
            "power_team_id": _safe_int(team_id),
            "county_id": _safe_int(county_id),
            # Only send occurred_at if user provided one (or it was invalid and we forced now)
            "occurred_at": (dt.isoformat() if (dt and occurred_at) else None),
            "source": "discord",
            "channel": infer_channel_from_action_type(at),
            "idempotency_key": idem,
            "meta": meta,
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        code, text, data = await api_request(api, "POST", "/impact/actions", json=payload, timeout=25)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        stage_changed_to = data.get("stage_changed_to")
        actor_stage = data.get("actor_stage")

        warnings: List[str] = []
        if occurred_at and forced_now:
            warnings.append("⚠️ I couldn't parse your occurred_at date/time. Logged it as *now*.")
        if qty_warn:
            warnings.append(f"⚠️ {qty_warn}")
        if linked_person_id and (actor_person_id == linked_person_id):
            warnings.append(f"ℹ️ Linked you as actor_person_id={linked_person_id} (via Discord).")

        msg = (
            "✅ Logged impact action\n"
            f"- type: {data.get('action_type')}\n"
            f"- qty: {data.get('quantity')}\n"
            f"- actor_person_id: {data.get('actor_person_id')}\n"
            f"- power_team_id: {data.get('power_team_id')}\n"
            f"- county_id: {data.get('county_id')}\n"
            f"- occurred_at: {data.get('occurred_at')}\n"
        )

        if warnings:
            msg += "\n" + "\n".join(warnings) + "\n"

        if stage_changed_to:
            msg += f"\n🎉 Stage updated: **{str(stage_changed_to).upper()}**\n{wins_hint()}"
        else:
            msg += "\n" + wins_hint()

        msg += "\n\nNext step:\n" + next_step_for_stage(actor_stage)

        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(name="reach", description="Compute impact reach summary over a date range (uses /impact/reach/summary).")
    @app_commands.describe(
        start="optional ISO datetime or YYYY-MM-DD (inclusive)",
        end="optional ISO datetime or YYYY-MM-DD (exclusive)",
        actor_person_id="optional filter",
        team_id="optional filter",
        county_id="optional filter",
    )
    async def reach(
        interaction: "discord.Interaction",
        start: Optional[str] = None,
        end: Optional[str] = None,
        actor_person_id: Optional[int] = None,
        team_id: Optional[int] = None,
        county_id: Optional[int] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api: Optional["httpx.AsyncClient"] = getattr(bot, "api", None)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        start_dt, start_ok = parse_iso_dt(start)
        end_dt, end_ok = parse_iso_dt(end)

        # Keep UX clean: if filters are invalid, warn once and ignore them.
        if (start and not start_ok) or (end and not end_ok):
            warn = "⚠️ I couldn't parse your date filter(s). "
            if start and not start_ok:
                warn += "Start ignored. "
            if end and not end_ok:
                warn += "End ignored. "
            await interaction.followup.send(warn.strip(), ephemeral=True)

        params: Dict[str, Any] = {}
        if start_dt:
            params["start"] = start_dt.isoformat()
        if end_dt:
            params["end"] = end_dt.isoformat()
        if actor_person_id is not None:
            params["actor_person_id"] = _safe_int(actor_person_id)
        if team_id is not None:
            params["power_team_id"] = _safe_int(team_id)
        if county_id is not None:
            params["county_id"] = _safe_int(county_id)

        # Drop None values
        params = {k: v for k, v in params.items() if v is not None}

        code, text, data = await api_request(api, "GET", "/impact/reach/summary", params=params, timeout=20)
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        qty_by_type = data.get("quantity_by_type", {}) or {}
        lines = [f"- {k}: {v}" for k, v in sorted(qty_by_type.items(), key=lambda kv: str(kv[0]))]

        msg = (
            "📈 Impact Reach Summary\n"
            f"Computed reach: {data.get('computed_reach')}\n"
            f"Actions rows: {data.get('actions_total')}\n"
            f"Rules loaded: {data.get('rules_loaded')}\n\n"
            "Quantities by type:\n"
            + ("\n".join(lines) if lines else "- (none)")
        )
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(name="my_next", description="Get your next suggested step in the 7-day activation arc.")
    @app_commands.describe(actor_stage="Optional: your current stage if you know it.")
    async def my_next(interaction: "discord.Interaction", actor_stage: Optional[str] = None) -> None:
        # If they didn't pass stage, try to infer from the linked person record (best-effort)
        stage = (actor_stage or "").strip() or None
        if not stage:
            try:
                _, pdata, err = await ensure_person_by_discord(bot, interaction)
                if err is None and isinstance(pdata, dict):
                    maybe = pdata.get("stage")
                    if isinstance(maybe, str) and maybe.strip():
                        stage = maybe.strip()
            except Exception:
                stage = None

        await interaction.response.send_message(next_step_for_stage(stage), ephemeral=True)
