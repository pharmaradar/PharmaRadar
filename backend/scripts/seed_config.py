#!/usr/bin/env python
"""Copy the monitoring configuration from one deployment into another.

A new deployment starts with whatever the migrations seed and nothing else, so
the targets, tracked accounts, burning topics and congresses that were entered
by hand would have to be typed again — slowly, and with another chance to
mistype a Facebook slug (commit 589d129 exists because that already happened).

Only CONFIGURATION travels: who we watch, and what we ask about them. Nothing
the pipeline produced comes along — no posts, insights or reports, and none of
an account's scan health or cached analysis. Those describe a corpus that lives
in the source database; written into an empty deployment they would state a post
count and an AI summary for content that is not there, which reads as a finding
rather than as absence. The destination generates its own.

Rows are matched on a natural key, never on id: migrations 024/028/030/032 seed
some of these rows themselves, so the destination already holds a subset under
different ids. Existing rows are left alone unless --update-existing is passed,
because the destination is the live system and someone may have edited them
there.

    # from backend/, reading the source deployment's .env
    python scripts/seed_config.py export config.json

    # against the destination: prints a plan and writes nothing
    TARGET_DATABASE_URL='postgresql://…' python scripts/seed_config.py import config.json

    # same command, once the plan looks right
    TARGET_DATABASE_URL='postgresql://…' python scripts/seed_config.py import config.json --commit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text                                    # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine         # noqa: E402


@dataclass(frozen=True)
class Spec:
    """One table's config surface.

    `columns` is an allowlist rather than "everything except…" on purpose: a
    later migration that adds a generated column would otherwise start copying
    it silently.
    """

    table: str
    columns: tuple[str, ...]
    key: tuple[str, ...]      # natural key used to match rows across deployments
    label: str                # column to name a row in the printed plan


SPECS: tuple[Spec, ...] = (
    Spec(
        table="targets",
        # Every column here is config. created_at/updated_at stay behind so the
        # destination timestamps when IT learned about the target.
        columns=("name", "known_urls", "notes", "disease_area", "target_type",
                 "twitter_handle", "linkedin_url", "source_scope", "active"),
        key=("name",),
        label="name",
    ),
    Spec(
        table="tracked_accounts",
        # Dropped: last_scanned_at, last_scan_status, post_count and every
        # analysis_* column. They are measurements of the source corpus.
        columns=("platform", "handle", "url", "label", "category",
                 "full_name", "notes", "role", "active"),
        key=("platform", "handle"),
        label="handle",
    ),
    Spec(
        table="burning_topics",
        # created_by is dropped: it is a FK to users.id in the SOURCE, and that
        # id belongs to a different person in the destination.
        columns=("name", "description", "language_filter", "period_days",
                 "exclusion_words", "restriction_terms", "is_active"),
        key=("name",),
        label="name",
    ),
    Spec(
        table="congresses",
        columns=("name", "hashtags", "start_date", "end_date",
                 "disease_area", "is_active"),
        key=("name",),
        label="name",
    ),
)

# Carried from app_settings: what we monitor. Deliberately NOT carried —
#   api_key                       a credential, and the destination uses env vars
#   llm_provider/llm_model/*_url  per-deployment infrastructure
#   cron_*, social_scan_enabled,  schedules cost money when they fire; they get
#   auto_synthesis_*              switched on deliberately in the destination
SETTINGS_FIELDS = ("social_keywords", "social_platforms", "social_window_days",
                   "social_max_per_query", "social_include_kols",
                   "social_lang_filter", "facebook_page_urls")

# DATE columns: asyncpg binds by type, so these must go back to date objects
# rather than the ISO strings JSON gives us.
DATE_FIELDS = frozenset({"start_date", "end_date"})


def _key_of(row: dict, spec: Spec) -> tuple:
    """Natural key, case-folded.

    The unique constraints are case-sensitive, so a differently-cased handle
    would insert cleanly and leave two rows for one account. Folding here means
    such a row is treated as already present and skipped — the conservative
    direction when the destination is live.
    """
    out = []
    for column in spec.key:
        value = row.get(column)
        out.append(value.strip().casefold() if isinstance(value, str) else value)
    return tuple(out)


def _coerce(row: dict) -> dict:
    return {k: (date.fromisoformat(v) if k in DATE_FIELDS and isinstance(v, str) else v)
            for k, v in row.items()}


async def export(destination: Path) -> None:
    from app.config import get_settings

    engine = create_async_engine(get_settings().async_database_url)
    payload: dict = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "note": "Configuration only — no posts, insights, reports or analyses.",
    }
    try:
        async with engine.connect() as conn:
            for spec in SPECS:
                columns = ", ".join(spec.columns)
                result = await conn.execute(
                    text(f"SELECT {columns} FROM {spec.table} ORDER BY {spec.key[0]}"))
                rows = [dict(r._mapping) for r in result]
                for row in rows:
                    for field in DATE_FIELDS & row.keys():
                        if isinstance(row[field], date):
                            row[field] = row[field].isoformat()
                payload[spec.table] = rows

            # Questions ride with their congress: their FK is a source-local id,
            # so they can only be placed once the congress has been re-inserted.
            by_name = {c["name"]: c for c in payload["congresses"]}
            questions = await conn.execute(text(
                "SELECT c.name, q.question_text FROM congress_questions q "
                "JOIN congresses c ON c.id = q.congress_id ORDER BY q.id"))
            for congress in by_name.values():
                congress["questions"] = []
            for name, question_text in questions:
                if name in by_name:
                    by_name[name]["questions"].append(question_text)

            fields = ", ".join(SETTINGS_FIELDS)
            settings_row = (await conn.execute(
                text(f"SELECT {fields} FROM app_settings ORDER BY id LIMIT 1"))).first()
            payload["app_settings"] = dict(settings_row._mapping) if settings_row else {}
    finally:
        await engine.dispose()

    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"Wrote {destination}")
    for spec in SPECS:
        print(f"  {spec.table:<20} {len(payload[spec.table]):>4}")
    total_questions = sum(len(c["questions"]) for c in payload["congresses"])
    print(f"  {'congress_questions':<20} {total_questions:>4}")
    print(f"  {'app_settings':<20} {len(payload['app_settings']):>4} fields")


async def import_config(source: Path, database_url: str, *, commit: bool,
                        update_existing: bool) -> None:
    payload = json.loads(source.read_text())
    engine = create_async_engine(database_url)
    plan: list[str] = []
    created = updated = skipped = 0

    try:
        async with engine.begin() as conn:
            for spec in SPECS:
                rows = payload.get(spec.table) or []
                key_columns = ", ".join(spec.key)
                existing = {
                    _key_of(dict(r._mapping), spec): r._mapping["id"]
                    for r in await conn.execute(
                        text(f"SELECT id, {key_columns} FROM {spec.table}"))
                }

                for row in rows:
                    row = _coerce(row)
                    name = row.get(spec.label)
                    row_id = existing.get(_key_of(row, spec))

                    if row_id is not None and not update_existing:
                        skipped += 1
                        continue
                    if row_id is not None:
                        assignments = ", ".join(f"{c} = :{c}" for c in spec.columns)
                        await conn.execute(
                            text(f"UPDATE {spec.table} SET {assignments} WHERE id = :_id"),
                            {**row, "_id": row_id})
                        plan.append(f"  update  {spec.table:<18} {name}")
                        updated += 1
                        continue

                    columns = ", ".join(spec.columns)
                    values = ", ".join(f":{c}" for c in spec.columns)
                    new_id = (await conn.execute(
                        text(f"INSERT INTO {spec.table} ({columns}) "
                             f"VALUES ({values}) RETURNING id"), row)).scalar_one()
                    existing[_key_of(row, spec)] = new_id
                    plan.append(f"  create  {spec.table:<18} {name}")
                    created += 1

                    if spec.table == "congresses":
                        for question in row.get("questions") or []:
                            await conn.execute(
                                text("INSERT INTO congress_questions "
                                     "(congress_id, question_text) VALUES (:c, :q)"),
                                {"c": new_id, "q": question})
                            plan.append(f"  create  {'congress_question':<18} {question[:60]}")
                            created += 1

            settings = payload.get("app_settings") or {}
            if settings:
                current = (await conn.execute(text(
                    f"SELECT id, {', '.join(SETTINGS_FIELDS)} FROM app_settings "
                    "ORDER BY id LIMIT 1"))).first()
                if current is None:
                    plan.append("  skip    app_settings       no row yet — boot the backend first")
                else:
                    # Only fill what the destination has left empty, so a value
                    # tuned in production is not reverted to the dev one.
                    changes = {
                        field: value for field, value in settings.items()
                        if value not in (None, "")
                        and (update_existing or current._mapping[field] in (None, ""))
                        and current._mapping[field] != value
                    }
                    if changes:
                        assignments = ", ".join(f"{f} = :{f}" for f in changes)
                        await conn.execute(
                            text(f"UPDATE app_settings SET {assignments} WHERE id = :_id"),
                            {**changes, "_id": current._mapping["id"]})
                        plan.append(f"  update  {'app_settings':<18} {', '.join(changes)}")
                        updated += 1
                    else:
                        skipped += 1

            if not commit:
                raise _DryRun()
    except _DryRun:
        pass
    finally:
        await engine.dispose()

    print("\n".join(plan) if plan else "  (nothing to do)")
    verb = "Applied" if commit else "Would apply"
    print(f"\n{verb}: {created} created, {updated} updated, {skipped} left alone")
    if not commit:
        print("Dry run — nothing was written. Re-run with --commit to apply.")


class _DryRun(Exception):
    """Rolls the transaction back so a dry run costs the destination nothing."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    exporter = sub.add_parser("export", help="write this deployment's config to JSON")
    exporter.add_argument("file", type=Path)

    importer = sub.add_parser("import", help="apply a config JSON to another deployment")
    importer.add_argument("file", type=Path)
    importer.add_argument("--database-url", default=os.getenv("TARGET_DATABASE_URL"),
                          help="destination DB (default: $TARGET_DATABASE_URL)")
    importer.add_argument("--commit", action="store_true",
                          help="actually write; without it the run is a dry run")
    importer.add_argument("--update-existing", action="store_true",
                          help="also overwrite rows already present in the destination")
    args = parser.parse_args()

    if args.command == "export":
        asyncio.run(export(args.file))
        return

    url = args.database_url
    if not url:
        parser.error("set TARGET_DATABASE_URL or pass --database-url")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # The host is worth printing and the password is not: this command gets run
    # against production by copy-paste, and naming the destination out loud is
    # the last chance to notice it is the wrong one.
    print(f"Destination: {url.split('@')[-1]}\n")
    asyncio.run(import_config(args.file, url, commit=args.commit,
                              update_existing=args.update_existing))


if __name__ == "__main__":
    main()
