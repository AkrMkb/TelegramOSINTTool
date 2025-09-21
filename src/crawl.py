from __future__ import annotations
import time, heapq, re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from telethon import types, functions
from config import Config
from discovery import get_entity_safe, passes_channel_filters
from discovery_guard import (
    probe_channel_quality, pass_quality_gates,
    mark_low_quality, is_low_quality_blocked
)
from scoring import extract_text
from util_channels import is_blocked

MENTION_RE  = re.compile(r"@([A-Za-z0-9_]{4,32})")
TME_RE      = re.compile(r"https?://t\.me/([A-Za-z0-9_+]{4,64})(?:/\d+)?")

def extract_candidates_from_text(text: str) -> List[str]:
    t = (text or "")
    users = [f"@{m}" for m in MENTION_RE.findall(t)]
    links = [m for m in TME_RE.findall(t)]
    links_norm = [f"https://t.me/{l}" for l in links]
    return sorted(set(users + links_norm))

def _extract_username(ref: str) -> str:
    if ref.startswith("@"):
        return ref[1:]
    if "t.me/" in ref:
        tail = ref.split("t.me/", 1)[1].strip("/")
        return tail.split("/", 1)[0]
    return ""

@dataclass(order=True)
class PQItem:
    priority: float
    depth: int
    ref: str = field(compare=False)
    seed: bool = field(compare=False)
    # 評価結果を覚えておきたいなら以下を追加
    # hit_rate: float = field(default=0.0, compare=False)

def compute_priority(*, hit_rate: float, depth: int, seed: bool, recent_bonus: float,
                     w_hit_rate: float, w_depth: float, w_seed_bonus: float, w_recent_bonus: float) -> float:
    """小さいほど先に処理される。良い指標には負の重みをかける。"""
    pr = 0.0
    pr += w_hit_rate * hit_rate
    pr += w_depth * depth
    pr += w_seed_bonus * (1.0 if seed else 0.0)
    pr += w_recent_bonus * recent_bonus
    return pr

async def ensure_join(client, ref: str, cfg: Config, debug=False):
    try:
        ref = ref.strip()
        if ref.startswith("http") and "t.me/" in ref:
            tail = ref.split("t.me/", 1)[1].strip("/")
            if tail.startswith("+"):
                invite_hash = tail.lstrip("+")
                try:
                    await client(functions.messages.ImportChatInviteRequest(hash=invite_hash))
                except Exception:
                    pass
                return
            username = tail.split("/", 1)[0]
            if username:
                ent = await get_entity_safe(client, f"@{username}", cfg, debug=debug)
                if ent and isinstance(ent, (types.Channel, types.Chat)):
                    try:
                        await client(functions.channels.JoinChannelRequest(ent))
                    except Exception:
                        pass
                return
        else:
            ent = await get_entity_safe(client, ref, cfg, debug=debug)
            if ent and isinstance(ent, (types.Channel, types.Chat)):
                try:
                    await client(functions.channels.JoinChannelRequest(ent))
                except Exception:
                    pass
    except Exception:
        pass

