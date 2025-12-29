from __future__ import annotations

import asyncio
import logging
import os
import httpx
import discord
from discord import app_commands
from ..config import settings

API_BASE = "http://127.0.0.1:8000"

class DashboardBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Sync slash commands globally. For faster iteration, you can change this to a guild-only sync later.
        await self.tree.sync()

bot = DashboardBot()

@bot.tree.command(name="ping", description="Sanity check: bot is alive.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong. Bot is online.", ephemeral=True)

@bot.tree.command(name="impact", description="Get impact reach score for a person_id.")
@app_commands.describe(person_id="Internal person_id (integer)")
async def impact(interaction: discord.Interaction, person_id: int):
    await interaction.response.defer(ephemeral=True)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{API_BASE}/people/{person_id}/impact")
    if r.status_code != 200:
        await interaction.followup.send(f"❌ Error: {r.text}", ephemeral=True)
        return
    data = r.json()
    msg = (
        f"📈 Impact for person_id={person_id}\n"
        f"- Downstream people: {data['downstream_people']}\n"
        f"- Downstream voters: {data['downstream_voters']}\n"
        f"- Impact Reach Score: {data['impact_reach_score']}"
    )
    await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(name="census", description="Census lookup: county population (ACS). Requires CENSUS_API_KEY in .env.")
@app_commands.describe(state_fips="State FIPS (AR = 05)", county_fips="County FIPS (3 digits, e.g., Pulaski = 119)", year="ACS year, default 2023")
async def census(interaction: discord.Interaction, state_fips: str, county_fips: str, year: str = "2023"):
    await interaction.response.defer(ephemeral=True)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{API_BASE}/external/census/county_population", params={"state_fips": state_fips, "county_fips": county_fips, "year": year})
    if r.status_code != 200:
        await interaction.followup.send(f"❌ Error: {r.text}", ephemeral=True)
        return
    data = r.json()
    await interaction.followup.send(
        f"🏛️ Census ACS {data['year']}\n{data['name']}\nTotal population: {data['total_population']}",
        ephemeral=True
    )

@bot.tree.command(name="bls", description="BLS lookup: series data. Requires BLS_API_KEY in .env.")
@app_commands.describe(series_id="BLS series id, e.g., LAUCN050010000000003")
async def bls(interaction: discord.Interaction, series_id: str, start_year: str = "2022", end_year: str = "2025"):
    await interaction.response.defer(ephemeral=True)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API_BASE}/external/bls/series", params={"series_id": series_id, "start_year": start_year, "end_year": end_year})
    if r.status_code != 200:
        await interaction.followup.send(f"❌ Error: {r.text}", ephemeral=True)
        return
    data = r.json()
    s = data["results"]
    title = s.get("seriesID", series_id)
    points = s.get("data", [])[:5]  # show first 5 points
    lines = [f"{p.get('year')}-{p.get('periodName')}: {p.get('value')}" for p in points]
    await interaction.followup.send(
        "📊 BLS series\n"
        f"{title}\n"
        + "\n".join(lines)
        + ("\n…(showing 5 points)" if len(points) == 5 else ""),
        ephemeral=True
    )

def run_bot() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in .env")
    bot.run(settings.discord_bot_token)
