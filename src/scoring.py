from __future__ import annotations
import re
import json
from dataclasses import dataclass
from typing import List, Optional
from langdetect import detect, LangDetectException
from config import Keywords

HASHTAG_RE  = re.compile(r"(#\S+)", re.UNICODE)
MENTION_RE  = re.compile(r"@([A-Za-z0-9_]{4,32})")
TME_RE      = re.compile(r"https?://t\.me/([A-Za-z0-9_+]{4,64})(?:/\d+)?")

@dataclass
class Scored:
    score: int
    matched: List[str]

KW_FAST: Optional[re.Pattern] = None

def init_keywords_fast_pattern(kws: Keywords) -> None:
    global KW_FAST
    words = set([w.casefold() for w in (kws.ja + kws.en + kws.zh + kws.ru + kws.ar)])
    words = [w for w in words if w]
    if not words:
        KW_FAST = None
        return
    KW_FAST = re.compile("|".join(map(re.escape, sorted(words, key=len, reverse=True))),
                         re.IGNORECASE)

def extract_text(msg) -> str:
    return getattr(msg, "raw_text", None) or getattr(msg, "message", "") or ""

def detect_lang_safe(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return "und"

def extract_candidates_from_text(text: str) -> List[str]:
    t = (text or "")
    users = [f"@{m}" for m in MENTION_RE.findall(t)]
    links = [m for m in TME_RE.findall(t)]
    links_norm = [f"https://t.me/{l}" for l in links]
    return sorted(set(users + links_norm))

def score_text(text: str, kws: Keywords, negatives: List[str]) -> Scored:
    raw = (text or "")
    body = HASHTAG_RE.sub(" ", raw).casefold()
    if not body.strip():
        return Scored(score=0, matched=[])
    if any((n or "").casefold() in body for n in negatives):
        return Scored(score=0, matched=[])
    if KW_FAST is not None and not KW_FAST.search(body):
        return Scored(score=0, matched=[])
    candidates = kws.ja + kws.en + kws.zh + kws.ru + kws.ar
    hits: List[str] = []
    for w in candidates:
        if not w:
            continue
        if re.search(re.escape(w.casefold()), body):
            hits.append(w)
    uniq = sorted(set(hits))
    return Scored(score=len(uniq), matched=uniq)

def matched_to_json(s: Scored) -> str:
    return json.dumps(s.matched, ensure_ascii=False)
