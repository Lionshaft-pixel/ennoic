import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

import database as database_layer


TASK_FIELDS = {"title", "description", "project", "due_date", "priority", "status", "parent_task_id"}
EDITABLE_TASK_FIELDS = {"title", "description", "project", "due_date", "priority"}
PRIORITIES = {"low", "medium", "high"}
STATUSES = {"open", "completed"}


def _connect():
    connection = database_layer.connect()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            project TEXT,
            due_date TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'open',
            parent_task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            user_id TEXT,
            CHECK (priority IN ('low', 'medium', 'high')),
            CHECK (status IN ('open', 'completed'))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS task_proposals (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            task_json TEXT NOT NULL,
            candidate_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            user_id TEXT
        )
        """
    )
    for table in ("tasks", "task_proposals"):
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "user_id" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
    connection.commit()
    return connection


@contextmanager
def _connection():
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _task_from_row(row):
    if row is None:
        return None
    task = dict(row)
    task.pop("user_id", None)
    return task


def _validate_date(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Deadline must use YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Deadline must be a valid calendar date.") from exc
    return value


def _validate_fields(data, *, creating):
    if not isinstance(data, dict):
        raise ValueError("Task details must be an object.")
    unknown_fields = set(data) - TASK_FIELDS
    if unknown_fields:
        raise ValueError("Task contains unsupported fields.")

    values = dict(data)
    if creating and "title" not in values:
        raise ValueError("A task title is required.")
    if "title" in values:
        title = values["title"]
        if not isinstance(title, str) or not title.strip():
            raise ValueError("A task title is required.")
        values["title"] = title.strip()

    for field in ("description", "project"):
        if field in values and values[field] is not None:
            if not isinstance(values[field], str):
                raise ValueError(f"{field.capitalize()} must be text.")
            values[field] = values[field].strip() or None

    if "due_date" in values:
        values["due_date"] = _validate_date(values["due_date"])
    if "priority" in values:
        if creating and values["priority"] is None:
            values["priority"] = "medium"
        if not isinstance(values["priority"], str):
            raise ValueError("Priority must be low, medium, or high.")
        if values["priority"] not in PRIORITIES:
            raise ValueError("Priority must be low, medium, or high.")
    if "status" in values:
        if not isinstance(values["status"], str) or values["status"] not in STATUSES:
            raise ValueError("Status must be open or completed.")
    if "parent_task_id" in values and values["parent_task_id"] is not None:
        if not isinstance(values["parent_task_id"], str) or not values["parent_task_id"].strip():
            raise ValueError("Parent task ID must be text.")
        values["parent_task_id"] = values["parent_task_id"].strip()
    return values


def _normalized_task_title(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if isinstance(value, str) else ""


def _task_title_terms(title):
    stop_words = {
        "a", "an", "and", "at", "by", "for", "from", "in", "into", "of", "on", "or",
        "the", "to", "with", "about", "please", "task",
    }
    return {
        term for term in _normalized_task_title(title).split()
        if term not in stop_words and len(term) > 1
    }


def _possible_duplicate_candidates(proposed_title, existing_tasks):
    normalized_title = _normalized_task_title(proposed_title)
    proposed_terms = _task_title_terms(proposed_title)
    if not normalized_title:
        return []

    ranked = []
    for task in existing_tasks:
        normalized_existing = _normalized_task_title(task["title"])
        existing_terms = _task_title_terms(task["title"])
        if not normalized_existing or not existing_terms:
            continue
        shared_terms = proposed_terms & existing_terms
        union_terms = proposed_terms | existing_terms
        jaccard = len(shared_terms) / len(union_terms) if union_terms else 0
        overlap = len(shared_terms) / min(len(proposed_terms), len(existing_terms)) if proposed_terms and existing_terms else 0
        similarity = SequenceMatcher(None, normalized_title, normalized_existing).ratio()
        exact = normalized_title == normalized_existing
        if exact or similarity >= 0.82 or (
            len(shared_terms) >= 2 and (jaccard >= 0.42 or overlap >= 0.75)
        ):
            ranked.append((exact, similarity, jaccard, task))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item[3] for item in ranked[:3]]


def create_tasks(tasks, parent_task_id=None, *, user_id=None):
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("At least one task is required.")
    if len(tasks) > 20:
        raise ValueError("A maximum of 20 tasks can be created at once.")

    prepared = []
    for task in tasks:
        values = _validate_fields(task, creating=True)
        values.setdefault("description", None)
        values.setdefault("project", None)
        values.setdefault("due_date", None)
        values.setdefault("priority", "medium")
        values.setdefault("status", "open")
        values["parent_task_id"] = parent_task_id or values.get("parent_task_id")
        prepared.append(values)

    now = datetime.now(timezone.utc).isoformat()
    created = []
    try:
        with _connection() as connection:
            if parent_task_id is not None and connection.execute(
                "SELECT 1 FROM tasks WHERE id = ? AND user_id IS ?",
                (parent_task_id, user_id),
            ).fetchone() is None:
                raise ValueError("The parent task could not be found.")
            for values in prepared:
                task_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO tasks
                        (id, title, description, project, due_date, priority, status,
                         parent_task_id, created_at, updated_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        values["title"],
                        values["description"],
                        values["project"],
                        values["due_date"],
                        values["priority"],
                        values["status"],
                        values["parent_task_id"],
                        now,
                        now,
                        user_id,
                    ),
                )
                created.append(
                    _task_from_row(connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
                )
    except database_layer.INTEGRITY_ERRORS as exc:
        raise ValueError("The parent task could not be found.") from exc
    return created


def create_task(data, *, user_id=None):
    return create_tasks([data], user_id=user_id)[0]


def stage_task_batch(tasks, conversation_id, *, user_id=None):
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("At least one task is required.")
    if len(tasks) > 20:
        raise ValueError("A maximum of 20 tasks can be created at once.")
    if not isinstance(conversation_id, str) or len(conversation_id) > 200:
        raise ValueError("A valid conversation ID is required.")

    prepared = []
    for raw_task in tasks:
        if not isinstance(raw_task, dict):
            raise ValueError("The task request could not be staged.")
        proposed_input = {
            field: value
            for field, value in raw_task.items()
            if field != "priority" or value is not None
        }
        proposed_values = _validate_fields(proposed_input, creating=False)
        if raw_task.get("priority", "not-provided") is None:
            proposed_values["priority"] = None
        values = _validate_fields(raw_task, creating=True)
        values.setdefault("description", None)
        values.setdefault("project", None)
        values.setdefault("due_date", None)
        values.setdefault("priority", "medium")
        values.setdefault("status", "open")
        values["parent_task_id"] = values.get("parent_task_id")
        prepared.append((values, proposed_values))

    now = datetime.now(timezone.utc).isoformat()
    created = []
    proposals = []
    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for values, proposed_values in prepared:
            existing_tasks = [
                _task_from_row(row)
                for row in connection.execute(
                    "SELECT * FROM tasks WHERE user_id IS ?",
                    (user_id,),
                ).fetchall()
            ]
            candidates = _possible_duplicate_candidates(values["title"], existing_tasks)
            candidate_ids = [candidate["id"] for candidate in candidates]
            if not candidate_ids:
                task_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO tasks
                        (id, title, description, project, due_date, priority, status,
                         parent_task_id, created_at, updated_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        values["title"],
                        values["description"],
                        values["project"],
                        values["due_date"],
                        values["priority"],
                        values["status"],
                        values["parent_task_id"],
                        now,
                        now,
                        user_id,
                    ),
                )
                created.append(_task_from_row(connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()))
                continue

            proposal_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO task_proposals (id, conversation_id, task_json, candidate_ids_json, created_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    conversation_id,
                    json.dumps(proposed_values, ensure_ascii=False),
                    json.dumps(candidate_ids),
                    now,
                    user_id,
                ),
            )
            proposals.append(
                {
                    "id": proposal_id,
                    "task": proposed_values,
                    "candidates": candidates,
                }
            )
    return {"created": created, "proposals": proposals}


def list_task_proposals(conversation_id, *, user_id=None):
    if not isinstance(conversation_id, str):
        return []
    with _connection() as connection:
        rows = connection.execute(
            "SELECT id, task_json, candidate_ids_json FROM task_proposals "
            "WHERE conversation_id = ? AND user_id IS ? ORDER BY created_at",
            (conversation_id, user_id),
        ).fetchall()
        result = []
        for row in rows:
            candidate_ids = json.loads(row["candidate_ids_json"])
            if not candidate_ids:
                continue
            placeholders = ", ".join("?" for _ in candidate_ids)
            candidates = connection.execute(
                f"SELECT * FROM tasks WHERE user_id IS ? AND id IN ({placeholders})",
                [user_id, *candidate_ids],
            ).fetchall()
            candidate_by_id = {candidate["id"]: _task_from_row(candidate) for candidate in candidates}
            result.append(
                {
                    "id": row["id"],
                    "task": json.loads(row["task_json"]),
                    "candidates": [
                        candidate_by_id[candidate_id]
                        for candidate_id in candidate_ids
                        if candidate_id in candidate_by_id
                    ],
                }
            )
    return result


def resolve_task_proposal(proposal_id, choice, candidate_id=None, *, user_id=None):
    if not isinstance(choice, str) or choice not in {"update_existing", "create_separate", "cancel"}:
        raise ValueError("Choose update existing, create a separate task, or cancel.")
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        proposal = connection.execute(
            "SELECT * FROM task_proposals WHERE id = ? AND user_id IS ?",
            (proposal_id, user_id),
        ).fetchone()
        if proposal is None:
            return None

        proposed_task = json.loads(proposal["task_json"])
        candidate_ids = json.loads(proposal["candidate_ids_json"])
        if choice == "cancel":
            connection.execute(
                "DELETE FROM task_proposals WHERE id = ? AND user_id IS ?",
                (proposal_id, user_id),
            )
            return {
                "choice": choice,
                "task": proposed_task,
                "updated": None,
                "conversation_id": proposal["conversation_id"],
            }

        if choice == "create_separate":
            separate_task = _validate_fields(proposed_task, creating=True)
            separate_task.setdefault("description", None)
            separate_task.setdefault("project", None)
            separate_task.setdefault("due_date", None)
            separate_task.setdefault("priority", "medium")
            separate_task.setdefault("parent_task_id", None)
            task_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO tasks
                    (id, title, description, project, due_date, priority, status,
                     parent_task_id, created_at, updated_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                """,
                (
                    task_id,
                    separate_task["title"],
                    separate_task["description"],
                    separate_task["project"],
                    separate_task["due_date"],
                    separate_task["priority"],
                    separate_task["parent_task_id"],
                    now,
                    now,
                    user_id,
                ),
            )
            created = _task_from_row(
                connection.execute(
                    "SELECT * FROM tasks WHERE id = ? AND user_id IS ?",
                    (task_id, user_id),
                ).fetchone()
            )
            connection.execute(
                "DELETE FROM task_proposals WHERE id = ? AND user_id IS ?",
                (proposal_id, user_id),
            )
            return {
                "choice": choice,
                "task": proposed_task,
                "created": created,
                "conversation_id": proposal["conversation_id"],
            }

        if candidate_id not in candidate_ids:
            raise ValueError("Choose one of the listed existing tasks.")
        existing_row = connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id IS ?",
            (candidate_id, user_id),
        ).fetchone()
        if existing_row is None:
            raise ValueError("That existing task is no longer available. Refresh the conversation and try again.")

        changes = {
            field: proposed_task[field]
            for field in ("description", "project", "due_date", "priority")
            if proposed_task.get(field) is not None
        }
        if changes:
            assignments = [f"{field} = ?" for field in changes]
            parameters = list(changes.values())
            assignments.append("updated_at = ?")
            parameters.extend((now, candidate_id, user_id))
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ? AND user_id IS ?",
                parameters,
            )
        updated_row = connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id IS ?",
            (candidate_id, user_id),
        ).fetchone()
        updated = _task_from_row(updated_row)
        connection.execute(
            "DELETE FROM task_proposals WHERE id = ? AND user_id IS ?",
            (proposal_id, user_id),
        )
        return {
            "choice": choice,
            "task": proposed_task,
            "updated": updated,
            "conversation_id": proposal["conversation_id"],
        }


