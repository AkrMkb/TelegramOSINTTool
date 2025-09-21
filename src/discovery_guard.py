from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional
from telethon.tl.custom import Message

from config import Config
from scoring import extract_text, score_text, detect_lang_safe


_LOW_QUALITY_UNTIL: dict[int, float] = {}  # chat_id -> unblock_epoch

def mark_low_quality(chat_id: int, cooldown_s: int) -> None:
    _LOW_QUALITY_UNTIL[chat_id] = time.time() + max(0, int(cooldown_s))

def is_low_quality_blocked(chat_id: int) -> bool:
    until = _LOW_QUALITY_UNTIL.get(chat_id)
    if not until:
        return False
    if time.time() >= until:
        _LOW_QUALITY_UNTIL.pop(chat_id, None)
        return False
    return True


@dataclass
class ProbeResult:
    total: int = 0
    hit: int = 0                # スコア閾値以上（=ヒット）
    negative: int = 0           # negatives に当たった数
    target_lang_hits: int = 0   # 日本語/英語/中国語/ロシア語/アラビア語 など対象言語の数
    avg_len: float = 0.0

    @property
    def hit_rate(self) -> float:
        return (self.hit / self.total) if self.total else 0.0

    @property
    def negative_rate(self) -> float:
        return (self.negative / self.total) if self.total else 0.0

    @property
    def target_lang_rate(self) -> float:
        return (self.target_lang_hits / self.total) if self.total else 0.0


# 必要に応じて対象言語を追加
TARGET_LANGS = {"ja", "en", "zh", "ru", "ar", "es"}  

async def probe_channel_quality(client, cfg: Config, entity, sample_messages: int = 50) -> ProbeResult:
    """
    指定チャンネルから直近 sample_messages を取り、簡易統計を返す。
    - score_text() と cfg.score_threshold でヒット判定
    - cfg.negatives に含まれる単語が本文にあれば negative++
    - 言語は detect_lang_safe()
    """
    pr = ProbeResult()
    try:
        async for msg in client.iter_messages(entity, limit=max(1, int(sample_messages))):
            if not isinstance(msg, Message):
                pass
            text = extract_text(msg)
            if not text:
                continue
            pr.total += 1

            s = score_text(text, cfg.keywords, cfg.negatives)
            if s.score >= max(0, int(cfg.score_threshold)):
                pr.hit += 1

            lower = text.lower()
            if any((n or "").lower() in lower for n in (cfg.negatives or [])):
                pr.negative += 1

            lang = detect_lang_safe(text)
            if lang in TARGET_LANGS:
                pr.target_lang_hits += 1

            ln = len(text)
            pr.avg_len += (ln - pr.avg_len) / pr.total 

    except Exception:
        pass

    return pr


def pass_quality_gates(probe: ProbeResult, cfg: Config) -> tuple[bool, str]:
    """
    閾値（無ければデフォルト）で探索可否を判断。
    - 最低メッセージ数
    - ヒット率の下限
    - ネガティブ率の上限
    - 平均テキスト長の下限
    """
    min_samples = getattr(cfg.discovery.crawl, "q_min_samples", 10)
    min_hit_rate = getattr(cfg.discovery.crawl, "q_min_hit_rate", 0.05)
    max_neg_rate = getattr(cfg.discovery.crawl, "q_max_negative_rate", 0.50)
    min_avg_len = getattr(cfg.discovery.crawl, "q_min_avg_len", 10)

    if probe.total < min_samples:
        return False, f"not_enough_samples({probe.total}<{min_samples})"
    if probe.hit_rate < min_hit_rate:
        return False, f"low_hit_rate({probe.hit_rate:.2f}<{min_hit_rate:.2f})"
    if probe.negative_rate > max_neg_rate:
        return False, f"high_negative_rate({probe.negative_rate:.2f}>{max_neg_rate:.2f})"
    if probe.avg_len < min_avg_len:
        return False, f"text_too_short({probe.avg_len:.1f}<{min_avg_len})"
    return True, "ok"
