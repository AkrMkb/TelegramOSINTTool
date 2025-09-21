import re
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from collections import Counter
import numpy as np

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import matplotlib
import matplotlib.pyplot as plt

import os
DB_PATH = os.getenv("STREAMLIT_DB_PATH", "./db/osint_tele.db")

matplotlib.rcParams['font.family'] = [
    'Noto Sans CJK JP',   # 日本語
    'Noto Sans Arabic',   # アラビア語
    'Noto Sans',          # ラテン/キリル等
    'DejaVu Sans',
    'sans-serif'
]
matplotlib.rcParams['axes.unicode_minus'] = False

FIGSIZE_SMALL = (3.2, 1.8)
FIGSIZE_MED = (4.2, 2.2)
FIGSIZE_LARGE = (6.0, 3.5)

def _plot_slot(width_pct: int, align: str = "中央"):
    """ページ幅に対する図の横幅割合と配置から、図を描画するカラムを返す"""
    frac = max(0.2, min(width_pct / 100.0, 1.0)) 
    # 残りを左右に割り当て
    if align == "左寄せ":
        cols = st.columns([frac, 1.0 - frac])
        return cols[0]
    elif align == "右寄せ":
        cols = st.columns([1.0 - frac, frac])
        return cols[1]
    else:  # 中央
        side = (1.0 - frac) / 2.0
        cols = st.columns([side, frac, side])
        return cols[1]

# -------- Streamlit ページ設定 --------
st.set_page_config(page_title="Telegram OSINT Viewer", layout="wide")
st.title("Telegram OSINT Viewer")
st.caption("テレグラム収集結果の可視化")

