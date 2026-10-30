# yukari-desktop 向けの変更（このフォークで足したもの）

このフォークは [yukari-desktop](https://github.com/TTTankTTT/yukari-desktop) の「体」（ゆかりの表示・口パク・読み上げ・聞き取り）として使います。
上流（Open-LLM-VTuber v1.2.1）からの変更は最小限にし、ここに一覧を残します。

## 背景

yukari-desktop の脳（`brain/`）は、思考層の仕事が終わると、その報告を `/proxy-ws` に `text-input` として送り、ゆかりに話してもらいます
（試作の段階。設計は yukari-desktop の `docs/brain/arbitration.md`）。そのため、ゆかりのフロント（Live2D の画面）も
`/proxy-ws` につなぎ、脳と同じ会話を共有します。その使い方で困る点を、`src/open_llm_vtuber/proxy_handler.py` の数行で直しました。

## 変更

| # | 変更 | 理由 |
|---|---|---|
| 1 | サーバーからの `conversation-chain-start` を見たら「会話中」にする | proxy は、自分の順番待ちから流した `text-input` の会話しか「会話中」と知らなかった。声の会話（`mic-audio-end`）や自発的な発話（`ai-speak-signal`）は順番待ちを通らないので、その最中に順番待ちの `text-input`（脳の報告）が流れ、2 つの会話が重なっていた |
| 2 | `/proxy-ws?init_config=false` でつないだクライアントには、接続時の `request-init-config` を送らない | 送ると、サーバーの返す `set-model-and-conf` が全クライアントに配られ、表示中のフロントが状態を idle に戻し、Live2D のモデルの設定（大きさなど）を読み直していた。脳のように画面を持たないクライアント向け。指定しなければ今までどおり |
| 3 | proxy からサーバーへの接続（aiohttp）の、受け取るメッセージの大きさの上限（既定 4 MB）を外す | 音声は 1 文ずつ WAV の base64 で送られる。1 文が長い（目安: 44.1 kHz・16 bit・モノラルで約 35 秒を超える）と 4 MB を超え、proxy とサーバーの接続が切れて、フロントに何も届かなくなっていた（実測で確認） |

proxy を使わない場合（フロントが `/client-ws` に直接つなぐ、ふだんの使い方）の動きは変わりません。

## 使い方

`conf.yaml` の `system_config` に `enable_proxy: true` を足すと、`/proxy-ws` が使えるようになります（上流と同じ）。

```yaml
system_config:
  enable_proxy: true
```

- フロントの設定（Settings の General タブ）の **WebSocket URL** を `ws://127.0.0.1:12393/proxy-ws` にします
- 脳は `ws://127.0.0.1:12393/proxy-ws?init_config=false` につなぎます（脳の既定の設定）

## テスト

`tests/test_proxy_handler.py` で、上の 3 つと、もともとの動き（会話していないときは `text-input` がすぐ流れる）を確かめます。
proxy を uvicorn で動かし、`/client-ws` の代わりに偽のサーバーを置くので、OLV のほかの依存（ASR・TTS など）は要りません。

```sh
uv run --with pytest --with pytest-asyncio pytest tests/test_proxy_handler.py
```

修正の前のコードでは、上の 3 つのテストが失敗することを確かめてあります。
