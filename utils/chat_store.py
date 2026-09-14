import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from utils import database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(title: Optional[str] = None) -> str:
    """Create a new chat session in SQLite and return its session ID."""
    session_id = str(uuid.uuid4())
    now = _now_iso()
    session_title = (title or "New Chat").strip()

    with database.db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_sessions (id, title, created_at, updated_at, is_pinned)
            VALUES (?, ?, ?, ?, 0)
            """,
            (session_id, session_title, now, now),
        )

    return session_id


def prune_empty_sessions(exclude_id: Optional[str] = None):
    """Remove any orphaned sessions that contain no messages, preserving the active session if specified."""
    with database.db.cursor() as cur:
        if exclude_id:
            cur.execute(
                """
                DELETE FROM chat_sessions
                WHERE id != ? AND id NOT IN (SELECT DISTINCT session_id FROM chat_messages)
                """,
                (exclude_id,),
            )
        else:
            cur.execute(
                """
                DELETE FROM chat_sessions
                WHERE id NOT IN (SELECT DISTINCT session_id FROM chat_messages)
                """
            )


def list_sessions(include_empty: bool = False, active_session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List chat sessions ordered by updated_at descending (only sessions with messages by default, or the active session)."""
    with database.db.cursor() as cur:
        if include_empty:
            cur.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, s.is_pinned,
                       COUNT(m.id) as msg_count
                FROM chat_sessions s
                LEFT JOIN chat_messages m ON s.id = m.session_id
                GROUP BY s.id
                ORDER BY s.is_pinned DESC, s.updated_at DESC
                """
            )
        elif active_session_id:
            cur.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, s.is_pinned,
                       COUNT(m.id) as msg_count
                FROM chat_sessions s
                LEFT JOIN chat_messages m ON s.id = m.session_id
                GROUP BY s.id
                HAVING COUNT(m.id) > 0 OR s.id = ?
                ORDER BY s.is_pinned DESC, s.updated_at DESC
                """,
                (active_session_id,),
            )
        else:
            cur.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, s.is_pinned,
                       COUNT(m.id) as msg_count
                FROM chat_sessions s
                JOIN chat_messages m ON s.id = m.session_id
                GROUP BY s.id
                HAVING COUNT(m.id) > 0
                ORDER BY s.is_pinned DESC, s.updated_at DESC
                """
            )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "title": r[1],
            "created_at": r[2],
            "updated_at": r[3],
            "is_pinned": bool(r[4]),
            "msg_count": r[5],
        }
        for r in rows
    ]


def search_sessions(query: str) -> List[Dict[str, Any]]:
    """Search chat sessions by title (case-insensitive substring match)."""
    with database.db.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.is_pinned,
                   COUNT(m.id) as msg_count
            FROM chat_sessions s
            JOIN chat_messages m ON s.id = m.session_id
            WHERE LOWER(s.title) LIKE ?
            GROUP BY s.id
            HAVING COUNT(m.id) > 0
            ORDER BY s.is_pinned DESC, s.updated_at DESC
            """,
            (f"%{query.lower().strip()}%",),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "title": r[1],
            "created_at": r[2],
            "updated_at": r[3],
            "is_pinned": bool(r[4]),
            "msg_count": r[5],
        }
        for r in rows
    ]


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a single session."""
    with database.db.cursor() as cur:
        cur.execute(
            "SELECT id, title, created_at, updated_at, is_pinned FROM chat_sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "title": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "is_pinned": bool(row[4]),
    }


def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
    """Load all messages for a session from SQLite, formatted for Streamlit."""
    with database.db.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT id, role, content, reasoning, monologue, tool_calls, file_path, provider, model, token_usage, timestamp, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
            schema_version = 3
        except Exception:
            try:
                cur.execute(
                    """
                    SELECT id, role, content, reasoning, monologue, tool_calls, file_path, timestamp, created_at
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                schema_version = 2
            except Exception:
                cur.execute(
                    """
                    SELECT id, role, content, reasoning, tool_calls, file_path, timestamp, created_at
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                schema_version = 1

    messages = []
    for r in rows:
        if schema_version == 3:
            msg_id, role, content, reasoning, monologue, tool_calls_raw, file_path_raw, provider, model, token_usage_raw, timestamp, created_at = r
        elif schema_version == 2:
            msg_id, role, content, reasoning, monologue, tool_calls_raw, file_path_raw, timestamp, created_at = r
            provider = ""
            model = ""
            token_usage_raw = None
        else:
            msg_id, role, content, reasoning, tool_calls_raw, file_path_raw, timestamp, created_at = r
            monologue = ""
            provider = ""
            model = ""
            token_usage_raw = None

        tool_calls = []
        if tool_calls_raw:
            try:
                tool_calls = json.loads(tool_calls_raw)
            except Exception:
                tool_calls = []

        file_path = None
        if file_path_raw:
            try:
                file_path = json.loads(file_path_raw)
            except Exception:
                file_path = file_path_raw

        token_usage = None
        if token_usage_raw:
            try:
                token_usage = json.loads(token_usage_raw)
            except Exception:
                token_usage = None

        msg_dict = {
            "id": msg_id,
            "role": role,
            "content": content,
            "reasoning": reasoning or "",
            "monologue": monologue or "",
            "tool_calls": tool_calls,
            "file_path": file_path,
            "provider": provider or "",
            "model": model or "",
            "token_usage": token_usage,
            "timestamp": timestamp,
        }
        messages.append(msg_dict)

    return messages


def add_message(
    session_id: str,
    role: str,
    content: str,
    reasoning: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    file_path: Any = None,
    timestamp: Optional[str] = None,
    monologue: str = "",
    provider: str = "",
    model: str = "",
    token_usage: Optional[Dict[str, Any]] = None,
) -> str:
    """Save a user or assistant message into SQLite and update session metadata."""
    msg_id = str(uuid.uuid4())
    now = _now_iso()
    ts = timestamp or datetime.now().strftime("%B %d, %Y %H:%M:%S")

    tool_calls_json = json.dumps(tool_calls or []) if tool_calls else None
    if isinstance(file_path, (list, dict)):
        file_path_json = json.dumps(file_path)
    elif file_path:
        file_path_json = str(file_path)
    else:
        file_path_json = None

    token_usage_json = json.dumps(token_usage) if token_usage else None

    with database.db.cursor() as cur:
        # Guarantee session exists in chat_sessions to prevent foreign key constraint violations
        cur.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
        if not cur.fetchone():
            default_title = "New Chat"
            if role == "user" and content:
                words = content.strip().replace("\n", " ").split()
                if words:
                    default_title = " ".join(words[:6])
                    if len(default_title) > 40:
                        default_title = default_title[:37] + "..."
            cur.execute(
                """
                INSERT INTO chat_sessions (id, title, created_at, updated_at, is_pinned)
                VALUES (?, ?, ?, ?, 0)
                """,
                (session_id, default_title, now, now),
            )

        # Prefer full schema with provider, model, token_usage;
        # fall back to previous schemas for older DBs.
        try:
            cur.execute(
                """
                INSERT INTO chat_messages (id, session_id, role, content, reasoning, monologue, tool_calls, file_path, provider, model, token_usage, timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg_id,
                    session_id,
                    role,
                    content,
                    reasoning or "",
                    monologue or "",
                    tool_calls_json,
                    file_path_json,
                    provider or "",
                    model or "",
                    token_usage_json,
                    ts,
                    now,
                ),
            )
        except Exception:
            try:
                cur.execute(
                    """
                    INSERT INTO chat_messages (id, session_id, role, content, reasoning, monologue, tool_calls, file_path, timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        session_id,
                        role,
                        content,
                        reasoning or "",
                        monologue or "",
                        tool_calls_json,
                        file_path_json,
                        ts,
                        now,
                    ),
                )
            except Exception:
                cur.execute(
                    """
                    INSERT INTO chat_messages (id, session_id, role, content, reasoning, tool_calls, file_path, timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        session_id,
                        role,
                        content,
                        reasoning or "",
                        tool_calls_json,
                        file_path_json,
                        ts,
                        now,
                    ),
                )

        # Update session updated_at
        cur.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )

        # If this is a user message and session has default title, auto-generate a meaningful title
        if role == "user":
            cur.execute("SELECT title FROM chat_sessions WHERE id = ?", (session_id,))
            res = cur.fetchone()
            if res and res[0] in ("New Chat", "Default Chat"):
                clean_text = content.strip().replace("\n", " ")
                # Truncate to ~6-8 words or max 40 chars
                words = clean_text.split()
                if words:
                    new_title = " ".join(words[:6])
                    if len(new_title) > 40:
                        new_title = new_title[:37] + "..."
                    cur.execute(
                        "UPDATE chat_sessions SET title = ? WHERE id = ?",
                        (new_title, session_id),
                    )

    return msg_id


def update_session_title(session_id: str, new_title: str):
    """Rename a chat session."""
    now = _now_iso()
    title = (new_title or "Untitled Chat").strip()
    with database.db.cursor() as cur:
        cur.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )


def delete_session(session_id: str):
    """Delete a chat session and all its messages (via ON DELETE CASCADE)."""
    with database.db.cursor() as cur:
        cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        cur.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))


def ensure_active_session() -> Optional[str]:
    """Returns the latest active session ID if one exists, otherwise None."""
    sessions = list_sessions()
    if sessions:
        return sessions[0]["id"]
    return None