def get_task(task_id, *, user_id=None):
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id IS ?",
            (task_id, user_id),
        ).fetchone()
    return _task_from_row(row)


def list_tasks(*, view="all", status=None, project=None, search=None, user_id=None):
    today = date.today().isoformat()
    conditions = ["user_id IS ?"]
    parameters = [user_id]
    if view == "today":
        conditions.extend(("status = 'open'", "due_date = ?"))
        parameters.append(today)
    elif view == "upcoming":
        conditions.extend(("status = 'open'", "due_date > ?"))
        parameters.append(today)
    elif view == "overdue":
        conditions.extend(("status = 'open'", "due_date < ?"))
        parameters.append(today)
    elif view == "completed":
        conditions.append("status = 'completed'")
    elif view not in {"all", "open"}:
        raise ValueError("View must be today, upcoming, overdue, completed, open, or all.")
    elif view == "open":
        conditions.append("status = 'open'")

    if status is not None:
        if status not in STATUSES:
            raise ValueError("Status must be open or completed.")
        conditions.append("status = ?")
        parameters.append(status)
    if project:
        conditions.append("LOWER(COALESCE(project, '')) LIKE LOWER(?)")
        parameters.append(f"%{project}%")
    if search:
        conditions.append(
            "(LOWER(title) LIKE LOWER(?) OR LOWER(COALESCE(description, '')) LIKE LOWER(?) "
            "OR LOWER(COALESCE(project, '')) LIKE LOWER(?))"
        )
        parameters.extend((f"%{search}%", f"%{search}%", f"%{search}%"))

    query = "SELECT * FROM tasks"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, created_at DESC"
    with _connection() as connection:
        return [_task_from_row(row) for row in connection.execute(query, parameters).fetchall()]


