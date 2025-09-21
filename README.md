# Telegram OSINT Tool

Telegram の公開チャンネルやグループを収集・監視し、SQLite に保存・可視化するための OSINT ツールです。  
Telethon をベースにしており、以下の機能を備えています。

<img width="3828" height="1903" alt="ui" src="https://github.com/user-attachments/assets/2b3607ae-871d-49e5-a72e-7016f3100e51" />




- **公開検索**: `contacts.Search` によるチャンネル探索
- **クロール**: メンション / t.me リンクを BFS で探索
- **翻訳**: 日本語以外の投稿を DeepL / Googletrans で自動翻訳
- **可視化**: Streamlit アプリで収集データをブラウズ


## 構成

```
.
├── app
│   ├── tele_osint_cli.py
│   └── streamlit_app.py
├── config
│   └── config.example.yaml   # サンプル設定
├── db                        # DB と Telegram セッションが永続化される
│   └── osint_tele.db
├── src
│   ├── app.py
│   ├── backfill.py
│   ├── config.py
│   ├── crawl.py
│   ├── db.py
│   ├── discovery.py
│   ├── scoring.py
│   ├── stream.py
│   └── translate.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## セットアップ
### TelegramのAPIを取得
本ツールは Telethonを利用して Telegram API にアクセスします。利用には API ID と API Hash が必要です。

1. [my.telegram.org](https://my.telegram.org/)にアクセス
2. アカウント（電話番号）でログイン
3. API Development Tools を選択
4. App title / Short name を入力して登録
5. 発行された API ID / API Hash を控える

### configの整備
``` bash
cp config/config.example.yaml config/config.yaml
```
config.example.yamlを参考に設定を記入してください。

## 実行方法

### Docker で実行

#### 初回ログイン（ユーザー対話）
セッションファイルがまだ無い場合、コンテナを対話モードで起動します：

```bash
chmod 755 ./db
docker compose build
docker compose run --rm collector
```

電話番号 / 認証コード / 2FA パスワードを入力すると  
`./db/telegram.session` が作成されます。

#### 2回目以降のログイン
以後は非対話で起動できます：

```bash
docker compose up -d
# ログ
docker compose logs -f collector
docker compose logs -f ui
```

ブラウザで http://localhost:8501 を開きます。



## 主なオプション

- `--discover` : 公開検索で候補を探索
- `--backfill` : 過去ログ取得
- `--new-only` : state 以降のみバックフィル
- `--run` : ライブ監視
- `--debug` : デバッグ出力



## UI
ブラウザで http://localhost:8501 または compose で割り当てたポートを開きます。

- 原文と日本語訳を並べて検索可能
- キーワードフィルタリング
  - フィルタリング
  - 期間（日数スライダ）、スコア閾値、チャネル名/ユーザ名部分一致
  - 本文/日本語訳/キーワードでの正規表現検索
  - 言語フィルタ（ja/en/zh/ru/ar/es/und）
  - DBの自動更新（秒）の設定と手動更新ボタン

- 日本語訳の可視化（Sudachi）
  - 頻出語 Top N
  - 選択トークンの日次トレンド（JST）
  - 上位語の共起ヒートマップ

- エクスポート
  - CSV一括ダウンロード（検索条件/フィルタ適用後の結果）


## 注意事項
- 本ツールの利用は Telegram の利用規約 および 各国の法令 を遵守してください。
- 本ツールは 研究・教育目的 に限定されています。違法行為や攻撃準備などへの利用を禁止します。
- 過度なリクエスト（短時間に大量の join・検索・クロールなど）は アカウント制限や BAN の原因となります。FloodWait が発生した場合は素直に待つか処理をスキップしてください。
- 非公開チャネルや招待リンク（t.me/+...）経由での参加は禁止です。対象は公開チャンネル／公開スーパーグループのみとしてください。
- 取得したデータの二次配布や商用利用は禁止します。収集した情報は社内研究や教育目的に留めてください。
- 本ツールを利用した結果について、開発者は一切の責任を負いません。

## LICENSE
Apache License 2.0
