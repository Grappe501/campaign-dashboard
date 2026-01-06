from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands

from .shared import api_request, ensure_person_by_discord, format_api_error

if TYPE_CHECKING:
    import httpx


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value if value is not None else default)
    except Exception:
        v = default
    return max(lo, min(v, hi))


def _safe_title(x: Any) -> str:
    s = ("" if x is None else str(x)).strip()
    return s if s else "(untitled)"


def _safe_slug(x: Any) -> str:
    s = ("" if x is None else str(x)).strip()
    return s if s else "n/a"


def _safe_minutes(x: Any) -> str:
    if x is None:
        return ""
    try:
        m = int(x)
        if m > 0:
            return f"{m}m"
    except Exception:
        pass
    return ""


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 3)] + "..."


def _clean_note(note: Optional[str], max_len: int = 500) -> Optional[str]:
    if note is None:
        return None
    s = str(note).strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _as_int(x: Any) -> Optional[int]:
    try:
        if isinstance(x, int):
            return x
        if isinstance(x, str) and x.strip().isdigit():
            return int(x.strip())
    except Exception:
        return None
    return None


def _parse_total_count(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    n = payload.get("total_count")
    if n is None:
        return None
    try:
        v = int(n)
        return v if v >= 0 else None
    except Exception:
        return None


def _api_client(bot: discord.Client) -> Optional["httpx.AsyncClient"]:
    return getattr(bot, "api", None)


def _status_emoji(completed: Optional[bool]) -> str:
    if completed is True:
        return "✅"
    if completed is False:
        return "⬜"
    return "•"


# -----------------------------------------------------------------------------
# Data model for UI layer
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class TrainingItem:
    id: int
    slug: str
    title: str
    description: str
    estimated_minutes: Optional[int]
    completed: Optional[bool] = None
    completed_at: Optional[str] = None


def _parse_training_items(payload: Any) -> List[TrainingItem]:
    items_raw: Any = []
    if isinstance(payload, dict):
        items_raw = payload.get("items") or []
    if not isinstance(items_raw, list):
        return []

    out: List[TrainingItem] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        mid = _as_int(it.get("id"))
        if not mid or mid < 1:
            continue

        out.append(
            TrainingItem(
                id=mid,
                slug=_safe_slug(it.get("slug")),
                title=_safe_title(it.get("title")),
                description=str(it.get("description") or ""),
                estimated_minutes=_as_int(it.get("estimated_minutes")),
                completed=(bool(it.get("completed")) if "completed" in it else None),
                completed_at=(str(it.get("completed_at")) if it.get("completed_at") else None),
            )
        )
    return out


def _build_list_embed(
    *,
    query: str,
    limit: int,
    offset: int,
    items: List[TrainingItem],
    total_hint: Optional[int],
) -> discord.Embed:
    q = (query or "").strip()
    page = (offset // max(1, limit)) + 1

    if items:
        start = offset + 1
        end = offset + len(items)
    else:
        start = 0
        end = 0

    if total_hint is None:
        range_label = f"{start}-{end}" if items else "0"
    else:
        range_label = f"{start}-{end} of {total_hint}" if items else f"0 of {total_hint}"

    title = "📚 Trainings"
    if q:
        title += f' — search: "{_truncate(q, 40)}"'

    embed = discord.Embed(
        title=title,
        description=f"Page **{page}** • Showing **{range_label}** • Limit **{limit}**",
    )

    if not items:
        embed.add_field(name="No results", value="Try a different search term.", inline=False)
        return embed

    lines: List[str] = []
    for it in items[:limit]:
        mins = _safe_minutes(it.estimated_minutes)
        mins_part = f" • {mins}" if mins else ""
        mark = _status_emoji(it.completed)
        lines.append(f"{mark} **{it.title}** (`{it.slug}`) • id:{it.id}{mins_part}")

    # Discord embed field limit is 1024 chars; truncate defensively.
    body = "\n".join(lines)
    if len(body) > 1000:
        body = body[:997] + "..."

    embed.add_field(name="Modules", value=body, inline=False)
    embed.set_footer(text="Select a module below to view details and mark complete.")
    return embed


def _build_detail_embed(it: TrainingItem) -> discord.Embed:
    mins = _safe_minutes(it.estimated_minutes)
    mins_part = f" • {mins}" if mins else ""
    mark = _status_emoji(it.completed)

    embed = discord.Embed(
        title=f"{mark} {it.title}",
        description=f"`{it.slug}` • id:{it.id}{mins_part}",
    )

    desc = (it.description or "").strip()
    if desc:
        embed.add_field(name="Description", value=_truncate(desc, 1000), inline=False)

    if it.completed is True:
        when = f"\nCompleted at: {it.completed_at}" if it.completed_at else ""
        embed.add_field(name="Status", value=f"✅ Completed{when}", inline=False)
    elif it.completed is False:
        embed.add_field(name="Status", value="⬜ Not completed yet", inline=False)

    return embed


# -----------------------------------------------------------------------------
# Discord UI: Modal for completion note
# -----------------------------------------------------------------------------
class TrainingCompleteModal(discord.ui.Modal, title="Mark training complete"):
    note = discord.ui.TextInput(
        label="Optional note (link, proof, reflection)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Paste a link to your training, or leave blank.",
    )

    def __init__(self, *, bot: discord.Client, module_id: int) -> None:
        super().__init__()
        self._bot = bot
        self._module_id = int(module_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        api = _api_client(self._bot)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        person_id, _, err = await ensure_person_by_discord(self._bot, interaction)
        if err or person_id is None:
            await interaction.followup.send(
                "❌ Could not link you to a person record.\n" + (err or ""),
                ephemeral=True,
            )
            return

        payload: Dict[str, Any] = {
            "person_id": person_id,
            "module_id": self._module_id,
            "note": _clean_note(str(self.note.value) if self.note.value else None),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        code, text, data = await api_request(api, "POST", "/training/complete", json=payload, timeout=20)
        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Training completion API not available yet (`/training/complete`).",
                ephemeral=True,
            )
            return
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        status = str(data.get("status") or "completed")
        mod_id = data.get("module_id", self._module_id)
        mod_slug = data.get("module_slug")

        extra = f"\n- module_slug: {mod_slug}" if mod_slug else ""
        if status == "already_completed":
            await interaction.followup.send(
                f"ℹ️ Already completed.\n- module_id: {mod_id}{extra}\n- person_id: {person_id}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Training marked complete.\n- module_id: {mod_id}{extra}\n- person_id: {person_id}",
            ephemeral=True,
        )


# -----------------------------------------------------------------------------
# Discord UI: Browser View (select + paging + mark complete)
# -----------------------------------------------------------------------------
class TrainingSelect(discord.ui.Select):
    def __init__(self, *, options: List[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Pick a training to view details…",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, TrainingsBrowserView):
            await interaction.response.send_message("❌ Unexpected UI state.", ephemeral=True)
            return
        await view.on_select(interaction, self.values[0])


class TrainingsBrowserView(discord.ui.View):
    """
    Ephemeral interactive browser for trainings:
      - Uses /training/modules with limit+offset+q (+ include_inactive=false)
      - Lets user select one to see details
      - Mark complete opens a Modal (note + submit)
    """

    def __init__(
        self,
        *,
        bot: discord.Client,
        person_id: Optional[int],
        query: str,
        limit: int,
        offset: int,
        items: List[TrainingItem],
        total_hint: Optional[int] = None,
    ) -> None:
        super().__init__(timeout=300)
        self._bot = bot
        self._person_id = person_id
        self._query = (query or "").strip()
        self._limit = _clamp_int(limit, 15, 5, 25)
        self._offset = max(0, int(offset))
        self._items = items
        self._selected_id: Optional[int] = None
        self._total_hint = (int(total_hint) if isinstance(total_hint, int) and total_hint >= 0 else None)

        # Build select options (Discord max 25)
        opts: List[discord.SelectOption] = []
        for it in self._items[:25]:
            mins = _safe_minutes(it.estimated_minutes)
            mins_part = f" • {mins}" if mins else ""
            done_part = " • ✅" if it.completed is True else ""
            label = _truncate(it.title, 90)
            desc = _truncate((it.description or "").replace("\n", " "), 80)
            opts.append(
                discord.SelectOption(
                    label=label,
                    description=_truncate(f"{it.slug}{mins_part}{done_part} — {desc}".strip(" —"), 100),
                    value=str(it.id),
                )
            )

        if opts:
            self.add_item(TrainingSelect(options=opts))

        # Buttons (initial states)
        self._btn_prev.disabled = self._offset <= 0
        self._btn_complete.disabled = True  # enabled after selection

        if self._total_hint is not None:
            self._btn_next.disabled = (self._offset + self._limit) >= self._total_hint
        else:
            self._btn_next.disabled = len(self._items) < self._limit

    def _selected_item(self) -> Optional[TrainingItem]:
        if self._selected_id is None:
            return None
        for it in self._items:
            if it.id == self._selected_id:
                return it
        return None

    async def on_select(self, interaction: discord.Interaction, raw_id: str) -> None:
        mid = _as_int(raw_id)
        if not mid:
            await interaction.response.send_message("❌ Invalid selection.", ephemeral=True)
            return

        self._selected_id = int(mid)
        it = self._selected_item()
        if it is None:
            await interaction.response.send_message("❌ Could not load selection.", ephemeral=True)
            return

        # Enable/disable mark-complete based on status
        self._btn_complete.disabled = bool(it.completed is True)

        list_embed = _build_list_embed(
            query=self._query,
            limit=self._limit,
            offset=self._offset,
            items=self._items,
            total_hint=self._total_hint,
        )
        detail_embed = _build_detail_embed(it)

        await interaction.response.edit_message(
            embeds=[list_embed, detail_embed],
            view=self,
        )

    async def _refetch(self, interaction: discord.Interaction, *, offset: int) -> None:
        api = _api_client(self._bot)
        if api is None:
            await interaction.response.send_message("❌ Bot API client is not initialized.", ephemeral=True)
            return

        new_offset = max(0, int(offset))
        params: Dict[str, Any] = {"limit": self._limit, "offset": new_offset, "include_inactive": False}
        if self._query:
            params["q"] = self._query

        code, text, data = await api_request(api, "GET", "/training/modules", params=params, timeout=20)
        if code in (404, 405):
            await interaction.response.send_message(
                "⚠️ Training API not available yet (`/training/modules`).",
                ephemeral=True,
            )
            return
        if code != 200 or not isinstance(data, dict):
            await interaction.response.send_message(format_api_error(code, text, data), ephemeral=True)
            return

        items = _parse_training_items(data)
        total_count = _parse_total_count(data)

        # Overlay completion state if we can
        if self._person_id is not None and items:
            code2, _, data2 = await api_request(
                api,
                "GET",
                "/training/progress",
                params={"person_id": self._person_id, "limit": 500, "include_inactive": False},
                timeout=20,
            )
            if code2 == 200 and isinstance(data2, dict):
                prog = _parse_training_items(data2)
                done_by_id: Dict[int, TrainingItem] = {it.id: it for it in prog}
                stitched: List[TrainingItem] = []
                for it in items:
                    p = done_by_id.get(it.id)
                    if p is None:
                        stitched.append(it)
                    else:
                        stitched.append(
                            TrainingItem(
                                id=it.id,
                                slug=it.slug,
                                title=it.title,
                                description=it.description,
                                estimated_minutes=it.estimated_minutes,
                                completed=p.completed,
                                completed_at=p.completed_at,
                            )
                        )
                items = stitched

        new_view = TrainingsBrowserView(
            bot=self._bot,
            person_id=self._person_id,
            query=self._query,
            limit=self._limit,
            offset=new_offset,
            items=items,
            total_hint=total_count,
        )

        list_embed = _build_list_embed(
            query=self._query,
            limit=self._limit,
            offset=new_offset,
            items=items,
            total_hint=total_count,
        )

        await interaction.response.edit_message(
            embeds=[list_embed],
            view=new_view,
        )

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def _btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        await self._refetch(interaction, offset=max(0, self._offset - self._limit))

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def _btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        await self._refetch(interaction, offset=self._offset + self._limit)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def _btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        await self._refetch(interaction, offset=self._offset)

    @discord.ui.button(label="Mark Complete", style=discord.ButtonStyle.success)
    async def _btn_complete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        it = self._selected_item()
        if it is None:
            await interaction.response.send_message("Pick a training first from the dropdown.", ephemeral=True)
            return
        if it.completed is True:
            await interaction.response.send_message("✅ You already completed this one.", ephemeral=True)
            return
        await interaction.response.send_modal(TrainingCompleteModal(bot=self._bot, module_id=it.id))

    @discord.ui.button(label="Copy Command", style=discord.ButtonStyle.primary)
    async def _btn_copy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        it = self._selected_item()
        if it is None:
            await interaction.response.send_message("Pick a training first from the dropdown.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Use:\n`/training_complete module_id:{it.id}`",
            ephemeral=True,
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def _btn_close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        try:
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True  # type: ignore[attr-defined]
        except Exception:
            pass
        # Use embeds=None instead of [] to avoid edge-case issues with some discord.py versions.
        await interaction.response.edit_message(embeds=None, content="✅ Closed trainings browser.", view=self)


class MyTrainingsLaunchView(discord.ui.View):
    """
    Small helper view attached to /my_trainings output.
    Launches the interactive browser in one click.
    """

    def __init__(self, *, bot: discord.Client, person_id: int, progress_items: List[TrainingItem]) -> None:
        super().__init__(timeout=120)
        self._bot = bot
        self._person_id = int(person_id)
        self._progress = progress_items

    @discord.ui.button(label="Browse Trainings", style=discord.ButtonStyle.primary)
    async def _open(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa: ANN001
        await interaction.response.defer(ephemeral=True)

        api = _api_client(self._bot)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        params: Dict[str, Any] = {"limit": 15, "offset": 0, "include_inactive": False}
        c, t, d = await api_request(api, "GET", "/training/modules", params=params, timeout=20)
        if c != 200 or not isinstance(d, dict):
            await interaction.followup.send(format_api_error(c, t, d), ephemeral=True)
            return

        page_items = _parse_training_items(d)
        total_hint = _parse_total_count(d)

        done_by_id: Dict[int, TrainingItem] = {it2.id: it2 for it2 in self._progress}
        stitched: List[TrainingItem] = []
        for it2 in page_items:
            p = done_by_id.get(it2.id)
            if p is None:
                stitched.append(it2)
            else:
                stitched.append(
                    TrainingItem(
                        id=it2.id,
                        slug=it2.slug,
                        title=it2.title,
                        description=it2.description,
                        estimated_minutes=it2.estimated_minutes,
                        completed=p.completed,
                        completed_at=p.completed_at,
                    )
                )

        view = TrainingsBrowserView(
            bot=self._bot,
            person_id=self._person_id,
            query="",
            limit=15,
            offset=0,
            items=stitched,
            total_hint=total_hint,
        )
        list_embed = _build_list_embed(
            query="",
            limit=15,
            offset=0,
            items=stitched,
            total_hint=total_hint,
        )
        await interaction.followup.send(embeds=[list_embed], view=view, ephemeral=True)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------
def register(bot: "discord.Client", tree: "app_commands.CommandTree") -> None:
    """
    Training / SOP System commands.

    UX goal:
      - Slash command to “open” the tool
      - Then an interactive dropdown + buttons (browse -> details -> mark complete)

    Provides:
      - /trainings         (interactive browser)
      - /training_complete (direct complete by id + modal note)
      - /my_trainings      (progress + launch browser)
    """

    async def _resolve_person_id(interaction: "discord.Interaction") -> Optional[int]:
        person_id, _, err = await ensure_person_by_discord(bot, interaction)
        if err or person_id is None:
            return None
        return int(person_id)

    @tree.command(name="trainings", description="Browse trainings (interactive).")
    @app_commands.describe(
        search="Optional search term (title/slug/description)",
        limit="Items per page (default 15, max 25)",
    )
    async def trainings(interaction: "discord.Interaction", search: Optional[str] = None, limit: int = 15) -> None:
        await interaction.response.defer(ephemeral=True)

        api = _api_client(bot)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        limit_i = _clamp_int(limit, 15, 5, 25)
        q = (search or "").strip()

        # Best-effort: link person so we can show completed status in the UI.
        person_id = await _resolve_person_id(interaction)

        params: Dict[str, Any] = {"limit": limit_i, "offset": 0, "include_inactive": False}
        if q:
            params["q"] = q

        code, text, data = await api_request(api, "GET", "/training/modules", params=params, timeout=20)
        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Training API not available yet (`/training/modules`).",
                ephemeral=True,
            )
            return
        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        items = _parse_training_items(data)
        total_count = _parse_total_count(data)

        # If we have person_id, overlay completion status
        if person_id is not None and items:
            code2, _, data2 = await api_request(
                api,
                "GET",
                "/training/progress",
                params={"person_id": person_id, "limit": 500, "include_inactive": False},
                timeout=20,
            )
            if code2 == 200 and isinstance(data2, dict):
                prog = _parse_training_items(data2)
                done_by_id: Dict[int, TrainingItem] = {it.id: it for it in prog}
                stitched: List[TrainingItem] = []
                for it in items:
                    p = done_by_id.get(it.id)
                    if p is None:
                        stitched.append(it)
                    else:
                        stitched.append(
                            TrainingItem(
                                id=it.id,
                                slug=it.slug,
                                title=it.title,
                                description=it.description,
                                estimated_minutes=it.estimated_minutes,
                                completed=p.completed,
                                completed_at=p.completed_at,
                            )
                        )
                items = stitched

        view = TrainingsBrowserView(
            bot=bot,
            person_id=person_id,
            query=q,
            limit=limit_i,
            offset=0,
            items=items,
            total_hint=total_count,
        )
        list_embed = _build_list_embed(query=q, limit=limit_i, offset=0, items=items, total_hint=total_count)
        await interaction.followup.send(embeds=[list_embed], view=view, ephemeral=True)

    @trainings.autocomplete("search")
    async def trainings_search_autocomplete(
        interaction: "discord.Interaction",
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """
        Fast suggestions reduce typing and make slash UI feel less awful.
        """
        api = _api_client(bot)
        if api is None:
            return []

        q = (current or "").strip()
        if not q:
            return [
                app_commands.Choice(name="SOP", value="sop"),
                app_commands.Choice(name="Onboarding", value="onboarding"),
                app_commands.Choice(name="Voter Registration", value="voter"),
                app_commands.Choice(name="Canvass / Door", value="door"),
                app_commands.Choice(name="Calls / Phones", value="call"),
            ]

        code, _, data = await api_request(
            api,
            "GET",
            "/training/modules",
            params={"limit": 25, "offset": 0, "q": q, "include_inactive": False},
            timeout=10,
        )
        if code != 200 or not isinstance(data, dict):
            return []

        items = _parse_training_items(data)
        choices: List[app_commands.Choice[str]] = []
        for it in items[:25]:
            label = _truncate(f"{it.title} ({it.slug})", 95)
            choices.append(app_commands.Choice(name=label, value=it.slug))
        return choices

    @tree.command(name="training_complete", description="Mark a training module complete (opens a note box).")
    @app_commands.describe(module_id="Training module id")
    async def training_complete(interaction: "discord.Interaction", module_id: int) -> None:
        """
        Keep command simple:
          - user runs /training_complete module_id:123
          - modal pops for optional note
        """
        mid = _clamp_int(module_id, 0, 0, 10_000_000)
        if mid < 1:
            await interaction.response.send_message("❌ module_id must be >= 1.", ephemeral=True)
            return
        await interaction.response.send_modal(TrainingCompleteModal(bot=bot, module_id=mid))

    @tree.command(name="my_trainings", description="Show your training progress (with a button to browse).")
    @app_commands.describe(limit="Max modules to display (default 25)")
    async def my_trainings(interaction: "discord.Interaction", limit: int = 25) -> None:
        await interaction.response.defer(ephemeral=True)

        api = _api_client(bot)
        if api is None:
            await interaction.followup.send("❌ Bot API client is not initialized.", ephemeral=True)
            return

        person_id, _, err = await ensure_person_by_discord(bot, interaction)
        if err or person_id is None:
            await interaction.followup.send(
                "❌ Could not link you to a person record.\n" + (err or ""),
                ephemeral=True,
            )
            return

        limit_i = _clamp_int(limit, 25, 1, 25)

        code, text, data = await api_request(
            api,
            "GET",
            "/training/progress",
            params={"person_id": int(person_id), "limit": 500, "include_inactive": False},
            timeout=20,
        )

        if code in (404, 405):
            await interaction.followup.send(
                "⚠️ Training progress API not available yet (`/training/progress`).",
                ephemeral=True,
            )
            return

        if code != 200 or not isinstance(data, dict):
            await interaction.followup.send(format_api_error(code, text, data), ephemeral=True)
            return

        items = _parse_training_items(data)
        if not items:
            await interaction.followup.send("📘 My Trainings\n- (no modules found)", ephemeral=True)
            return

        completed_count = int(data.get("completed_count") or 0)
        total_count = int(data.get("total_count") or len(items))

        # Compact readout in an embed
        embed = discord.Embed(
            title="📘 My Trainings",
            description=f"Completed **{completed_count} / {total_count}**",
        )

        lines: List[str] = []
        for it in items[:limit_i]:
            mins = _safe_minutes(it.estimated_minutes)
            mins_part = f" • {mins}" if mins else ""
            mark = "✅" if it.completed else "⬜"
            lines.append(f"{mark} **{it.title}** (`{it.slug}`) • id:{it.id}{mins_part}")

        body = "\n".join(lines)
        if len(body) > 1000:
            body = body[:997] + "..."

        embed.add_field(name="Modules", value=body or "- (none)", inline=False)
        view = MyTrainingsLaunchView(bot=bot, person_id=int(person_id), progress_items=items)

        await interaction.followup.send(embeds=[embed], view=view, ephemeral=True)