def update_task(task_id, changes, *, user_id=None):
    if not isinstance(changes, dict) or not changes:
        raise ValueError("At least one task field must be updated.")
    if set(changes) - EDITABLE_TASK_FIELDS:
        raise ValueError("Only title, description, project, deadline, and priority can be edited.")
    values = _validate_fields(changes, creating=False)
    if not values:
        raise ValueError("At least one task field must be updated.")

    assignments = [f"{field} = ?" for field in values]
    parameters = list(values.values())
    assignments.append("updated_at = ?")
    parameters.extend((datetime.now(timezone.utc).isoformat(), task_id, user_id))
    with _connection() as connection:
        cursor = connection.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ? AND user_id IS ?",
            parameters,
        )
        if cursor.rowcount == 0:
            return None
        row = connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id IS ?",
            (task_id, user_id),
        ).fetchone()
    return _task_from_row(row)


def set_task_status(task_id, status, *, user_id=None):
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("Status must be open or completed.")
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND user_id IS ?",
            (status, datetime.now(timezone.utc).isoformat(), task_id, user_id),
        )
        if cursor.rowcount == 0:
            return None
        row = connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id IS ?",
            (task_id, user_id),
        ).fetchone()
    return _task_from_row(row)


def delete_task(task_id, *, user_id=None):
    with _connection() as connection:
        cursor = connection.execute(
            "DELETE FROM tasks WHERE id = ? AND user_id IS ?",
            (task_id, user_id),
        )
    return cursor.rowcount > 0
