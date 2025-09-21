from __future__ import annotations
import sqlite3

SQL_CREATE = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER,
    chat_title TEXT,
    chat_username TEXT,
    date TEXT,
    message_id INTEGER,
    text TEXT,
    lang TEXT,
    matched_keywords TEXT,
    score INTEGER,
    url TEXT,
    text_ja TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_chat_msg ON messages(chat_id, message_id);

CREATE TABLE IF NOT EXISTS state (
    chat_id INTEGER PRIMARY KEY,
    last_msg_id INTEGER,
    last_date TEXT
);
"""

UPSERT_MSG_SQL = """
INSERT INTO messages(
  id, chat_id, chat_title, chat_username, date, message_id, text, lang, matched_keywords, score, url, text_ja
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  chat_title       = excluded.chat_title,
  chat_username    = excluded.chat_username,
  date             = excluded.date,
  text             = excluded.text,
  lang             = excluded.lang,
  matched_keywords = excluded.matched_keywords,
  score            = excluded.score,
  url              = excluded.url,
  text_ja          = CASE
                       WHEN (excluded.text_ja IS NOT NULL AND excluded.text_ja <> '')
                       THEN excluded.text_ja
                       ELSE messages.text_ja
                     END;
"""

UPSERT_STATE_SQL = """
INSERT INTO state(chat_id, last_msg_id, last_date)
VALUES (?, ?, ?)
ON CONFLICT(chat_id) DO UPDATE SET
  last_msg_id = CASE WHEN excluded.last_msg_id > state.last_msg_id OR state.last_msg_id IS NULL
                     THEN excluded.last_msg_id ELSE state.last_msg_id END,
  last_date   = CASE WHEN excluded.last_msg_id > state.last_msg_id OR state.last_msg_id IS NULL
                     THEN excluded.last_date   ELSE state.last_date   END;
"""

def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-20000;")
    conn.executescript(SQL_CREATE)
    if not _column_exists(conn, "messages", "text_ja"):
        conn.execute("ALTER TABLE messages ADD COLUMN text_ja TEXT;")
    return conn

def get_last_seen(conn: sqlite3.Connection, chat_id: int) -> int:
    cur = conn.execute("SELECT last_msg_id FROM state WHERE chat_id = ?", (chat_id,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0

def is_already_scored(conn: sqlite3.Connection, chat_id: int, message_id: int) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM messages WHERE chat_id = ? AND message_id = ? LIMIT 1",
        (chat_id, message_id),
    )
    return cur.fetchone() is not None

def persist_message(conn: sqlite3.Connection, chat_id: int, title: str, username: str,
                    msg_id: int, date_utc: str, text: str, lang: str,
                    matched_keywords_json: str, score: int, url: str, text_ja: str) -> None:
    pk = (hash((chat_id, msg_id)) & 0x7fffffff)
    conn.execute(UPSERT_MSG_SQL, (
        pk, chat_id, title, username, date_utc, msg_id, text,
        lang, matched_keywords_json, score, url, text_ja
    ))
    conn.execute(UPSERT_STATE_SQL, (chat_id, msg_id, date_utc))
