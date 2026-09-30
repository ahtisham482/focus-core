"""Timesheets (RescueTime-style).

How it works:
1. The system SUGGESTS time blocks: consecutive tracked activities with
   the same category are merged into one block (see suggest_blocks).
2. You ACCEPT a suggestion (optionally assigning a project/task), EDIT it,
   DELETE it, or ADD a manual entry for time the tracker missed.
3. LOCK a day when the timesheet is final -- locked entries cannot be
   edited or deleted anymore.
4. EXPORT any date range to CSV.

Usage:
    python -m focuscore.timesheet suggest --day 2026-09-25
    python -m focuscore.timesheet export --from 2026-09-20 --to 2026-09-26 \\
        --out week.csv
"""

import argparse
import csv
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from . import store

# Blocks shorter than this are noise (e.g. a 30-second app switch) and
# are not suggested. Documented constant, not a guess about the user.
MIN_BLOCK_MINUTES = 5

# Two same-category activities merge into one block only when the gap
# between them is at most this long. A longer gap means the user did
# something else (or nothing) in between.
MERGE_GAP_MINUTES = 5

ENTRY_STATUSES = ("accepted", "edited", "added")


def _parse_ts(value):
    """Parse a stored timestamp to a naive local datetime."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def suggest_blocks(day, db_path=None, min_minutes=MIN_BLOCK_MINUTES):
    """Suggest timesheet blocks for one day.

    Merge rule: walk the day's stored activities in time order and merge
    consecutive activities that share the SAME category into one block,
    as long as the gap between them is at most MERGE_GAP_MINUTES.
    Blocks shorter than min_minutes are dropped as noise.

    Returns a list of dicts:
    {"start_ts", "end_ts", "minutes", "category",
     "app" (most common app in the block),
     "title_hint" (title of the block's first activity)}.
    """
    events = store.get_day_activities(day, path=db_path)
    blocks = []
    current = None  # [start, end, category, apps Counter, first title]

    def flush():
        if current is None:
            return
        start, end, category, apps, first_title = current
        minutes = (end - start).total_seconds() / 60.0
        if minutes >= min_minutes:
            top_app = apps.most_common(1)[0][0] if apps else ""
            blocks.append({
                "start_ts": start.isoformat(timespec="minutes"),
                "end_ts": end.isoformat(timespec="minutes"),
                "minutes": round(minutes, 1),
                "category": category,
                "app": top_app,
                "title_hint": first_title,
            })

    for event in events:
        try:
            start = _parse_ts(event.get("ts"))
        except (ValueError, TypeError):
            continue
        duration = float(event.get("duration") or 0)
        if duration <= 0:
            continue
        end = start + timedelta(seconds=duration)
        category = event.get("category") or "Uncategorized"
        app = event.get("app") or ""
        title = event.get("title") or ""

        if (current is not None and category == current[2]
                and (start - current[1]).total_seconds() / 60.0
                <= MERGE_GAP_MINUTES):
            current[1] = max(current[1], end)
            current[3][app] += duration
        else:
            flush()
            current = [start, end, category, Counter({app: duration}), title]
    flush()
    return blocks


def accept_suggestion(block, day, project_id=None, task="", note="",
                      db_path=None):
    """Turn a suggested block into a timesheet entry (status 'accepted').

    A locked day refuses new entries. Returns the new entry id or
    {"error": ...}.
    """
    if store.day_is_locked(day, path=db_path):
        return {"error": "This day is locked -- entries cannot be added."}
    return store.create_entry(
        day, block["start_ts"], block["end_ts"], block["minutes"],
        block["category"], app=block.get("app", ""),
        title=block.get("title_hint", ""), project_id=project_id,
        task=task, note=note, status="accepted", path=db_path)


def _validate_entry_times(start_ts, end_ts):
    """Parse and sanity-check entry bounds; returns (start, end, minutes)
    or {"error": ...}."""
    try:
        start = _parse_ts(start_ts)
        end = _parse_ts(end_ts)
    except (ValueError, TypeError):
        return {"error": "Start/end must look like 2026-09-25T09:00."}
    minutes = (end - start).total_seconds() / 60.0
    if minutes <= 0:
        return {"error": "End must be after start."}
    if minutes > 24 * 60:
        return {"error": "An entry cannot be longer than 24 hours."}
    return (start, end, round(minutes, 1))


def add_entry(day, start_ts, end_ts, category, app="", title="",
              project_id=None, task="", note="", db_path=None):
    """Add a manual entry for time the tracker missed (status 'added').

    A locked day refuses new entries. Returns the new entry or
    {"error": ...}.
    """
    if store.day_is_locked(day, path=db_path):
        return {"error": "This day is locked -- entries cannot be added."}
    if not (category or "").strip():
        return {"error": "Please choose a category."}
    checked = _validate_entry_times(start_ts, end_ts)
    if isinstance(checked, dict):
        return checked
    _start, _end, minutes = checked
    entry_id = store.create_entry(
        day, start_ts, end_ts, minutes, category.strip(), app=app,
        title=title, project_id=project_id, task=task, note=note,
        status="added", path=db_path)
    return store.get_entry(entry_id, path=db_path)


def edit_entry(entry_id, project_id=None, task=None, note=None,
               start_ts=None, end_ts=None, category=None, db_path=None):
    """Edit an entry (status becomes 'edited'). Locked entries refuse.

    Only the arguments that are not None are changed. Returns the updated
    entry or {"error": ...}.
    """
    entry = store.get_entry(entry_id, path=db_path)
    if not entry:
        return {"error": "Unknown entry."}
    if entry["locked"]:
        return {"error": "This day is locked -- entries cannot be changed."}

    fields = {}
    if project_id is not None:
        fields["project_id"] = project_id or None
    if task is not None:
        fields["task"] = task or ""
    if note is not None:
        fields["note"] = note or ""
    if category is not None:
        if not category.strip():
            return {"error": "Please choose a category."}
        fields["category"] = category.strip()

    new_start = start_ts if start_ts is not None else entry["start_ts"]
    new_end = end_ts if end_ts is not None else entry["end_ts"]
    if start_ts is not None or end_ts is not None:
        checked = _validate_entry_times(new_start, new_end)
        if isinstance(checked, dict):
            return checked
        _s, _e, minutes = checked
        fields["start_ts"] = new_start
        fields["end_ts"] = new_end
        fields["minutes"] = minutes
        # Keep the day in sync when the time moves across midnight.
        fields["day"] = _s.date().isoformat()

    fields["status"] = "edited"
    store.update_entry(entry_id, fields, path=db_path)
    return store.get_entry(entry_id, path=db_path)


def delete_entry(entry_id, db_path=None):
    """Delete an entry. Locked entries refuse. Returns {} or {"error": ...}."""
    entry = store.get_entry(entry_id, path=db_path)
    if not entry:
        return {"error": "Unknown entry."}
    if entry["locked"]:
        return {"error": "This day is locked -- entries cannot be deleted."}
    try:
        store.delete_entry(entry_id, path=db_path)
    except sqlite3.IntegrityError:
        # Roadmap 1.7: FK enforcement is live; an entry referenced by
        # an invoice line (ON DELETE RESTRICT) cannot be removed.
        return {"error": "This entry is on an invoice, so it cannot be deleted."}
    return {}


def lock_day(day, db_path=None):
    """Finalize a day: mark its entries 'locked' and lock the day itself.

    Locked entries cannot be edited or deleted, and no new entries can be
    added. Locking is permanent. Returns {"locked": count}.
    """
    entries = store.list_entries(day=day, path=db_path)
    for entry in entries:
        if not entry["locked"]:
            store.update_entry(entry["id"], {"status": "locked"},
                               path=db_path)
    store.lock_day(day, path=db_path)
    return {"locked": len(entries)}


CSV_COLUMNS = ["date", "start", "end", "minutes", "category", "app",
               "project", "client", "task", "note", "status"]


def write_csv_rows(entries, handle):
    """Write timesheet entry dicts (see store.list_entries) as CSV."""
    writer = csv.writer(handle)
    writer.writerow(CSV_COLUMNS)
    for entry in entries:
        writer.writerow([
            entry["day"], entry["start_ts"], entry["end_ts"],
            entry["minutes"], entry["category"], entry["app"],
            entry["project_name"], entry["client"], entry["task"],
            entry["note"], entry["status"],
        ])


def export_csv(day_from, day_to, file_path, db_path=None):
    """Export entries in a date range to CSV. Returns the row count."""
    entries = store.list_entries(day_from=day_from, day_to=day_to,
                                 path=db_path)
    with open(file_path, "w", newline="", encoding="utf-8") as handle:
        write_csv_rows(entries, handle)
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description="Focus Core timesheets.")
    parser.add_argument("--db", default=None,
                        help="SQLite file to use (default: focuscore.db "
                             "next to the code).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_suggest = sub.add_parser("suggest", help="Suggest blocks for a day.")
    p_suggest.add_argument("--day", required=True,
                           help="Day as YYYY-MM-DD.")
    p_suggest.add_argument("--min-minutes", type=float,
                           default=MIN_BLOCK_MINUTES,
                           help="Drop blocks shorter than this (default: 5).")

    p_export = sub.add_parser("export", help="Export a date range to CSV.")
    p_export.add_argument("--from", dest="day_from", required=True,
                          help="First day as YYYY-MM-DD.")
    p_export.add_argument("--to", dest="day_to", required=True,
                          help="Last day as YYYY-MM-DD.")
    p_export.add_argument("--out", required=True,
                          help="CSV file to write.")
    args = parser.parse_args()

    if args.command == "suggest":
        try:
            datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            parser.error("--day must look like YYYY-MM-DD")
        blocks = suggest_blocks(args.day, db_path=args.db,
                                min_minutes=args.min_minutes)
        if not blocks:
            print("No blocks suggested for %s." % args.day)
            return
        for block in blocks:
            print("%s - %s | %5.1f min | %-24s | %s%s" % (
                block["start_ts"][11:], block["end_ts"][11:],
                block["minutes"], block["category"], block["app"],
                (" | " + block["title_hint"][:40]
                 if block["title_hint"] else "")))

    elif args.command == "export":
        for label, value in (("--from", args.day_from),
                             ("--to", args.day_to)):
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                parser.error("%s must look like YYYY-MM-DD" % label)
        count = export_csv(args.day_from, args.day_to, args.out,
                           db_path=args.db)
        print("Wrote %d entr%s to %s." % (
            count, "y" if count == 1 else "ies", args.out))


if __name__ == "__main__":
    main()
