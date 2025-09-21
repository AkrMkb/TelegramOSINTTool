from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator
import yaml

class Keywords(BaseModel):
    ja: List[str] = Field(default_factory=list)
    en: List[str] = Field(default_factory=list)
    zh: List[str] = Field(default_factory=list)
    ru: List[str] = Field(default_factory=list)
    ar: List[str] = Field(default_factory=list)

class CrawlConfig(BaseModel):
    enabled: bool = False
    max_depth: int = 1
    max_channels: int = 100
    follow_mentions: bool = True
    follow_tme_links: bool = True
    blocklist_keywords: List[str] = Field(default_factory=list)
    allow_types: List[str] = Field(default_factory=lambda: ["channel", "supergroup"])
    join_sleep_ms: int = 600
    floodwait_padding_s: int = 2
    max_wait_on_flood_s: int = 120
    global_time_limit_s: int = 600

class DiscoveryFilters(BaseModel):
    min_members: Optional[int] = None
    name_must_include: List[str] = Field(default_factory=list)
    username_block_patterns: List[str] = Field(default_factory=list)

class Discovery(BaseModel):
    queries: List[str] = Field(default_factory=list)
    limit_per_query: int = 25
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    filters: DiscoveryFilters = Field(default_factory=DiscoveryFilters)

class CollectParams(BaseModel):
    backfill_limit: int = 1000
    poll_interval_sec: int = 5

class Alerts(BaseModel):
    slack_webhook: str = ""

class TranslationCfg(BaseModel):
    enabled: bool = False
    provider: str = "googletrans"  # googletrans or deepl
    timeout_sec: int = 8
    deepl_api_key: str = ""
    deepl_api_url: str = ""        # https://api-free.deepl.com/v2/translate など

class Config(BaseModel):
    api_id: int
    api_hash: str
    session: str
    seed_channels: List[str] = Field(default_factory=list)
    block_channels: list[str] = Field(default_factory=list)
    discovery: Discovery = Field(default_factory=Discovery)
    keywords: Keywords = Field(default_factory=Keywords)
    negatives: List[str] = Field(default_factory=list)
    score_threshold: int = 1
    collect: CollectParams = Field(default_factory=CollectParams)
    sqlite_path: str = "./osint_tele.db"
    alerts: Alerts = Field(default_factory=Alerts)
    translation: TranslationCfg = Field(default_factory=TranslationCfg)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, values: Dict[str, Any]):
        if values is None:
            return {}
        defaults = {
            "seed_channels": [],
            "discovery": {},
            "keywords": {},
            "negatives": [],
            "collect": {},
            "alerts": {},
            "translation": {},
        }
        for k, default in defaults.items():
            if values.get(k) in (None, "null"):
                values[k] = default
        if isinstance(values.get("discovery"), dict):
            if values["discovery"].get("crawl") in (None, "null"):
                values["discovery"]["crawl"] = {}
            if values["discovery"].get("filters") in (None, "null"):
                values["discovery"]["filters"] = {}
        return values

def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config.model_validate(data)
