from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
}

TEXT_EXTS = {
    ".py", ".md", ".txt", ".toml", ".ini", ".cfg", ".yml", ".yaml", ".json",
}

TEXT_FILENAMES = {".env", ".env.example", "LICENSE", "README.md"}

RE_ROUTER = re.compile(r"APIRouter\(\s*prefix\s*=\s*['\"]([^'\"]+)['\"]", re.M)
RE_FASTAPI_DECORATOR = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", re.M)

RE_ENV = re.compile(r"os\.getenv\(\s*['\"]([^'\"]+)['\"]", re.M)
RE_IMPORT = re.compile(r"^\s*(from\s+[.\w]+\s+import\s+.+|import\s+[\w.]+)", re.M)

RE_SETTINGS_GETATTR = re.compile(r"getattr\(\s*settings\s*,\s*['\"]([^'\"]+)['\"]", re.M)
RE_SETTINGS_DOT = re.compile(r"\bsettings\.([A-Za-z_][A-Za-z0-9_]*)\b")

# extra Discord heuristics
RE_DISCORD_DECORATOR_NAME = re.compile(
    r"@app_commands\.command\((?P<args>[^)]*)\)", re.M
)
RE_TREE_COMMAND = re.compile(
    r"\.command\((?P<args>[^)]*)\)", re.M
)
RE_NAME_KWARG = re.compile(r"name\s*=\s*['\"]([^'\"]+)['\"]", re.M)


@dataclass
class FileInfo:
    path: str
    kind: str
    bytes: int
    lines: int
    imports: List[str]
    env_vars: List[str]
    settings_keys: List[str]
    api_router_prefixes: List[str]
    api_endpoints: List[Dict[str, str]]
    discord_commands: List[Dict[str, str]]  # {name, kind: decorator|tree|fallback, func?}
    sqlmodel_tables: List[str]
    notes: List[str]


@dataclass
class RepoSummary:
    root: str
    file_count: int
    python_files: int
    api_files: int
    discord_files: int
    model_files: int
    endpoints: List[Dict[str, str]]  # {method, full_path, file}
    discord_commands: List[Dict[str, str]]  # {name, file, kind}
    models: List[Dict[str, str]]  # {model, file}
    env_vars: List[str]
    settings_keys: List[str]
    wiring: Dict[str, Any]


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def is_text_file(path: Path) -> bool:
    if path.name in TEXT_FILENAMES:
        return True
    if path.suffix.lower() in TEXT_EXTS:
        return True
    return False


def safe_read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None


def file_kind(path: Path) -> str:
    if path.suffix.lower() == ".py":
        return "python"
    if path.suffix.lower() == ".md":
        return "markdown"
    if is_text_file(path):
        return "text"
    return "other"


def walk_files() -> List[Path]:
    out: List[Path] = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            out.append(Path(root) / f)
    return out


def _extract_discord_command_names_fallback(source: str) -> List[Dict[str, str]]:
    """
    Regex fallback: looks for @app_commands.command(name="x") and tree.command(name="x")
    """
    found: List[Dict[str, str]] = []

    for m in RE_DISCORD_DECORATOR_NAME.finditer(source):
        args = m.group("args") or ""
        nm = RE_NAME_KWARG.search(args)
        if nm:
            found.append({"name": nm.group(1), "kind": "decorator"})

    for m in RE_TREE_COMMAND.finditer(source):
        args = m.group("args") or ""
        nm = RE_NAME_KWARG.search(args)
        if nm:
            found.append({"name": nm.group(1), "kind": "tree"})

    # If decorator exists but no name kwarg, we still mark it as a command presence
    if "@app_commands.command" in source and not any(x["kind"] == "decorator" for x in found):
        found.append({"name": "(unnamed_decorator_command)", "kind": "decorator"})

    return found


