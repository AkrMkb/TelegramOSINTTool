from __future__ import annotations

import datetime as dt
import sqlite3
from typing import List, Optional

from telethon import events

from config import Config
from db import persist_message, is_already_scored
from scoring import extract_text, score_text, detect_lang_safe, matched_to_json
from translate import translate_to_ja
# from alerts import slack_notify 
from util_channels import is_blocked

import asyncio


class LiveStream:
    """
    TelethonのNewMessage監視を「開始/停止」できるように包んだクラス。
    - 既にDBにあるメッセージは is_already_scored でスキップ
    - negatives / score_threshold 適用
    - is_blocked(username) でチャンネル除外
    - 日本語訳は失敗しても空文字で継続
    """
    def __init__(
        self,
        client,
        cfg: Config,
        conn: sqlite3.Connection,
        target_entities: Optional[List[object]] = None,
        debug: bool = False,
    ):
        self.client = client
        self.cfg = cfg
        self.conn = conn
        self.target_entities = target_entities
        self.debug = debug

        self._stop_evt = asyncio.Event()
        self._handler_ref = None

    async def _handler(self, event):
        try:
            msg = event.message
            chat = await event.get_chat()

            if is_already_scored(self.conn, chat.id, msg.id):
                if self.debug:
                    print(f"[skip-live] already-scored chat_id={chat.id} id={msg.id}")
                return

            text = extract_text(msg)
            if not text:
                return

            s = score_text(text, self.cfg.keywords, self.cfg.negatives)
            if s.score < self.cfg.score_threshold:
                if self.debug:
                    print(f"[skip-live] low score {s.score} chat_id={chat.id} id={msg.id}")
                return

            title = getattr(chat, "title", "") or getattr(chat, "first_name", "")
            username = getattr(chat, "username", "") or ""

            if is_blocked(username, self.cfg):
                if self.debug:
                    print(f"[skip-live] blocked @{username} id={msg.id}")
                return

            url = f"https://t.me/{username}/{msg.id}" if username else ""
            date_utc = msg.date.replace(tzinfo=dt.timezone.utc).isoformat()

            try:
                lang_hint = detect_lang_safe(text)
            except Exception:
                lang_hint = "und"

            try:
                text_ja = translate_to_ja(text, lang_hint, self.cfg)
            except Exception:
                text_ja = ""

            try:
                persist_message(
                    self.conn,
                    chat_id=chat.id,
                    title=title,
                    username=username,
                    msg_id=msg.id,
                    date_utc=date_utc,
                    text=text,
                    lang=lang_hint,
                    matched_keywords_json=matched_to_json(s),
                    score=s.score,
                    url=url,
                    text_ja=text_ja,
                )
                self.conn.commit()
            except sqlite3.IntegrityError:
                pass

            if self.debug:
                print(f"[LIVE-HIT] score={s.score} kw={s.matched} chat={title} id={msg.id} url={url}")

            # ToDO: 任意のSlack通知
            # if getattr(self.cfg.alerts, "slack_webhook", ""):
            #     ts = msg.date.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            #     slack_notify(
            #         self.cfg.alerts.slack_webhook,
            #         f"[TELE-OSINT] hit score={s.score} {s.matched}\n"
            #         f"chat={title} (@{username})\n"
            #         f"time={ts}\nurl={url}\n{text[:500]}",
            #     )

        except Exception as e:
            if self.debug:
                print(f"[err-live] {e}")

    async def start(self):
        """ハンドラを登録し、停止要求が来るまで待機します。"""
        self._handler_ref = self._handler

        if self.target_entities:
            self.client.add_event_handler(self._handler_ref, events.NewMessage(chats=self.target_entities))
        else:
            self.client.add_event_handler(self._handler_ref, events.NewMessage())

        print("[run] listening…")
        try:
            await self._stop_evt.wait()
        finally:
            if self._handler_ref:
                try:
                    self.client.remove_event_handler(self._handler_ref, events.NewMessage)
                except Exception:
                    pass
                self._handler_ref = None
            self._stop_evt.clear()

    async def stop(self):
        """停止イベントを発火して start() の待ちを解除します。"""
        self._stop_evt.set()


async def run_stream(
    client,
    cfg: Config,
    conn: sqlite3.Connection,
    target_entities: Optional[List[object]] = None,
    debug: bool = False,
) -> None:
    live = LiveStream(client, cfg, conn, target_entities=target_entities, debug=debug)
    await live.start()
