from __future__ import annotations
import asyncio
from pathlib import Path
import sqlite3
from typing import List, Optional
import time

from telethon import TelegramClient

from config import Config, load_config
from db import open_db
from scoring import init_keywords_fast_pattern
from discovery import build_dialog_cache, discover_public_channels, get_entity_safe
from crawl import ensure_join, discover_by_crawl
from backfill import backfill_channel
from stream import LiveStream


class TeleOsintApp:
    def __init__(self, cfg: Config, client: TelegramClient, conn: sqlite3.Connection):
        self.cfg = cfg
        self.client = client
        self.conn = conn

        self._maint_lock = asyncio.Lock()
        self._maint_last_started: float = 0.0

        self._live_obj: Optional[LiveStream] = None
        self._live_task: Optional[asyncio.Task] = None
        self._live_entities: Optional[List[object]] = None

        self._maint_task: Optional[asyncio.Task] = None

    async def init_runtime(self, debug: bool = False):
        await build_dialog_cache(self.client, debug=debug)

    async def discover(self, debug: bool = False) -> List[str]:
        return await discover_public_channels(self.client, self.cfg)

    async def crawl(self, seeds: List[str], debug: bool = False) -> List[str]:
        return await discover_by_crawl(self.client, self.cfg, seeds=seeds, debug=debug)

    async def join_targets(self, targets: List[str], debug: bool = False) -> None:
        for ref in targets:
            await ensure_join(self.client, ref, self.cfg, debug=debug)

    async def entities_from_refs(self, refs: List[str], debug: bool = False) -> List[object]:
        ents: List[object] = []
        for ref in refs:
            ent = await get_entity_safe(self.client, ref, self.cfg, debug=debug)
            if ent:
                ents.append(ent)
        return ents

    async def backfill_targets(self, refs: List[str], new_only: bool, debug: bool = False) -> None:
        mode = "new-only" if new_only else "all"
        for ref in refs:
            try:
                print(f"[backfill-{mode}] {ref}")
                await backfill_channel(self.client, self.cfg, self.conn, ref, new_only=new_only, debug=debug)
            except Exception as e:
                print(f"[backfill] skip {ref}: {e}")

    async def start_live(self, entities: Optional[List[object]] = None, debug: bool = False):
        if self._live_task and not self._live_task.done():
            return
        self._live_entities = entities
        self._live_obj = LiveStream(self.client, self.cfg, self.conn, target_entities=entities, debug=debug)
        self._live_task = asyncio.create_task(self._live_obj.start())

    async def stop_live(self):
        if self._live_obj:
            await self._live_obj.stop()
        if self._live_task:
            try:
                await asyncio.wait_for(self._live_task, timeout=10)
            except asyncio.TimeoutError:
                self._live_task.cancel()
            finally:
                self._live_task = None
                self._live_obj = None

    async def run_live(self, entities: Optional[List[object]] = None, debug: bool = False):
        await self.start_live(entities=entities, debug=debug)
        if self._live_task:
            await self._live_task

    async def maintenance_once(self, debug: bool = False) -> None:
        seeds = list(set(self.cfg.seed_channels or []))

        found: List[str] = []
        if getattr(getattr(self.cfg, "maintenance", None), "run_discover", True):
            print("[maint] discover…")
            found = await self.discover(debug=debug)

        crawl_found: List[str] = []
        if getattr(getattr(self.cfg, "maintenance", None), "run_crawl", True):
            print("[maint] crawl…")
            crawl_found = await self.crawl(sorted(set(seeds + found)), debug=debug)

        targets = sorted(set(seeds + found + crawl_found))
        if targets:
            print(f"[maint] targets: {len(targets)}")
            await self.join_targets(targets, debug=debug)

            if getattr(getattr(self.cfg, "maintenance", None), "backfill_new_only", True):
                await self.backfill_targets(targets, new_only=True, debug=debug)

        try:
            self._live_entities = await self.entities_from_refs(targets, debug=debug)
        except Exception:
            pass

    async def maintenance_loop(self, debug: bool = False) -> None:
        """一定間隔で「ライブ停止→メンテ→ライブ再開」"""
        interval = int(getattr(getattr(self.cfg, "maintenance", None), "interval_sec", 0) or 0)
        if interval <= 0:
            print("[maint] disabled")
            return

        print(f"[maint] loop enabled: every {interval}s")
        while True:
            now = time.monotonic()
            async with self._maint_lock:
                if now - self._maint_last_started >= interval:
                    self._maint_last_started = now
                    try:
                        print("[maint] stop live…")
                        await self.stop_live()

                        print("[maint] run maintenance…")
                        await self.maintenance_once(debug=debug)

                        print("[maint] restart live…")
                        await self.start_live(entities=self._live_entities, debug=debug)
                    except Exception as e:
                        print(f"[maint] error: {e}")
            await asyncio.sleep(5)

    async def start_maintenance_background(self, debug: bool = False):
        if self._maint_task and not self._maint_task.done():
            return
        self._maint_task = asyncio.create_task(self.maintenance_loop(debug=debug))

    async def shutdown(self):
        await self.stop_live()
        if self._maint_task:
            self._maint_task.cancel()
            try:
                await self._maint_task
            except Exception:
                pass


async def create_app(config_path: str) -> TeleOsintApp:
    cfg = load_config(config_path)
    init_keywords_fast_pattern(cfg.keywords)

    Path(cfg.session).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    conn = open_db(cfg.sqlite_path)
    client = TelegramClient(cfg.session, cfg.api_id, cfg.api_hash)
    await client.start()
    return TeleOsintApp(cfg=cfg, client=client, conn=conn)
