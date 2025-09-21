from __future__ import annotations
from typing import Optional
from config import Config

def _norm(u: Optional[str]) -> str:
    if not u:
        return ""
    return u.lstrip("@").strip().lower()

def is_blocked(username: Optional[str], cfg: Config) -> bool:
    u = _norm(username)
    if not u:
        return False
    bl = {_norm(x) for x in (cfg.block_channels or [])}
    return u in bl