def _extract_sqlmodel_tables_ast(source: str) -> List[str]:
    tables: List[str] = []
    try:
        tree = ast.parse(source)
    except Exception:
        return tables

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            has_sqlmodel = any(
                (isinstance(b, ast.Name) and b.id == "SQLModel")
                or (isinstance(b, ast.Attribute) and b.attr == "SQLModel")
                for b in node.bases
            )
            table_kw = False
            for kw in getattr(node, "keywords", []) or []:
                if isinstance(kw, ast.keyword) and kw.arg == "table":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        table_kw = True
            if has_sqlmodel and table_kw:
                tables.append(node.name)

    return sorted(set(tables))


def analyze_file(path: Path) -> FileInfo:
    kind = file_kind(path)
    b = path.stat().st_size
    txt = safe_read_text(path) if kind in ("python", "markdown", "text") else None
    lines = (txt.count("\n") + 1) if txt is not None else 0

    imports: List[str] = []
    env_vars: List[str] = []
    settings_keys: List[str] = []
    api_router_prefixes: List[str] = []
    api_endpoints: List[Dict[str, str]] = []
    discord_commands: List[Dict[str, str]] = []
    sqlmodel_tables: List[str] = []
    notes: List[str] = []

    if txt is not None:
        imports = [m.group(0).strip() for m in RE_IMPORT.finditer(txt)]
        env_vars = sorted(set(RE_ENV.findall(txt)))

        keys = set(RE_SETTINGS_GETATTR.findall(txt))
        for m in RE_SETTINGS_DOT.finditer(txt):
            keys.add(m.group(1))
        settings_keys = sorted(keys)

        if "APIRouter" in txt:
            api_router_prefixes = RE_ROUTER.findall(txt)
            for m in RE_FASTAPI_DECORATOR.finditer(txt):
                api_endpoints.append({"method": m.group(1).upper(), "path": m.group(2)})

        if kind == "python":
            if "app_commands" in txt or ".command(" in txt:
                discord_commands = _extract_discord_command_names_fallback(txt)

            if "SQLModel" in txt:
                sqlmodel_tables = _extract_sqlmodel_tables_ast(txt)

    p = rel(path)
    if p == "app/main.py":
        notes.append("FastAPI boot + router include surface")
    if p == "app/database.py":
        notes.append("DB engine lifecycle + init_db + optional SQLite micro-migrate")
    if p.startswith("app/discord/") and p.endswith("bot.py"):
        notes.append("Discord bot entry: setup_hook registers command modules")
    if p.startswith("app/discord/commands/") and kind == "python":
        notes.append("Discord command module")

    return FileInfo(
        path=rel(path),
        kind=kind,
        bytes=b,
        lines=lines,
        imports=imports,
        env_vars=env_vars,
        settings_keys=settings_keys,
        api_router_prefixes=api_router_prefixes,
        api_endpoints=api_endpoints,
        discord_commands=discord_commands,
        sqlmodel_tables=sqlmodel_tables,
        notes=notes,
    )


def build_tree() -> str:
    lines: List[str] = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_root = Path(root).relative_to(ROOT)
        depth = len(rel_root.parts)
        indent = "  " * depth

        if rel_root == Path("."):
            lines.append(f"{ROOT.name}/")
        else:
            lines.append(f"{indent}{rel_root.name}/")

        for f in sorted(files):
            p = Path(root) / f
            if p.name == ".DS_Store":
                continue
            lines.append(f"{indent}  {f}")
    return "\n".join(lines)


