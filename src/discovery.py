from __future__ import annotations
import asyncio
from typing import List
from telethon import functions, types
from telethon.errors import FloodWaitError
from config import Config
from util_channels import is_blocked

DIALOG_CACHE: dict[str, object] = {}

async def build_dialog_cache(client, debug=False) -> None:
    global DIALOG_CACHE
    count = 0
    async for d in client.iter_dialogs():
        ent = d.entity
        uname = getattr(ent, "username", None)
        if uname:
            DIALOG_CACHE[uname.lower()] = ent
            count += 1
    if debug:
        print(f"[cache] dialogs cached: {count}")

async def get_entity_safe(client, ref: str, cfg: Config, debug=False):
    key = None
    if ref.startswith("@"):
        key = ref[1:].lower()
    elif ref.startswith("http") and "t.me/" in ref:
        tail = ref.split("t.me/", 1)[1].strip("/")
        if tail and not tail.startswith("+"):
            key = tail.split("/", 1)[0].lower()
    if key and key in DIALOG_CACHE:
        return DIALOG_CACHE[key]

    try:
        return await client.get_entity(ref)
    except FloodWaitError as e:
        wait_s = int(e.seconds)
        if wait_s <= cfg.discovery.crawl.max_wait_on_flood_s:
            if debug:
                print(f"[entity] floodwait {wait_s}s on {ref}")
            await asyncio.sleep(wait_s + cfg.discovery.crawl.floodwait_padding_s)
            try:
                return await client.get_entity(ref)
            except Exception:
                return None
        else:
            if debug:
                print(f"[entity] SKIP {ref} due to huge FloodWait {wait_s}s")
            return None
    except Exception:
        return None

async def passes_channel_filters(client, cfg: Config, entity, debug=False) -> bool:
    f = cfg.discovery.filters
    title = (getattr(entity, 'title', '') or '').lower()
    uname = (getattr(entity, 'username', '') or '').lower()
    if not uname:
        return False
    
    if is_blocked(uname, cfg):
        if debug:
            print(f"[discover] block @{uname}")
        return False

    if f.name_must_include:
        if not any(s.lower() in title or s.lower() in uname for s in f.name_must_include):
            return False

    import re
    for pat in f.username_block_patterns or []:
        try:
            if re.search(pat, uname or ""):
                return False
        except re.error:
            pass

    if f.min_members:
        try:
            full = await client(functions.channels.GetFullChannelRequest(entity))
            members = getattr(full.full_chat, "participants_count", 0)
            if members < int(f.min_members):
                return False
        except Exception:
            pass

    return True

async def discover_public_channels(client, cfg: Config) -> List[str]:
    found_usernames: List[str] = []
    total = len(cfg.discovery.queries)
    for i, q in enumerate(cfg.discovery.queries, 1):
        try:
            res = await asyncio.wait_for(
                client(functions.contacts.SearchRequest(q=q, limit=cfg.discovery.limit_per_query)),
                timeout=15
            )
            for c in list(res.chats) + list(res.users):
                if isinstance(c, types.Channel) and getattr(c, 'username', None):
                    ent = await get_entity_safe(client, f"@{c.username}", cfg)
                    if not ent:
                        continue
                    ok = await passes_channel_filters(client, cfg, ent)
                    if not ok:
                        continue
                    found_usernames.append(f"@{c.username}")
            print(f"[discover] {i}/{total} done: '{q}' -> {len(found_usernames)} total")
        except asyncio.TimeoutError:
            print(f"[discover] {i}/{total} timeout on '{q}', skip")
            continue
        except FloodWaitError as e:
            wait_s = int(e.seconds)
            if wait_s <= cfg.discovery.crawl.max_wait_on_flood_s:
                print(f"[discover] floodwait {wait_s}s on '{q}'")
                await asyncio.sleep(wait_s + cfg.discovery.crawl.floodwait_padding_s)
            else:
                print(f"[discover] skip '{q}' due to huge floodwait {wait_s}s")
            continue
        except Exception as ex:
            print(f"[discover] {i}/{total} err on '{q}': {ex}")
            continue
    return sorted(set(found_usernames))


LOW_QUALITY_UNTIL: dict[int, float] = {}  # chat_id -> unix_ts

def mark_low_quality(chat_id: int, cooldown_s: int):
    import time
    LOW_QUALITY_UNTIL[chat_id] = time.time() + cooldown_s

def is_low_quality_blocked(chat_id: int) -> bool:
    import time
    until = LOW_QUALITY_UNTIL.get(chat_id, 0)
    return time.time() < until