async def discover_by_crawl(client, cfg: Config, seeds: List[str], debug=False) -> List[str]:
    if not cfg.discovery.crawl.enabled:
        return []

    start = time.monotonic()
    visited: set[str] = set()
    found: set[str]   = set()

    pq: List[PQItem] = []
    seed_set = set(seeds or [])
    for s in sorted(set(seeds or [])):
        heapq.heappush(pq, PQItem(
            priority=compute_priority(
                hit_rate=0.0, depth=0, seed=True, recent_bonus=0.0,
                w_hit_rate=getattr(cfg.discovery.crawl, "w_hit_rate", -1.0),
                w_depth=getattr(cfg.discovery.crawl, "w_depth", 0.3),
                w_seed_bonus=getattr(cfg.discovery.crawl, "w_seed_bonus", -0.5),
                w_recent_bonus=getattr(cfg.discovery.crawl, "w_recent_bonus", -0.2),
            ),
            depth=0, ref=s, seed=True
        ))

    max_depth = max(0, cfg.discovery.crawl.max_depth)
    max_channels = max(1, cfg.discovery.crawl.max_channels)
    allow_types = set([t.lower() for t in cfg.discovery.crawl.allow_types])
    sample_n = getattr(cfg.discovery.crawl, "sample_messages", 50)
    per_channel_timeout = getattr(cfg.discovery.crawl, "per_channel_time_limit_s", 20)
    cooldown_s = getattr(cfg.discovery.crawl, "low_quality_cooldown_s", 86400)

    while pq and len(found) < max_channels:
        # グローバル時間制限
        if (time.monotonic() - start) > cfg.discovery.crawl.global_time_limit_s:
            if debug:
                print(f"[crawl] reached global time limit {cfg.discovery.crawl.global_time_limit_s}s")
            break

        item = heapq.heappop(pq)
        ref, depth, is_seed = item.ref, item.depth, item.seed

        if ref in visited:
            continue
        visited.add(ref)
        uname0 = _extract_username(ref)
        if is_blocked(uname0, cfg):
            if debug:
                print(f"[crawl] skip @{uname0}: block_channels (pre-join)")
            continue

        await ensure_join(client, ref, cfg, debug=debug)
        entity = await get_entity_safe(client, ref, cfg, debug=debug)
        if not entity:
            continue

        uname = getattr(entity, "username", "") or ""
        if is_blocked(uname, cfg):
            if debug:
                print(f"[crawl] skip @{uname}: block_channels (post-resolve)")
            continue

        chat_id = getattr(entity, "id", None)
        if chat_id is not None and is_low_quality_blocked(chat_id):
            if debug:
                print(f"[crawl] skip low-quality cooled-down entity {ref}")
            continue

        etype = entity.__class__.__name__.lower()
        if "channel" in etype:
            etype = "channel"
        elif "megagroup" in etype or "supergroup" in etype or "chat" in etype:
            etype = "supergroup"
        if allow_types and etype not in allow_types:
            continue
        if not await passes_channel_filters(client, cfg, entity, debug=debug):
            continue

        t0 = time.monotonic()
        probe = await probe_channel_quality(client, cfg, entity, sample_messages=sample_n)
        ok, reason = pass_quality_gates(probe, cfg)
        if debug:
            name = getattr(entity, "username", "") or getattr(entity, "title", "")
            print(f"[probe] {name} depth={depth} n={probe.total} "
                  f"hit={probe.hit_rate:.2f} neg={probe.negative_rate:.2f} "
                  f"langT={probe.target_lang_rate:.2f} avglen={probe.avg_len:.1f} -> {ok} ({reason})")

        recent_bonus = 1.0 if probe.total > 0 else 0.0

        if not ok:
            if chat_id is not None:
                mark_low_quality(chat_id, cooldown_s)
            continue

        if getattr(entity, "username", None):
            found.add(f"@{entity.username}")

        if (time.monotonic() - t0) > per_channel_timeout:
            if debug:
                print(f"[probe] timeout on {ref}, skip expanding neighbors")
            continue

        if depth >= max_depth:
            continue

        try:
            next_refs: list[str] = []
            async for msg in client.iter_messages(entity, limit=200):
                text = extract_text(msg)
                if not text:
                    continue
                # blocklist_keywords でノイズ除去
                if any(b.lower() in text.lower() for b in (cfg.discovery.crawl.blocklist_keywords or [])):
                    continue
                next_refs.extend(extract_candidates_from_text(text))
            next_refs = sorted(set(next_refs))
        except Exception:
            next_refs = []

        for nr in next_refs:
            if nr in visited:
                continue
            nu = _extract_username(nr)
            if is_blocked(nu, cfg):
                if debug:
                    print(f"[crawl] neighbor skip @{nu}: block_channels")
                continue

            pr = compute_priority(
                hit_rate=probe.hit_rate,
                depth=depth + 1,
                seed=(nr in seed_set),
                recent_bonus=recent_bonus,
                w_hit_rate=getattr(cfg.discovery.crawl, "w_hit_rate", -1.0),
                w_depth=getattr(cfg.discovery.crawl, "w_depth", 0.3),
                w_seed_bonus=getattr(cfg.discovery.crawl, "w_seed_bonus", -0.5),
                w_recent_bonus=getattr(cfg.discovery.crawl, "w_recent_bonus", -0.2),
            )
            heapq.heappush(pq, PQItem(priority=pr, depth=depth + 1, ref=nr, seed=(nr in seed_set)))

    return sorted(found)
