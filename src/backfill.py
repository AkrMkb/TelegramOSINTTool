from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict

from config import Config
from db import get_last_seen, persist_message, is_already_scored
from scoring import extract_text, score_text, detect_lang_safe, matched_to_json
from translate import translate_to_ja
from discovery import get_entity_safe
from util_channels import is_blocked


async def backfill_channel(
    client,
    cfg: Config,
    conn: sqlite3.Connection,
    chat: str,
    new_only: bool = False,
    debug: bool = False,
) -> None:
    """
    指定チャネルの履歴取得。
    - new_only=True の場合は state.last_msg_id 以降のみ取得
    - 既にDBにあるメッセージ（=スコア済み）は is_already_scored でスキップ
    - スコア閾値未満は保存しない
    """

    entity = await get_entity_safe(client, chat, cfg, debug=debug)
    if not entity:
        if debug:
            print(f"[backfill] skip {chat}: unresolved")
        return

    title = getattr(entity, "title", "") or getattr(entity, "first_name", "")
    username = getattr(entity, "username", None)
    
    if is_blocked(username, cfg):
        if debug:
            print(f"[backfill] skip @{username}: blocked")
        return
    last_seen = get_last_seen(conn, entity.id) if new_only else 0
    kwargs: Dict[str, Any] = {"limit": cfg.collect.backfill_limit}
    if new_only and last_seen > 0:
        kwargs["min_id"] = last_seen

    count_total = 0
    count_hits = 0
    count_skipped_scored = 0
    count_low_score = 0

    async for msg in client.iter_messages(entity, **kwargs):
        count_total += 1

        if new_only and last_seen and msg.id <= last_seen:
            if debug:
                print(f"[skip-old] chat={title} id={msg.id} <= last_seen={last_seen}")
            continue

        if is_already_scored(conn, entity.id, msg.id):
            count_skipped_scored += 1
            if debug:
                print(f"[skip-bf] already-scored chat={title} id={msg.id}")
            continue

        text = extract_text(msg)
        if not text:
            continue

        s = score_text(text, cfg.keywords, cfg.negatives)
        if s.score < cfg.score_threshold:
            count_low_score += 1
            if debug:
                print(f"[skip] low score {s.score} chat={title} id={msg.id}")
            continue

        url = f"https://t.me/{username}/{msg.id}" if username else ""
        date_utc = msg.date.replace(tzinfo=dt.timezone.utc).isoformat()

        try:
            lang_hint = detect_lang_safe(text)
        except Exception:
            lang_hint = "und"

        try:
            text_ja = translate_to_ja(text, lang_hint, cfg)
        except Exception:
            text_ja = ""

        try:
            persist_message(
                conn,
                entity.id,
                title,
                username or "",
                msg.id,
                date_utc,
                text,
                lang_hint,
                matched_to_json(s),
                s.score,
                url,
                text_ja,
            )
            conn.commit()
            count_hits += 1
            if debug:
                print(f"[HIT] score={s.score} kw={s.matched} chat={title} id={msg.id} url={url}")
        except sqlite3.IntegrityError:
            pass

    if debug:
        print(
            f"[backfill-summary] chat={title} total={count_total} "
            f"hits={count_hits} skipped_scored={count_skipped_scored} low_score={count_low_score}"
        )
