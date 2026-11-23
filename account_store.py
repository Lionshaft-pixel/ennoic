import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

import database as database_layer


DEFAULT_DATABASE_PATH = Path(__file__).resolve().with_name("tasks.sqlite3")


class MemoryCapacityReached(Exception):
    def __init__(self, used, limit):
        super().__init__("The account's memory capacity has been reached.")
        self.used = used
        self.limit = limit


def database_path():
    return Path(os.environ.get("ENNOIC_TASK_DB", DEFAULT_DATABASE_PATH))


def database_key():
    return database_layer.database_key()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _timestamp_after(value, reference):
    if not value:
        return False
    try:
        parsed_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed_reference = datetime.fromisoformat(reference.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return False
    if parsed_value.tzinfo is None:
        parsed_value = parsed_value.replace(tzinfo=timezone.utc)
    if parsed_reference.tzinfo is None:
        parsed_reference = parsed_reference.replace(tzinfo=timezone.utc)
    return parsed_value > parsed_reference


def connection():
    return database_layer.connection()


def _begin_user_write(db, user_id):
    """Start a write transaction and serialize mutations for one account.

    PostgreSQL locks only this account's users row, so unrelated users proceed
    independently. SQLite has no row-level locks; BEGIN IMMEDIATE acquires its
    normal database-wide writer reservation before any count/check reads.
    """
    db.execute("BEGIN IMMEDIATE")
    if database_layer.uses_postgres():
        db.execute("SELECT id FROM users WHERE id = ? FOR UPDATE", (user_id,)).fetchone()


def initialize():
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0 CHECK (email_verified IN (0, 1)),
                created_at TEXT NOT NULL,
                password_changed_at TEXT,
                auth_version INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                purge_after TEXT,
                last_login_at TEXT,
                last_login_ip TEXT
            )
            """
        )
        user_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "auth_version" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0")
        if "deleted_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN deleted_at TEXT")
        if "purge_after" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN purge_after TEXT")
        if "last_login_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        if "last_login_ip" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN last_login_ip TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS account_deletion_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_ref TEXT NOT NULL,
                email TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                client_ip TEXT,
                purge_after TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                cancelled_at TEXT,
                purged_at TEXT
            )
            """
        )
        audit_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(account_deletion_audit)").fetchall()
        }
        if "purged_at" not in audit_columns:
            db.execute("ALTER TABLE account_deletion_audit ADD COLUMN purged_at TEXT")
        db.execute(
            "CREATE INDEX IF NOT EXISTS users_pending_account_deletions "
            "ON users(purge_after) WHERE deleted_at IS NOT NULL"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS account_deletion_audit_expiry "
            "ON account_deletion_audit(expires_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS account_deletion_audit_lookup "
            "ON account_deletion_audit(account_ref, requested_at DESC)"
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS account_subscriptions (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
                status TEXT NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'pending', 'active', 'canceled', 'past_due', 'expired')),
                provider TEXT,
                provider_subscription_id TEXT,
                period_start TEXT,
                period_end TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO account_subscriptions (user_id, plan, status, updated_at) "
            "SELECT id, 'free', 'free', ? FROM users",
            (_now(),),
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS checkout_contacts (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS account_usage (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                meter TEXT NOT NULL,
                period_start TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, meter, period_start)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_requests (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL,
                period_start TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('reserved', 'completed', 'failed')),
                billable INTEGER NOT NULL DEFAULT 1 CHECK (billable IN (0, 1)),
                response_json TEXT,
                response_status INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, request_id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_subscription_events (
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (provider, event_id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS paddle_checkout_intents (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                price_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                transaction_id TEXT,
                paddle_customer_id TEXT,
                paddle_subscription_id TEXT
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS paddle_checkout_intents_user "
            "ON paddle_checkout_intents(user_id, created_at DESC)"
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS paddle_subscriptions (
                paddle_subscription_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                paddle_customer_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'past_due', 'paused', 'canceled', 'expired')),
                price_id TEXT NOT NULL,
                period_start TEXT,
                period_end TEXT,
                cancel_at TEXT,
                last_event_at TEXT NOT NULL,
                provider_updated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS paddle_subscriptions_user "
            "ON paddle_subscriptions(user_id, updated_at DESC)"
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS paddle_transactions (
                paddle_transaction_id TEXT PRIMARY KEY,
                user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                paddle_customer_id TEXT,
                paddle_subscription_id TEXT,
                environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
                status TEXT NOT NULL,
                price_id TEXT,
                last_event_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS paddle_transactions_user "
            "ON paddle_transactions(user_id, updated_at DESC)"
        )
        ai_request_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(ai_requests)").fetchall()
        }
        if "billable" not in ai_request_columns:
            db.execute("ALTER TABLE ai_requests ADD COLUMN billable INTEGER NOT NULL DEFAULT 1")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS account_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL CHECK (purpose IN ('verify', 'reset')),
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                messages_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                person TEXT NOT NULL,
                object TEXT NOT NULL,
                time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS memories_user_person_object "
            "ON memories(user_id, lower(person), lower(object))"
        )
        for table in ("tasks", "task_proposals"):
            columns = {
                row["name"]
                for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if columns and "user_id" not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE")
            elif not columns and table == "tasks":
                db.execute(
                    """
                    CREATE TABLE tasks (
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
                        user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                        CHECK (priority IN ('low', 'medium', 'high')),
                        CHECK (status IN ('open', 'completed'))
                    )
                    """
                )
            elif not columns and table == "task_proposals":
                db.execute(
                    """
                    CREATE TABLE task_proposals (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        task_json TEXT NOT NULL,
                        candidate_ids_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        user_id TEXT REFERENCES users(id) ON DELETE CASCADE
                    )
                    """
                )
        db.execute("CREATE INDEX IF NOT EXISTS tasks_user_id ON tasks(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS task_proposals_user_id ON task_proposals(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS conversations_user_id ON conversations(user_id)")


def normalize_email(email):
    if not isinstance(email, str):
        raise ValueError("Enter a valid email address.")
    normalized = email.strip().casefold()
    if (
        len(normalized) > 254
        or normalized.count("@") != 1
        or any(char.isspace() for char in normalized)
        or normalized.startswith("@")
        or normalized.endswith("@")
        or "." not in normalized.rsplit("@", 1)[1]
    ):
        raise ValueError("Enter a valid email address.")
    return normalized


def validate_password(password):
    if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
        raise ValueError("Use a password between 12 and 128 characters.")
    return password


def create_user(email, password):
    normalized = normalize_email(email)
    validate_password(password)
    user_id = str(uuid.uuid4())
    try:
        with connection() as db:
            db.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, normalized, generate_password_hash(password, method="pbkdf2:sha256:600000"), _now()),
            )
            db.execute(
                "INSERT INTO account_subscriptions (user_id, plan, status, updated_at) VALUES (?, 'free', 'free', ?)",
                (user_id, _now()),
            )
    except database_layer.INTEGRITY_ERRORS:
        return None
    return get_user(user_id)