def summarize(files: List[FileInfo]) -> RepoSummary:
    endpoints: List[Dict[str, str]] = []
    discord_cmds: List[Dict[str, str]] = []
    models: List[Dict[str, str]] = []

    all_env = set()
    all_settings = set()

    for fi in files:
        for e in fi.api_endpoints:
            if fi.api_router_prefixes:
                for prefix in fi.api_router_prefixes:
                    endpoints.append({"method": e["method"], "full_path": f"{prefix}{e['path']}", "file": fi.path})
            else:
                endpoints.append({"method": e["method"], "full_path": e["path"], "file": fi.path})

        for c in fi.discord_commands:
            discord_cmds.append({"name": c["name"], "file": fi.path, "kind": c["kind"]})

        for m in fi.sqlmodel_tables:
            models.append({"model": m, "file": fi.path})

        all_env.update(fi.env_vars)
        all_settings.update(fi.settings_keys)

    wiring = {
        "api_entrypoints": ["run_api.py", "app/main.py"],
        "bot_entrypoints": ["run_bot.py", "app/discord/bot.py"],
        "model_registry": ["app/models/__init__.py"],
        "db": ["app/database.py"],
        "routers_dir": "app/api/",
        "discord_commands_dir": "app/discord/commands/",
        "models_dir": "app/models/",
    }

    return RepoSummary(
        root=str(ROOT),
        file_count=len(files),
        python_files=sum(1 for f in files if f.kind == "python"),
        api_files=sum(1 for f in files if f.path.startswith("app/api/") and f.kind == "python"),
        discord_files=sum(1 for f in files if f.path.startswith("app/discord/") and f.kind == "python"),
        model_files=sum(1 for f in files if f.path.startswith("app/models/") and f.kind == "python"),
        endpoints=sorted(endpoints, key=lambda x: (x["full_path"], x["method"], x["file"])),
        discord_commands=sorted(discord_cmds, key=lambda x: (x["name"], x["file"])),
        models=sorted(models, key=lambda x: (x["model"], x["file"])),
        env_vars=sorted(all_env),
        settings_keys=sorted(all_settings),
        wiring=wiring,
    )


def write_reports(files: List[FileInfo], summary: RepoSummary) -> None:
    json_out = {"summary": asdict(summary), "files": [asdict(f) for f in files]}
    (ROOT / "REPO_MAP.json").write_text(json.dumps(json_out, indent=2), encoding="utf-8")

    md: List[str] = []
    md.append(f"# Repo Map — {ROOT.name}\n")

    md.append("## Tree\n```text")
    md.append(build_tree())
    md.append("```\n")

    md.append("## Inventory Summary\n")
    md.append(f"- Files: **{summary.file_count}**")
    md.append(f"- Python: **{summary.python_files}**")
    md.append(f"- API modules: **{summary.api_files}**")
    md.append(f"- Discord modules: **{summary.discord_files}**")
    md.append(f"- Model modules: **{summary.model_files}**\n")

    md.append("## API Endpoints (discovered)\n")
    md.append("| Method | Path | File |")
    md.append("|---|---|---|")
    for e in summary.endpoints:
        md.append(f"| {e['method']} | `{e['full_path']}` | `{e['file']}` |")
    md.append("")

    md.append("## Discord Commands (discovered)\n")
    if summary.discord_commands:
        md.append("| Name | Kind | File |")
        md.append("|---|---|---|")
        for c in summary.discord_commands:
            md.append(f"| `{c['name']}` | {c['kind']} | `{c['file']}` |")
        md.append("")
    else:
        md.append("_No Discord commands detected (unexpected)._\n")

    md.append("## SQLModel Tables (discovered)\n")
    md.append("| Table Class | File |")
    md.append("|---|---|")
    for m in summary.models:
        md.append(f"| `{m['model']}` | `{m['file']}` |")
    md.append("")

    md.append("## Settings keys referenced (scan)\n```text")
    md.append("\n".join(summary.settings_keys) if summary.settings_keys else "(none)")
    md.append("```\n")

    md.append("## Env vars referenced via os.getenv (scan)\n```text")
    md.append("\n".join(summary.env_vars) if summary.env_vars else "(none)")
    md.append("```\n")

    (ROOT / "REPO_MAP.md").write_text("\n".join(md), encoding="utf-8")
    print("Wrote REPO_MAP.md and REPO_MAP.json")


def main() -> None:
    paths = walk_files()
    files: List[FileInfo] = []
    for p in paths:
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        files.append(analyze_file(p))

    summary = summarize(files)
    write_reports(files, summary)


if __name__ == "__main__":
    main()
