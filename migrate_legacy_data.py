import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import account_store


def read_legacy_memories(path):
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path.name} on line {line_number}.") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Invalid memory record in {path.name} on line {line_number}.")
            records.append(record)
    return records


def backup_database(path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.name}.legacy-backup-{stamp}")
    with sqlite3.connect(path) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    return backup_path


def main():
    account_store.initialize()
    email = input("Email of the verified account that should receive legacy local data: ")
    try:
        user = account_store.get_user_by_email(email)
    except ValueError as exc:
        print(exc)
        return 1
    if user is None or not user["email_verified"]:
        print("No verified account matches that email.")
        return 1

    database = account_store.database_path().resolve()
    memories_path = Path(__file__).resolve().with_name("memory.jsonl")
    try:
        memories = read_legacy_memories(memories_path)
    except ValueError as exc:
        print(exc)
        return 1

    counts = account_store.legacy_data_counts()
    print(
        f"This will assign {counts['tasks']} unowned task(s), "
        f"{counts['memories']} unowned SQLite memory record(s), and "
        f"{len(memories)} JSONL memory record(s) to {user['email']}."
    )
    confirmation = input(f"Type IMPORT {user['email']} to continue: ")
    if confirmation != f"IMPORT {user['email']}":
        print("No data was changed.")
        return 0

    backup_path = backup_database(database)
    imported = account_store.import_legacy_data(user["id"], memories)
    print(
        f"Imported {imported['tasks']} task(s) and {imported['memories']} memory record(s). "
        f"Database backup: {backup_path}"
    )
    print("memory.jsonl was left unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