def get_user(user_id):
    with connection() as db:
        row = db.execute(
            "SELECT id, email, password_hash, email_verified, created_at, password_changed_at, auth_version, deleted_at, purge_after "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_email(email):
    normalized = normalize_email(email)
    with connection() as db:
        row = db.execute(
            "SELECT id, email, password_hash, email_verified, created_at, password_changed_at, auth_version, deleted_at, purge_after "
            "FROM users WHERE email = ?",
            (normalized,),
        ).fetchone()
    return dict(row) if row else None


def record_user_login(user_id, client_ip):
    ip_value = client_ip.strip()[:64] if isinstance(client_ip, str) else None
    with connection() as db:
        cursor = db.execute(
            "UPDATE users SET last_login_at = ?, last_login_ip = ? WHERE id = ? AND deleted_at IS NULL",
            (_now(), ip_value or None, user_id),
        )
    return cursor.rowcount == 1


def validate_checkout_contact(full_name, email, phone):
    if not isinstance(full_name, str):
        raise ValueError("Enter your full name.")
    full_name = full_name.strip()
    if len(full_name) < 2 or len(full_name) > 120 or any(ord(char) < 32 for char in full_name):
        raise ValueError("Enter a valid name between 2 and 120 characters.")
    email = normalize_email(email)
    if not isinstance(phone, str):
        raise ValueError("Enter a valid mobile number.")
    phone = phone.strip()
    digits = "".join(char for char in phone if char.isdigit())
    if (len(phone) > 32 or len(digits) < 7 or len(digits) > 15
            or any(char not in "+0123456789 ()-" for char in phone)):
        raise ValueError("Enter a valid mobile number with 7 to 15 digits.")
    return {"full_name": full_name, "email": email, "phone": phone}


def save_checkout_contact(user_id, full_name, email, phone):
    contact = validate_checkout_contact(full_name, email, phone)
    full_name, email, phone = contact["full_name"], contact["email"], contact["phone"]
    now = _now()
    with connection() as db:
        db.execute(
            """
            INSERT INTO checkout_contacts (user_id, full_name, email, phone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name = excluded.full_name,
                email = excluded.email,
                phone = excluded.phone,
                updated_at = excluded.updated_at
            """,
            (user_id, full_name, email, phone, now, now),
        )
    return get_checkout_contact(user_id)


def get_checkout_contact(user_id):
    with connection() as db:
        row = db.execute(
            "SELECT full_name, email, phone, created_at, updated_at FROM checkout_contacts WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def list_admin_users():
    with connection() as db:
        rows = db.execute(
            """
            SELECT u.id, u.email, u.email_verified, u.created_at, u.deleted_at, u.purge_after,
                   u.last_login_at, u.last_login_ip,
                   c.full_name AS contact_name, c.email AS contact_email,
                   c.phone AS contact_phone, c.updated_at AS contact_updated_at
            FROM users AS u
            LEFT JOIN checkout_contacts AS c ON c.user_id = u.id
            ORDER BY u.created_at DESC, u.email COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]


def schedule_user_deletion(user_id, client_ip, *, grace_days=30, audit_retention_days=90):
    now = datetime.now(timezone.utc)
    requested_at = now.isoformat()
    purge_after = (now + timedelta(days=grace_days)).isoformat()
    expires_at = (now + timedelta(days=grace_days + audit_retention_days)).isoformat()
    account_ref = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        select_user = "SELECT email, deleted_at, purge_after FROM users WHERE id = ?"
        if database_layer.uses_postgres():
            # Serialize concurrent deletion confirmations so only one audit
            # record and one deletion schedule are created for this account.
            select_user += " FOR UPDATE"
        row = db.execute(select_user, (user_id,)).fetchone()
        if row is None:
            return None
        if row["deleted_at"]:
            return {"email": row["email"], "purge_after": row["purge_after"], "already_scheduled": True}
        db.execute(
            "UPDATE users SET deleted_at = ?, purge_after = ?, auth_version = auth_version + 1 WHERE id = ?",
            (requested_at, purge_after, user_id),
        )
        db.execute("DELETE FROM account_tokens WHERE user_id = ?", (user_id,))
        db.execute(
            "INSERT INTO account_deletion_audit "
            "(account_ref, email, requested_at, client_ip, purge_after, expires_at, cancelled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (account_ref, row["email"], requested_at, client_ip, purge_after, expires_at),
        )
    return {"email": row["email"], "purge_after": purge_after, "already_scheduled": False}


def get_scheduled_deletion(user_id):
    account_ref = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    with connection() as db:
        row = db.execute(
            "SELECT email, deleted_at, purge_after FROM users WHERE id = ? AND deleted_at IS NOT NULL",
            (user_id,),
        ).fetchone()
        if row:
            return dict(row)
        row = db.execute(
            "SELECT email, requested_at AS deleted_at, purge_after, cancelled_at, purged_at, expires_at "
            "FROM account_deletion_audit WHERE account_ref = ? ORDER BY requested_at DESC LIMIT 1",
            (account_ref,),
        ).fetchone()
    if row and row["cancelled_at"] is None and row["expires_at"] > _now():
        return dict(row)
    return None


def cancel_user_deletion(user_id):
    account_ref = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "UPDATE users SET deleted_at = NULL, purge_after = NULL, auth_version = auth_version + 1 "
            "WHERE id = ? AND deleted_at IS NOT NULL AND purge_after > ?",
            (user_id, _now()),
        )
        if cursor.rowcount:
            cancelled_at = _now()
            db.execute(
                "UPDATE account_deletion_audit SET cancelled_at = ?, expires_at = ? "
                "WHERE id = (SELECT id FROM account_deletion_audit "
                "WHERE account_ref = ? AND cancelled_at IS NULL ORDER BY requested_at DESC LIMIT 1)",
                (cancelled_at, (datetime.fromisoformat(cancelled_at) + timedelta(days=90)).isoformat(), account_ref),
            )
    return cursor.rowcount == 1


def due_deletion_user_ids(*, limit=25):
    with connection() as db:
        rows = db.execute(
            "SELECT id FROM users WHERE deleted_at IS NOT NULL AND purge_after <= ? LIMIT ?",
            (_now(), limit),
        ).fetchall()
    return [row["id"] for row in rows]


def purge_due_deletions(*, verified_user_ids, limit=25):
    """Purge only accounts whose billing state the caller just re-verified."""
    verified_user_ids = list(dict.fromkeys(verified_user_ids))
    if not verified_user_ids:
        return 0
    now = _now()
    purged = 0
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        placeholders = ", ".join("?" for _ in verified_user_ids)
        ids = [row["id"] for row in db.execute(
            "SELECT id FROM users WHERE deleted_at IS NOT NULL AND purge_after <= ? "
            f"AND id IN ({placeholders}) LIMIT ?",
            (now, *verified_user_ids, limit),
        ).fetchall()]
        for user_id in ids:
            account_ref = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
            purged_at = datetime.now(timezone.utc)
            db.execute(
                "UPDATE account_deletion_audit SET purged_at = ?, expires_at = ? "
                "WHERE id = (SELECT id FROM account_deletion_audit "
                "WHERE account_ref = ? AND cancelled_at IS NULL AND purged_at IS NULL "
                "ORDER BY requested_at DESC LIMIT 1)",
                (purged_at.isoformat(), (purged_at + timedelta(days=90)).isoformat(), account_ref),
            )
            for table in ("account_tokens", "task_proposals", "tasks", "conversations", "memories"):
                db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            purged += 1
        db.execute("DELETE FROM account_deletion_audit WHERE expires_at <= ?", (now,))
    return purged


def verify_password(user, password):
    if not user or not isinstance(password, str):
        return False

    encoded_hash = user["password_hash"]
    if encoded_hash.startswith("scrypt:"):
        try:
            method, salt, expected = encoded_hash.split("$", 2)
            if method != "scrypt:32768:8:1":
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt.encode("utf-8"),
                n=32768,
                r=8,
                p=1,
                maxmem=64 * 1024 * 1024,
            ).hex()
        except (ValueError, UnicodeError):
            return False
        return hmac.compare_digest(actual, expected)

    return check_password_hash(encoded_hash, password)


def update_password(user_id, password):
    validate_password(password)
    with connection() as db:
        cursor = db.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ?, auth_version = auth_version + 1 WHERE id = ?",
            (generate_password_hash(password, method="pbkdf2:sha256:600000"), _now(), user_id),
        )
        db.execute("DELETE FROM account_tokens WHERE user_id = ?", (user_id,))
    return cursor.rowcount == 1


def create_account_token(user_id, token_hash, purpose):
    if purpose not in {"verify", "reset"}:
        raise ValueError("Invalid account token purpose.")
    with connection() as db:
        db.execute("DELETE FROM account_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
        db.execute(
            "INSERT INTO account_tokens (token_hash, user_id, purpose, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, purpose, _now()),
        )


def consume_account_token(user_id, token_hash, purpose):
    with connection() as db:
        row = db.execute(
            "SELECT token_hash FROM account_tokens WHERE user_id = ? AND token_hash = ? AND purpose = ?",
            (user_id, token_hash, purpose),
        ).fetchone()
        if row is None:
            return False
        db.execute(
            "DELETE FROM account_tokens WHERE user_id = ? AND purpose = ?",
            (user_id, purpose),
        )
        return True


def verify_email(user_id):
    with connection() as db:
        cursor = db.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
        if cursor.rowcount:
            db.execute("DELETE FROM account_tokens WHERE user_id = ? AND purpose = 'verify'", (user_id,))
    return cursor.rowcount == 1


def create_conversation(user_id, conversation_id, title, messages=None):
    messages = messages if isinstance(messages, list) else []
    with connection() as db:
        db.execute(
            """
            INSERT INTO conversations (id, user_id, title, pinned, messages_json, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (conversation_id, user_id, title, json.dumps(messages, ensure_ascii=False), _now(), _now()),
        )
    return get_conversation(user_id, conversation_id)


def get_conversation(user_id, conversation_id):
    with connection() as db:
        row = db.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    return _conversation_from_row(row) if row else None


def list_conversations(user_id, *, limit=None):
    if limit is not None:
        limit = max(0, int(limit))
    with connection() as db:
        query = "SELECT * FROM conversations WHERE user_id = ? ORDER BY pinned DESC, updated_at DESC, id DESC"
        parameters = [user_id]
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        rows = db.execute(
            query,
            parameters,
        ).fetchall()
    return [_conversation_from_row(row) for row in rows]


def count_conversations(user_id):
    with connection() as db:
        return db.execute("SELECT COUNT(*) AS count FROM conversations WHERE user_id = ?", (user_id,)).fetchone()["count"]


def conversation_is_in_window(user_id, conversation_id, limit):
    limit = max(0, int(limit))
    with connection() as db:
        row = db.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM ("
            "SELECT id FROM conversations WHERE user_id = ? "
            "ORDER BY pinned DESC, updated_at DESC, id DESC LIMIT ?"
            ") AS recent WHERE recent.id = ?"
            ") AS is_in_window",
            (user_id, limit, conversation_id),
        ).fetchone()
    return bool(row["is_in_window"])


def _conversation_from_row(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "pinned": bool(row["pinned"]),
        "messages": json.loads(row["messages_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def update_conversation(user_id, conversation_id, *, title=None, pinned=None, messages=None):
    changes = []
    parameters = []
    if title is not None:
        changes.append("title = ?")
        parameters.append(title)
    if pinned is not None:
        changes.append("pinned = ?")
        parameters.append(int(pinned))
    if messages is not None:
        changes.append("messages_json = ?")
        parameters.append(json.dumps(messages, ensure_ascii=False))
    if not changes:
        return get_conversation(user_id, conversation_id)
    changes.append("updated_at = ?")
    parameters.extend((_now(), conversation_id, user_id))
    with connection() as db:
        cursor = db.execute(
            f"UPDATE conversations SET {', '.join(changes)} WHERE id = ? AND user_id = ?",
            parameters,
        )
        if cursor.rowcount != 1:
            return None
        row = db.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    return _conversation_from_row(row)


def delete_conversation(user_id, conversation_id):
    with connection() as db:
        db.execute(
            "DELETE FROM task_proposals WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        cursor = db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
    return cursor.rowcount == 1


def delete_all_conversations(user_id):
    with connection() as db:
        db.execute("DELETE FROM task_proposals WHERE user_id = ?", (user_id,))
        cursor = db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
    return cursor.rowcount


def append_conversation_turn(user_id, conversation_id, user_message, history, assistant_message):
    conversation = get_conversation(user_id, conversation_id)
    if conversation is None:
        return None
    messages = conversation["messages"]
    if not messages or messages[-1].get("role") != "user" or messages[-1].get("text") != user_message:
        messages.append({"role": "user", "text": user_message})
    messages.append(assistant_message)
    return update_conversation(
        user_id,
        conversation_id,
        messages=messages,
    )


def import_conversations(user_id, conversations):
    if not isinstance(conversations, list) or len(conversations) > 500:
        raise ValueError("Conversation import must contain at most 500 conversations.")
    imported = []
    for item in conversations:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        messages = item.get("messages")
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or not isinstance(messages, list):
            continue
        clean_messages = []
        for message in messages[:500]:
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                continue
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            clean_messages.append(
                {
                    "role": message["role"],
                    "text": text[:4000] if message["role"] == "user" else text[-4000:],
                    **(
                        {"truncated": True}
                        if message["role"] == "assistant" and message.get("truncated") is True
                        else {}
                    ),
                }
            )
        conversation_id = str(uuid.uuid4())
        imported.append(create_conversation(user_id, conversation_id, title.strip(), clean_messages))
    return imported


def list_memories(user_id):
    with connection() as db:
        rows = db.execute(
            "SELECT id, person, object, time FROM memories WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_memories(user_id):
    with connection() as db:
        return db.execute("SELECT COUNT(*) AS count FROM memories WHERE user_id = ?", (user_id,)).fetchone()["count"]


def remember(user_id, person, object_name, time_value, *, max_memories=None):
    with connection() as db:
        _begin_user_write(db, user_id)
        existing = db.execute(
            "SELECT id FROM memories WHERE user_id = ? AND lower(person) = lower(?) "
            "AND lower(object) = lower(?)",
            (user_id, person, object_name),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE memories SET time = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (time_value, _now(), existing["id"], user_id),
            )
            return "Updated existing memory"
        if max_memories is not None:
            used = db.execute("SELECT COUNT(*) AS count FROM memories WHERE user_id = ?", (user_id,)).fetchone()["count"]
            if used >= max_memories:
                raise MemoryCapacityReached(used, max_memories)
        db.execute(
            "INSERT INTO memories (id, user_id, person, object, time, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, person, object_name, time_value, _now(), _now()),
        )
    return "Stored new memory"


def get_account_subscription(user_id, *, now=None):
    now = now or _now()
    with connection() as db:
        row = db.execute("SELECT * FROM account_subscriptions WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        subscription = dict(row)
        if (
            subscription["plan"] == "pro"
            and subscription["status"] in {"active", "canceled"}
            and subscription["period_end"]
            and not _timestamp_after(subscription["period_end"], now)
        ):
            db.execute(
                "UPDATE account_subscriptions SET plan = 'free', status = 'expired', updated_at = ? "
                "WHERE user_id = ?",
                (now, user_id),
            )
            # Keep provider IDs and period metadata so account deletion can still
            # verify that an apparently expired subscription is not renewing.
            subscription.update(plan="free", status="expired", updated_at=now)
    return subscription


def get_account_subscription_for_deletion(user_id):
    """Return the stored provider subscription without expiring or mutating it."""
    with connection() as db:
        row = db.execute(
            "SELECT * FROM account_subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_usage(user_id, meter, period_start):
    with connection() as db:
        row = db.execute(
            "SELECT used FROM account_usage WHERE user_id = ? AND meter = ? AND period_start = ?",
            (user_id, meter, period_start),
        ).fetchone()
    return row["used"] if row else 0


def reserve_ai_request(user_id, request_id, request_hash, period_start, period_end, monthly_limit, *, billable=True):
    now = _now()
    with connection() as db:
        _begin_user_write(db, user_id)
        existing = db.execute(
            "SELECT * FROM ai_requests WHERE user_id = ? AND request_id = ?",
            (user_id, request_id),
        ).fetchone()
        used_row = db.execute(
            "SELECT used FROM account_usage WHERE user_id = ? AND meter = 'ai_requests' AND period_start = ?",
            (user_id, period_start),
        ).fetchone()
        used = used_row["used"] if used_row else 0
        if existing:
            existing = dict(existing)
            if existing["request_hash"] != request_hash:
                return {"state": "conflict", "used": used, "limit": monthly_limit, "period_end": period_end}
            if existing["state"] == "completed":
                return {
                    "state": "completed", "used": used, "limit": monthly_limit,
                    "period_end": period_end,
                    "response": json.loads(existing["response_json"]),
                    "response_status": existing["response_status"] or 200,
                }
            if existing["state"] == "reserved":
                try:
                    updated_at = existing["updated_at"]
                    if isinstance(updated_at, str):
                        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    stale = updated_at < datetime.now(timezone.utc) - timedelta(minutes=5)
                except (TypeError, ValueError):
                    stale = False
                if not stale:
                    return {"state": "in_progress", "used": used, "limit": monthly_limit, "period_end": period_end}
                if existing["billable"]:
                    db.execute(
                        "UPDATE account_usage SET used = CASE WHEN used > 0 THEN used - 1 ELSE 0 END, updated_at = ? "
                        "WHERE user_id = ? AND meter = 'ai_requests' AND period_start = ?",
                        (now, user_id, existing["period_start"]),
                    )
                    if existing["period_start"] == period_start:
                        used = max(0, used - 1)
                db.execute(
                    "UPDATE ai_requests SET state = 'failed', updated_at = ? WHERE user_id = ? AND request_id = ?",
                    (now, user_id, request_id),
                )
        if billable and monthly_limit is not None and used >= monthly_limit:
            return {"state": "limit", "used": used, "limit": monthly_limit, "period_end": period_end}
        if billable:
            db.execute(
                "INSERT INTO account_usage (user_id, meter, period_start, used, updated_at) "
                "VALUES (?, 'ai_requests', ?, 1, ?) "
                "ON CONFLICT(user_id, meter, period_start) DO UPDATE SET "
                "used = account_usage.used + 1, updated_at = excluded.updated_at",
                (user_id, period_start, now),
            )
        db.execute(
            """
            INSERT INTO ai_requests (user_id, request_id, period_start, request_hash, state, response_json,
                response_status, billable, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'reserved', NULL, NULL, ?, ?, ?)
            ON CONFLICT(user_id, request_id) DO UPDATE SET period_start = excluded.period_start,
                request_hash = excluded.request_hash, state = 'reserved', response_json = NULL,
                response_status = NULL, billable = excluded.billable, updated_at = excluded.updated_at
            """,
            (user_id, request_id, period_start, request_hash, int(billable), now, now),
        )
        return {"state": "reserved", "used": used + int(billable), "limit": monthly_limit, "period_end": period_end}


def complete_ai_request(user_id, request_id, response, response_status=200):
    now = _now()
    with connection() as db:
        _begin_user_write(db, user_id)
        db.execute(
            "UPDATE ai_requests SET state = 'completed', response_json = ?, response_status = ?, updated_at = ? "
            "WHERE user_id = ? AND request_id = ? AND state = 'reserved'",
            (json.dumps(response, ensure_ascii=False), response_status, now, user_id, request_id),
        )


def release_ai_request(user_id, request_id):
    now = _now()
    with connection() as db:
        _begin_user_write(db, user_id)
        row = db.execute(
            "SELECT period_start, state, billable FROM ai_requests WHERE user_id = ? AND request_id = ?",
            (user_id, request_id),
        ).fetchone()
        if row is None or row["state"] != "reserved":
            return
        if row["billable"]:
            db.execute(
                "UPDATE account_usage SET used = CASE WHEN used > 0 THEN used - 1 ELSE 0 END, updated_at = ? "
                "WHERE user_id = ? AND meter = 'ai_requests' AND period_start = ?",
                (now, user_id, row["period_start"]),
            )
        db.execute(
            "UPDATE ai_requests SET state = 'failed', updated_at = ? WHERE user_id = ? AND request_id = ?",
            (now, user_id, request_id),
        )


def apply_verified_subscription_event(*, provider, event_id, user_id, event_type, subscription_id=None,
                                      period_start=None, period_end=None):
    now = _now()
    if event_type not in {"activated", "renewed", "canceled", "expired", "payment_failed"}:
        raise ValueError("Unsupported subscription event.")
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "INSERT OR IGNORE INTO processed_subscription_events (provider, event_id, processed_at) VALUES (?, ?, ?)",
            (provider, event_id, now),
        )
        if cursor.rowcount == 0:
            return False
        previous = db.execute(
            "SELECT plan, status, provider, provider_subscription_id, period_start, period_end "
            "FROM account_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        cancellation_was_confirmed = bool(
            previous
            and previous["status"] == "canceled"
            and previous["provider"] == provider
            and previous["provider_subscription_id"] == subscription_id
        )
        if event_type in {"activated", "renewed"}:
            plan, status = "pro", "active"
        elif event_type == "canceled" and _timestamp_after(period_end, now):
            plan, status = "pro", "canceled"
        elif event_type == "payment_failed":
            plan, status = "free", "past_due"
        else:
            plan, status = "free", "expired"
        if cancellation_was_confirmed and event_type in {"activated", "renewed", "payment_failed"}:
            # Razorpay events have no ordering guard in this store. A delayed
            # pre-cancellation event must not make a provider-canceled account
            # look renewable again after restoration.
            plan, status = previous["plan"], "canceled"
            period_start, period_end = previous["period_start"], previous["period_end"]
        db.execute(
            """
            INSERT INTO account_subscriptions (user_id, plan, status, provider, provider_subscription_id,
                period_start, period_end, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET plan = excluded.plan, status = excluded.status,
                provider = excluded.provider, provider_subscription_id = excluded.provider_subscription_id,
                period_start = excluded.period_start, period_end = excluded.period_end, updated_at = excluded.updated_at
            """,
            (user_id, plan, status, provider,
             subscription_id if plan == "pro" or event_type == "payment_failed" else None,
             period_start if plan == "pro" or event_type == "payment_failed" else None,
             period_end if plan == "pro" or event_type == "payment_failed" else None, now),
        )
    return True


def create_paddle_checkout_intent(user_id, environment, price_id, *, lifetime_minutes=120):
    if environment not in {"sandbox", "production"}:
        raise ValueError("Unsupported Paddle environment.")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = datetime.now(timezone.utc)
    with connection() as db:
        db.execute(
            "INSERT INTO paddle_checkout_intents "
            "(token_hash, user_id, environment, price_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token_hash, user_id, environment, price_id, now.isoformat(),
             (now + timedelta(minutes=lifetime_minutes)).isoformat()),
        )
    return token


def _paddle_intent_by_token(db, token, *, allow_expired=False):
    if not isinstance(token, str) or not token or len(token) > 200:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    intent = db.execute(
        "SELECT * FROM paddle_checkout_intents WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if not intent or (not allow_expired and not _timestamp_after(intent["expires_at"], _now())):
        return None
    return intent


def get_paddle_checkout_intent(user_id, environment, price_id, token):
    """Resolve a Paddle custom-data token only when it belongs to this user/env/price."""
    with connection() as db:
        intent = _paddle_intent_by_token(db, token, allow_expired=True)
    if (not intent or intent["user_id"] != user_id or intent["environment"] != environment
            or intent["price_id"] != price_id):
        return None
    return dict(intent)


def _paddle_event_is_newer(occurred_at, provider_updated_at, row):
    def parse(value):
        if not value:
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
        except (AttributeError, TypeError, ValueError):
            return None

    event_time = parse(occurred_at)
    stored_time = parse(row["last_event_at"])
    if event_time is None or stored_time is None:
        return False
    if event_time > stored_time:
        return True
    if event_time < stored_time:
        return False
    new_updated = parse(provider_updated_at)
    old_updated = parse(row["provider_updated_at"])
    return bool(new_updated and (not old_updated or new_updated > old_updated))


def record_paddle_transaction_event(*, event_id, event_type, occurred_at, transaction_id,
                                    status, environment, customer_id=None, subscription_id=None,
                                    price_id=None, checkout_token=None, event_source="paddle",
                                    allow_expired_intent=False):
    """Record signed Paddle transaction events without granting Pro access."""
    now = _now()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        inserted = db.execute(
            "INSERT OR IGNORE INTO processed_subscription_events (provider, event_id, processed_at) "
            "VALUES (?, ?, ?)", (event_source, event_id, now),
        )
        if inserted.rowcount == 0:
            return False

        intent = _paddle_intent_by_token(
            db, checkout_token, allow_expired=allow_expired_intent
        )
        existing_subscription = None
        if subscription_id:
            existing_subscription = db.execute(
                "SELECT user_id, paddle_customer_id, environment FROM paddle_subscriptions "
                "WHERE paddle_subscription_id = ?", (subscription_id,),
            ).fetchone()
        user_id = intent["user_id"] if intent else (
            existing_subscription["user_id"] if existing_subscription else None
        )
        if intent and existing_subscription and intent["user_id"] != existing_subscription["user_id"]:
            raise ValueError("Paddle transaction account mapping does not match its subscription.")
        if existing_subscription and (
            existing_subscription["paddle_customer_id"] != customer_id
            or existing_subscription["environment"] != environment
        ):
            raise ValueError("Paddle transaction customer or environment does not match its subscription.")
        if intent and intent["environment"] != environment:
            raise ValueError("Paddle transaction environment does not match its checkout intent.")
        if intent and price_id and intent["price_id"] != price_id:
            raise ValueError("Paddle transaction price does not match its checkout intent.")
        if intent and subscription_id:
            bound_id = intent["paddle_subscription_id"]
            if bound_id and bound_id != subscription_id:
                raise ValueError("Paddle checkout intent is already linked to another subscription.")
            db.execute(
                "UPDATE paddle_checkout_intents SET transaction_id = ?, paddle_customer_id = ?, "
                "paddle_subscription_id = ? WHERE token_hash = ?",
                (transaction_id, customer_id, subscription_id, intent["token_hash"]),
            )

        previous = db.execute(
            "SELECT * FROM paddle_transactions WHERE paddle_transaction_id = ?", (transaction_id,)
        ).fetchone()
        if previous:
            if previous["environment"] != environment:
                raise ValueError("Paddle transaction environment changed unexpectedly.")
            if previous["user_id"] and user_id and previous["user_id"] != user_id:
                raise ValueError("Paddle transaction account mapping changed unexpectedly.")
            if previous["paddle_customer_id"] and customer_id and previous["paddle_customer_id"] != customer_id:
                raise ValueError("Paddle transaction customer changed unexpectedly.")
            event_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            previous_time = datetime.fromisoformat(previous["last_event_at"].replace("Z", "+00:00"))
            if event_time <= previous_time:
                return False
            updated = db.execute(
                "UPDATE paddle_transactions SET user_id = COALESCE(user_id, ?), "
                "paddle_customer_id = COALESCE(?, paddle_customer_id), "
                "paddle_subscription_id = COALESCE(?, paddle_subscription_id), status = ?, "
                "price_id = COALESCE(?, price_id), last_event_at = ?, updated_at = ? "
                "WHERE paddle_transaction_id = ? AND last_event_at < ?",
                (user_id, customer_id, subscription_id, status, price_id, occurred_at, now,
                 transaction_id, occurred_at),
            )
            if updated.rowcount == 0:
                return False
        else:
            db.execute(
                "INSERT INTO paddle_transactions (paddle_transaction_id, user_id, paddle_customer_id, "
                "paddle_subscription_id, environment, status, price_id, last_event_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transaction_id, user_id, customer_id, subscription_id, environment, status, price_id,
                 occurred_at, now, now),
            )
    return True


def apply_paddle_subscription_event(*, event_id, occurred_at, provider_updated_at, environment,
                                    subscription_id, customer_id, status, price_id, period_start=None,
                                    period_end=None, cancel_at=None, checkout_token=None,
                                    event_source="paddle", allow_expired_intent=False):
    """Atomically deduplicate, order, and persist a signed Paddle subscription snapshot."""
    if environment not in {"sandbox", "production"}:
        raise ValueError("Unsupported Paddle environment.")
    if status not in {"pending", "active", "past_due", "paused", "canceled", "expired"}:
        raise ValueError("Unsupported Paddle subscription status.")
    now = _now()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        inserted = db.execute(
            "INSERT OR IGNORE INTO processed_subscription_events (provider, event_id, processed_at) "
            "VALUES (?, ?, ?)", (event_source, event_id, now),
        )
        if inserted.rowcount == 0:
            return False

        existing = db.execute(
            "SELECT * FROM paddle_subscriptions WHERE paddle_subscription_id = ?", (subscription_id,)
        ).fetchone()
        intent = _paddle_intent_by_token(
            db, checkout_token, allow_expired=allow_expired_intent
        )
        if intent and intent["environment"] != environment:
            raise ValueError("Paddle subscription environment does not match its checkout intent.")
        if intent and price_id != intent["price_id"]:
            raise ValueError("Paddle subscription price does not match its checkout intent.")
        if existing and intent and existing["user_id"] != intent["user_id"]:
            raise ValueError("Paddle subscription account mapping does not match its checkout intent.")
        if existing and existing["paddle_customer_id"] != customer_id:
            raise ValueError("Paddle customer does not match the stored subscription owner.")
        if existing and existing["environment"] != environment:
            raise ValueError("Paddle environment does not match the stored subscription.")
        if intent:
            bound_id = intent["paddle_subscription_id"]
            if bound_id and bound_id != subscription_id:
                raise ValueError("Paddle checkout intent is already linked to another subscription.")
            db.execute(
                "UPDATE paddle_checkout_intents SET paddle_customer_id = ?, paddle_subscription_id = ? "
                "WHERE token_hash = ?",
                (customer_id, subscription_id, intent["token_hash"]),
            )
        elif existing is None:
            # A new subscription must be tied to an authenticated checkout intent.
            return False

        if existing:
            if not _paddle_event_is_newer(occurred_at, provider_updated_at, existing):
                return False
            event_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            stored_time = datetime.fromisoformat(existing["last_event_at"].replace("Z", "+00:00"))
            if event_time > stored_time:
                order_guard = "last_event_at < ?"
                order_parameters = (occurred_at,)
            else:
                order_guard = (
                    "last_event_at = ? AND (provider_updated_at IS NULL OR provider_updated_at < ?)"
                )
                order_parameters = (occurred_at, provider_updated_at)

        user_id = intent["user_id"] if intent else existing["user_id"]
        if existing and status == "canceled":
            # Paddle omits current_billing_period after cancellation. Keep the last
            # known period end so scheduled cancellations retain access until effective.
            period_start = period_start or existing["period_start"]
            period_end = period_end or existing["period_end"] or cancel_at
            cancel_at = cancel_at or existing["cancel_at"]
        if status == "active" and cancel_at and not period_end:
            period_end = cancel_at
        if existing:
            updated = db.execute(
                "UPDATE paddle_subscriptions SET user_id = ?, paddle_customer_id = ?, environment = ?, "
                "status = ?, price_id = ?, period_start = ?, period_end = ?, cancel_at = ?, "
                "last_event_at = ?, provider_updated_at = ?, updated_at = ? "
                "WHERE paddle_subscription_id = ? AND " + order_guard,
                (user_id, customer_id, environment, status, price_id, period_start, period_end, cancel_at,
                 occurred_at, provider_updated_at, now, subscription_id, *order_parameters),
            )
            if updated.rowcount == 0:
                return False
        else:
            db.execute(
                "INSERT INTO paddle_subscriptions (paddle_subscription_id, user_id, paddle_customer_id, "
                "environment, status, price_id, period_start, period_end, cancel_at, last_event_at, "
                "provider_updated_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subscription_id, user_id, customer_id, environment, status, price_id, period_start,
                 period_end, cancel_at, occurred_at, provider_updated_at, now, now),
            )
    return True


def get_paddle_subscription(user_id, environment, *, now=None):
    now = now or _now()
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM paddle_subscriptions WHERE user_id = ? AND environment = ? "
            "ORDER BY updated_at DESC", (user_id, environment),
        ).fetchall()
    for raw in rows:
        subscription = dict(raw)
        effective_end = subscription.get("cancel_at") or subscription.get("period_end")
        if (subscription["status"] in {"active", "past_due", "canceled"}
                and _timestamp_after(effective_end, now)):
            if subscription["cancel_at"]:
                subscription["status"] = "canceled"
            return subscription
    if not rows:
        return None
    latest = dict(rows[0])
    effective_end = latest.get("cancel_at") or latest.get("period_end")
    if latest["status"] in {"active", "past_due", "canceled"} and not _timestamp_after(effective_end, now):
        latest["status"] = "expired"
    return latest


def get_paddle_subscriptions_for_deletion(user_id):
    """Return potentially billable Paddle records across both isolated environments."""
    now = _now()
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM paddle_subscriptions WHERE user_id = ? "
            "AND status IN ('pending', 'active', 'past_due', 'paused', 'canceled') "
            "ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    subscriptions = []
    for raw in rows:
        subscription = dict(raw)
        if subscription["status"] != "canceled" or _timestamp_after(
            subscription.get("cancel_at") or subscription.get("period_end"), now
        ):
            subscriptions.append(subscription)
    return subscriptions


def get_unlinked_paddle_checkout_intents(user_id):
    """Return this account's checkout attempts without a mapped subscription."""
    with connection() as db:
        rows = db.execute(
            "SELECT DISTINCT environment, price_id FROM paddle_checkout_intents "
            "WHERE user_id = ? AND paddle_subscription_id IS NULL "
            "ORDER BY environment, price_id",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_paddle_cancellation_schedule(user_id, environment, subscription_id, effective_at):
    """Persist a provider-confirmed Paddle cancellation for restore-time UI."""
    with connection() as db:
        cursor = db.execute(
            "UPDATE paddle_subscriptions SET status = 'canceled', cancel_at = ?, updated_at = ? "
            "WHERE user_id = ? AND environment = ? AND paddle_subscription_id = ? "
            "AND status IN ('pending', 'active', 'past_due', 'paused', 'canceled')",
            (effective_at, _now(), user_id, environment, subscription_id),
        )
    return cursor.rowcount == 1


def record_legacy_cancellation_schedule(user_id, provider, subscription_id, effective_at):
    """Persist a provider-confirmed legacy-provider cancellation for restore-time UI."""
    with connection() as db:
        cursor = db.execute(
            "UPDATE account_subscriptions SET status = 'canceled', "
            "period_end = COALESCE(?, period_end), updated_at = ? "
            "WHERE user_id = ? AND provider = ? AND provider_subscription_id = ? "
            "AND status IN ('active', 'past_due', 'canceled')",
            (effective_at, _now(), user_id, provider, subscription_id),
        )
    return cursor.rowcount == 1


def get_paddle_subscription_for_portal(user_id, environment):
    with connection() as db:
        row = db.execute(
            "SELECT paddle_customer_id, paddle_subscription_id FROM paddle_subscriptions "
            "WHERE user_id = ? AND environment = ? ORDER BY updated_at DESC LIMIT 1",
            (user_id, environment),
        ).fetchone()
    return dict(row) if row else None


def forget_memory(user_id, person, object_name):
    with connection() as db:
        cursor = db.execute(
            "DELETE FROM memories WHERE user_id = ? AND lower(person) = lower(?) "
            "AND lower(object) = lower(?)",
            (user_id, person, object_name),
        )
    return cursor.rowcount


def legacy_data_counts():
    with connection() as db:
        task_count = db.execute("SELECT COUNT(*) AS count FROM tasks WHERE user_id IS NULL").fetchone()["count"]
        memory_count = db.execute("SELECT COUNT(*) AS count FROM memories WHERE user_id IS NULL").fetchone()["count"]
    return {"tasks": task_count, "memories": memory_count}


def import_legacy_data(user_id, legacy_memories=()):
    if not isinstance(legacy_memories, (list, tuple)):
        raise ValueError("Legacy memories must be a list.")
    prepared_memories = []
    for memory in legacy_memories:
        if not isinstance(memory, dict):
            raise ValueError("A legacy memory entry is invalid.")
        person, object_name, time_value = (
            memory.get("person"),
            memory.get("object"),
            memory.get("time"),
        )
        if not all(isinstance(value, str) and value.strip() for value in (person, object_name, time_value)):
            raise ValueError("A legacy memory entry is missing person, object, or time.")
        prepared_memories.append((person.strip(), object_name.strip(), time_value.strip()))

    with connection() as db:
        _begin_user_write(db, user_id)
        task_count = db.execute("SELECT COUNT(*) AS count FROM tasks WHERE user_id IS NULL").fetchone()["count"]
        memory_count = db.execute("SELECT COUNT(*) AS count FROM memories WHERE user_id IS NULL").fetchone()["count"]
        db.execute("UPDATE tasks SET user_id = ? WHERE user_id IS NULL", (user_id,))
        db.execute("UPDATE task_proposals SET user_id = ? WHERE user_id IS NULL", (user_id,))
        db.execute("UPDATE memories SET user_id = ? WHERE user_id IS NULL", (user_id,))
        for person, object_name, time_value in prepared_memories:
            existing = db.execute(
                "SELECT id FROM memories WHERE user_id = ? AND lower(person) = lower(?) "
                "AND lower(object) = lower(?)",
                (user_id, person, object_name),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE memories SET time = ?, updated_at = ? WHERE id = ?",
                    (time_value, _now(), existing["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO memories (id, user_id, person, object, time, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), user_id, person, object_name, time_value, _now(), _now()),
                )
    return {"tasks": task_count, "memories": memory_count + len(prepared_memories)}
