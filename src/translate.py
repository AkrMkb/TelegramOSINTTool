from __future__ import annotations
import os
import requests
from config import Config
from deep_translator import GoogleTranslator


def translate_to_ja(text: str, src_lang_hint: str, cfg: Config) -> str:
    """
    日本語/空文字なら翻訳しない。
    provider: "deepl"（推奨） or "googletrans"
    エラーは空文字で返す（可視化側で未翻訳表示）。
    """
    if not cfg.translation.enabled:
        return ""
    if not text:
        return ""
    if (src_lang_hint or "").lower().startswith("ja"):
        return ""

    provider = (cfg.translation.provider or "deepl").lower()

    if provider == "deepl":
        api_key = cfg.translation.deepl_api_key or os.environ.get("DEEPL_API_KEY", "")
        api_url = (
            cfg.translation.deepl_api_url
            or os.environ.get("DEEPL_API_URL")
            or "https://api-free.deepl.com/v2/translate"
        )
        if not api_key:
            return ""
        try:
            r = requests.post(
                api_url,
                data={"text": text, "target_lang": "JA"},
                headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
                timeout=cfg.translation.timeout_sec,
            )
            if r.ok:
                data = r.json()
                return (data.get("translations", [{}])[0].get("text", "") or "")
        except Exception:
            return ""
        return ""

    if provider == "googletrans":
        try:
            return GoogleTranslator(source="auto", target="ja").translate(text) or ""
        except Exception:
            return ""

    return ""