# -----------------------------
# DB utils
# -----------------------------
@st.cache_data(show_spinner=False, ttl=60)
def load_messages(limit:int=10000, dt_from:str|None=None, dt_to:str|None=None,
                  min_score:int=0, chat_query:str|None=None) -> pd.DataFrame:
    where = ["1=1"]
    params: list = []

    if dt_from:
        where.append("date >= ?")
        params.append(dt_from)
    if dt_to:
        where.append("date <= ?")
        params.append(dt_to)
    if min_score:
        where.append("score >= ?")
        params.append(min_score)
    if chat_query:
        where.append("(LOWER(chat_title) LIKE ? OR LOWER(chat_username) LIKE ?)")
        like = f"%{chat_query.lower()}%"
        params += [like, like]

    q = f"""
      SELECT date, chat_title, chat_username, message_id,
             text, text_ja, lang, matched_keywords, score, url
      FROM messages
      WHERE {' AND '.join(where)}
      ORDER BY date DESC
      LIMIT ?
    """
    params.append(limit)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(q, conn, params=params)
    conn.close()

    if not df.empty:
        df["dt"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
        df["dt_local"] = df["dt"].dt.tz_convert("Asia/Tokyo")
        df["day"] = df["dt_local"].dt.date

        def parse_mk(x):
            if x is None:
                return []
            s = str(x).strip()
            if s.startswith("["):
                try:
                    return json.loads(s)
                except Exception:
                    pass
            return [w.strip() for w in re.split(r"[,\s]+", s) if w.strip()]

        df["kw_list"] = df["matched_keywords"].apply(parse_mk)
        df["kw_flat"] = df["kw_list"].apply(lambda xs: [str(x).lower() for x in xs])
    return df

# -----------------------------
# Sudachi トークナイザ（フォールバック付）
# -----------------------------
_SUDACHI_AVAILABLE = False
try:
    from sudachipy import tokenizer as _tok
    from sudachipy import dictionary as _dict
    _SUDACHI_AVAILABLE = True
    _SUDACHI = _dict.Dictionary().create()
    _SPLIT_MODE = _tok.Tokenizer.SplitMode.B  # A:短, B:中, C:長
except Exception:
    _SUDACHI_AVAILABLE = False
    _SUDACHI = None
    _SPLIT_MODE = None


_JA_TOKEN_RE = re.compile(r"[一-龥々〆ヵヶぁ-んァ-ヴーｦ-ﾟーA-Za-z0-9]+")

_DEFAULT_STOPWORDS = {
    "の","に","は","を","が","と","て","で","も","や","から","まで","より","へ",
    "です","ます","でした","だ","な","ない","いる","ある","する","なる","できる",
    "こと","これ","それ","あれ","ため","よう","さん","など","そして","しかし","また",
    "として","について","により","に対して","または","および","及び","もの","ために",
    "下さい","ください","すること","でき","できた","した","しています","している","して"
}

_ALLOWED_POS = {
    "名詞", "固有名詞", "動詞", "形容詞", "外来語"
}

def _tokenize_ja_sudachi(text: str,
                         stopwords: set[str],
                         min_len: int) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    tokens = []
    for m in _SUDACHI.tokenize(text, _SPLIT_MODE):
        surf = m.surface().strip().lower()
        if len(surf) < min_len:
            continue
        pos = m.part_of_speech()[0] if m.part_of_speech() else ""
        if pos not in _ALLOWED_POS:
            continue
        if surf in stopwords:
            continue
        tokens.append(surf)
    return tokens

def _tokenize_ja_fallback(text: str,
                          stopwords: set[str],
                          min_len: int) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    toks = _JA_TOKEN_RE.findall(text)
    out = []
    for t in toks:
        t = t.strip().lower()
        if len(t) < min_len:
            continue
        if t in stopwords:
            continue
        out.append(t)
    return out

def tokenize_ja(text: str,
                stopwords: set[str] = _DEFAULT_STOPWORDS,
                min_len: int = 2) -> list[str]:
    if _SUDACHI_AVAILABLE and _SUDACHI is not None:
        return _tokenize_ja_sudachi(text, stopwords, min_len)
    else:
        return _tokenize_ja_fallback(text, stopwords, min_len)

@st.cache_data(show_spinner=False, ttl=60)
def build_token_stats(df: pd.DataFrame,
                      text_col: str = "text_ja",
                      min_len: int = 2,
                      limit_docs: int = 20000) -> tuple[Counter, dict]:
    """
    日本語訳カラムからトークン頻度と各ポストのトークンを構築
    return: (freq_counter, {doc_index: [tokens...]})
    """
    if df.empty or text_col not in df.columns:
        return Counter(), {}
    sub = df.head(min(limit_docs, len(df))).copy()
    doc_tokens = {}
    freq = Counter()
    for i, txt in enumerate(sub[text_col].fillna("").astype(str)):
        toks = tokenize_ja(txt, min_len=min_len)
        if toks:
            doc_tokens[i] = toks
            freq.update(toks)
    return freq, doc_tokens

# -----------------------------
# Sidebar filters
# -----------------------------
with st.sidebar:
    st.subheader("フィルタ")
    limit = st.slider("最大取得件数", 500, 50000, 10000, step=500)
    days = st.slider("期間（日）", 1, 120, 30)

    now_utc = datetime.now(timezone.utc)
    dt_from = (now_utc - timedelta(days=days)).isoformat()
    dt_to = now_utc.isoformat()

    min_score = st.slider("スコアしきい値", 0, 10, 1)
    chat_query = st.text_input("チャネル名/ユーザ名（部分一致）", "")
    kw_filter = st.text_input("本文/日本語訳/キーワード絞り込み（正規表現OK）", "")
    show_langs = st.multiselect("言語（空=全件）", ["ja","en","zh","ru","ar","es","und"])

    refresh_sec = st.number_input("DB自動更新（秒）", min_value=0, max_value=600, value=60, step=10)
    manual_btn = st.button("DB手動更新")

    if refresh_sec > 0:
        st_autorefresh(interval=refresh_sec * 1000, key="data_refresh")

    if manual_btn:
        st.cache_data.clear()

    st.markdown("---")
    st.caption("日本語訳の可視化（Sudachi対応）")
    ja_min_len = st.number_input("最小文字数（トークン）", 1, 10, 2)
    ja_topn = st.slider("上位語 Top N", 5, 50, 15, step=1)  
    
    st.markdown("---")
    st.caption("図サイズ")
    fig_size_opt = st.selectbox(
        "可視化の図サイズ",
        ["小", "中", "大"],
        index=1 
    )
    st.caption("図のレイアウト")
    plot_width_pct = st.slider("図の横幅割合（%）", 20, 100, 40, step=5)
    plot_align = st.selectbox("配置", ["左寄せ", "中央", "右寄せ"], index=1)

if fig_size_opt == "小":
    FIG_1 = FIGSIZE_SMALL
    FIG_2 = FIGSIZE_SMALL
    FIG_3 = (5.0, 5.0)
elif fig_size_opt == "中":
    FIG_1 = FIGSIZE_MED
    FIG_2 = FIGSIZE_MED
    FIG_3 = (6.0, 6.0)
else:
    FIG_1 = FIGSIZE_LARGE
    FIG_2 = FIGSIZE_LARGE
    FIG_3 = (7.0, 7.0)

# -----------------------------
# Load
# -----------------------------
df = load_messages(limit=limit, dt_from=dt_from, dt_to=dt_to,
                   min_score=min_score, chat_query=chat_query or None)

if show_langs:
    df = df[df["lang"].isin(show_langs)]

if kw_filter.strip():
    pat = re.compile(kw_filter, re.IGNORECASE)
    df = df[
        df["text"].fillna("").str.contains(pat)
        | df["text_ja"].fillna("").str.contains(pat)
        | df["kw_flat"].apply(lambda xs: any(pat.search(x or "") for x in xs))
    ]

st.success(
    f"読み込み: {len(df):,} 件（期間: {dt_from[:10]}–{dt_to[:10]} / score≥{min_score} / "
    f"Sudachi={'ON' if _SUDACHI_AVAILABLE else 'OFF'}）"
)

# -----------------------------
# Summary
# -----------------------------
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("総ヒット件数", f"{len(df):,}")
with c2:
    st.metric("ユニークチャネル", f"{df['chat_username'].nunique() or df['chat_title'].nunique():,}")
with c3:
    st.metric("平均スコア", f"{df['score'].mean():.2f}" if not df.empty else "–")
with c4:
    last_dt = df["dt_local"].max() if not df.empty else None
    st.metric("最新検知（JST）", last_dt.strftime("%Y-%m-%d %H:%M") if last_dt is not None else "–")

if not df.empty:
    st.subheader("日次ヒット推移")
    daily = df.groupby("day").size().reset_index(name="count")
    fig = plt.figure(figsize=FIG_1)
    plt.plot(daily["day"], daily["count"])
    plt.title("Daily Hits (JST)")
    plt.xlabel("Date"); plt.ylabel("Hits")
    plt.xticks(rotation=45)
    with _plot_slot(plot_width_pct, plot_align):
        st.pyplot(fig)

    st.subheader("チャネル別ヒット（Top 15）")
    chan = (df.assign(chan=lambda x: x["chat_username"].where(x["chat_username"].ne(""),
                                                              other=x["chat_title"]))
              .groupby("chan").size().sort_values(ascending=False).head(15))
    fig = plt.figure(figsize=FIG_1)
    chan.sort_values().plot(kind="barh")
    plt.title("Top Channels")
    plt.xlabel("Hits"); plt.ylabel("Channel")
    with _plot_slot(plot_width_pct, plot_align):
        st.pyplot(fig)

    st.subheader("キーワード頻度（Top 20）")
    kw_series = pd.Series([k for ks in df["kw_flat"] for k in (ks or [])])
    if not kw_series.empty:
        topkw = kw_series.value_counts().head(20).sort_values()
        fig = plt.figure(figsize=FIG_1)
        topkw.plot(kind="barh")
        plt.title("Top Keywords")
        plt.xlabel("Count"); plt.ylabel("Keyword")
        with _plot_slot(plot_width_pct, plot_align):
            st.pyplot(fig)
    else:
        st.info("キーワード情報がありません。")

    st.subheader("スコア分布")
    fig = plt.figure(figsize=FIG_2)
    max_bin = int(df["score"].max()) if not df["score"].isna().all() else 10
    df["score"].plot(kind="hist", bins=range(0, max(10, max_bin) + 2))
    plt.title("Score Histogram"); plt.xlabel("Score"); plt.ylabel("Count")
    with _plot_slot(plot_width_pct, plot_align):
        st.pyplot(fig)

if not df.empty:
    st.subheader("日本語訳の頻出語（Top N）")
    freq, doc_tokens = build_token_stats(df, text_col="text_ja", min_len=ja_min_len)

    if freq:
        top_items = freq.most_common(ja_topn)
        labels = [w for w, _ in top_items][::-1]
        values = [c for _, c in top_items][::-1]

        fig = plt.figure(figsize=FIG_1)
        plt.barh(range(len(values)), values)
        plt.yticks(range(len(labels)), labels)
        plt.xlabel("Count"); plt.ylabel("Token")
        plt.title(f"Top {ja_topn} Tokens in text_ja (min_len={ja_min_len})")
        with _plot_slot(plot_width_pct, plot_align):
            st.pyplot(fig)

        st.subheader("選択トークンの日次トレンド（JST）")
        all_top = [w for w, _ in freq.most_common(min(50, len(freq)))]
        default_sel = all_top[:3]
        chosen = st.multiselect("トークンを選択（最大5）", options=all_top, default=default_sel, max_selections=5)
        if chosen:
            daily_tok = df.groupby("day").size().reset_index(name="__dummy__")[["day"]].copy()
            for tok in chosen:
                hit = []
                for txt in df["text_ja"].fillna("").astype(str):
                    toks = tokenize_ja(txt, min_len=ja_min_len)
                    hit.append(1 if tok in toks else 0)
                tmp = df.copy()
                tmp["__hit__"] = hit
                g = tmp.groupby("day")["__hit__"].sum().reset_index(name=tok)
                daily_tok = daily_tok.merge(g, on="day", how="left")
            daily_tok = daily_tok.fillna(0)

            fig = plt.figure(figsize=FIG_2)
            for tok in chosen:
                plt.plot(daily_tok["day"], daily_tok[tok], label=tok)
            plt.title("Token Daily Trend (JST)")
            plt.xlabel("Date"); plt.ylabel("Count")
            plt.xticks(rotation=45)
            plt.legend()
            with _plot_slot(plot_width_pct, plot_align):
                st.pyplot(fig)

        st.subheader("上位語の共起ヒートマップ")
        top_for_co = [w for w, _ in freq.most_common(min(20, len(freq)))]
        idx = {w:i for i, w in enumerate(top_for_co)}
        mat = np.zeros((len(top_for_co), len(top_for_co)), dtype=int)

        for toks in doc_tokens.values():
            present = [w for w in set(toks) if w in idx]
            for i in range(len(present)):
                for j in range(i+1, len(present)):
                    a, b = idx[present[i]], idx[present[j]]
                    mat[a, b] += 1
                    mat[b, a] += 1

        if len(top_for_co) > 0:
            fig = plt.figure(figsize=FIG_3)
            plt.imshow(mat, aspect="auto")
            plt.title("Co-occurrence (Top tokens in text_ja)")
            plt.xticks(range(len(top_for_co)), top_for_co, rotation=90)
            plt.yticks(range(len(top_for_co)), top_for_co)
            plt.xlabel("Token")
            plt.ylabel("Token")
            plt.colorbar()
            with _plot_slot(plot_width_pct, plot_align):
                st.pyplot(fig)

        freq_df = pd.DataFrame(freq.most_common(), columns=["token","count"])
        st.download_button(
            "頻度表（CSV）をダウンロード",
            data=freq_df.to_csv(index=False).encode("utf-8"),
            file_name="ja_token_freq.csv",
            mime="text/csv"
        )
    else:
        st.info("日本語訳（text_ja）からトークンを抽出できませんでした。")

# -----------------------------
# Table
# -----------------------------
st.subheader("ヒット一覧")
if df.empty:
    st.info("該当なし")
else:
    show_cols = ["dt_local","chat_title","chat_username","score","matched_keywords","lang","text","text_ja","url","message_id"]
    st.dataframe(df[show_cols].sort_values("dt_local", ascending=False), height=500)

    st.markdown("---")
    st.subheader("詳細（クリック展開）")
    for _, row in df.head(200).iterrows():
        ttl = f"{row['dt_local'].strftime('%Y-%m-%d %H:%M')} | {row['chat_title']} (@{row['chat_username']}) | score={row['score']}"
        with st.expander(ttl):
            st.write(f"**Keywords:** {row['matched_keywords']}")
            st.write(f"**言語:** {row['lang']}")
            st.write("**原文**")
            st.write(row.get("text") or "")
            st.write("**日本語訳**")
            ja = row.get("text_ja")
            st.write(ja if (isinstance(ja, str) and ja.strip()) else "—")
            if row.get("url"):
                st.markdown(f"[Telegramメッセージ]({row['url']})")

# -----------------------------
# Export
# -----------------------------
st.sidebar.markdown("---")
if not df.empty:
    csv = df.to_csv(index=False).encode("utf-8")
    st.sidebar.download_button("CSVをダウンロード", data=csv, file_name="osint_hits.csv", mime="text/csv")
