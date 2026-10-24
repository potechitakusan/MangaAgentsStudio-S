# MangaAgentsStudio-S

小説から漫画の字コンテと画像を作り、枠・台詞を付けてPNG／PSDへ書き出す、ローカルのブラウザアプリです。完成ページを見ながら文章や衣装を編集し、気になるコマだけ描き直せます。物語と画像の最終確認・採否は利用者が行います。

## 必要な環境とサービス

WindowsとPowerShell、**Python 3.12**、通常のブラウザを用意してください。Pythonは3.12.10での動作記録があり、他のバージョン・OSでの動作は未検証です。Pythonライブラリは初回に導入します。

**制作AIを1つ、画像生成先を1つ選びます。** StrataとGrokの両方を用意する必要はありません。サービス・CLI・モデル本体は同梱していません。

| 選択肢 | 役割・準備 | 送信先と費用 |
|---|---|---|
| Strata（標準） | ページ割り・字コンテ・修正・翻訳・点検を行う制作AI。別途構築して起動 | 既定はローカルの `127.0.0.1:8080`。`.env`で接続先を変更可能。実行用のGPU・メモリが必要 |
| Grok CLI | Strataの代わりになる制作AI。公式CLIを導入し、同じPCでログイン | 対象の原作・台帳・指示等をクラウドへ送信。利用枠・費用はGrok側の契約による |
| NovelAI | コマ／ページの画像生成。**Opus契約**と永続的APIトークンが必要 | 英語プロンプト等をNovelAIへ送信。契約料金・V5利用枠・Anlasを確認 |
| QwenImage 2.1 Turbo | ローカルで画像生成。必要モデルを入れたComfyUIを起動 | `127.0.0.1:8188`。Anlasは消費しない。GPU等の実行環境が必要 |

Grokの料金はNovelAIのAnlasとは別管理です。Strataを選んでも、NovelAIで画像を作るときは外部送信があります。Qwenで人物・衣装・背景の参照画像を使う場合は、[Qwenガイド](web/guide/qwen-image.html)の準備も行ってください。

## 初回セットアップ

### 1. 取得し、Pythonライブラリを入れる

[GitHub](https://github.com/potechitakusan/MangaAgentsStudio-S) の **Code → Download ZIP** で取得して展開し、`README.md` と `start-studio.cmd` があるフォルダでPowerShellを開きます。Gitを使う場合は次のコマンドでも取得できます。

```powershell
git clone https://github.com/potechitakusan/MangaAgentsStudio-S.git
cd MangaAgentsStudio-S
```

同じPowerShellで確認・導入します。`python` が見つからない場合はPythonのインストールとPATHを確認してください。

```powershell
python --version
python -m pip install -r requirements.txt
```

依存ライブラリはPillowとpsd-toolsです。仮想環境を使う場合は、導入時も起動時もその環境を有効にします。

### 2. 制作AIを用意する

- **Strataを使う場合：** [公式Strata](https://github.com/Niko1221/Strata)を構築・起動します。接続先の既定値は `http://127.0.0.1:8080/v1/chat/completions` で、ツール呼出しに対応するAPIが必要です。1応答の待ち時間は最大180秒。コンテキスト128K以上は推奨で、アプリが検査する必須値ではありません。起動コマンド、接続確認、構築例は[Strataの準備](web/guide/setup.html#strata)へ。
- **Grokを使う場合：** [公式Grok Buildの導入手順](https://docs.x.ai/build/overview)に従い、PowerShellで `grok --version` と `grok login` を実行します。ログイン後にアプリの「汎用」タブの「制作AI」で **Grok** を選びます。Strataの構築・起動は不要です。[インストールと認識の確認](web/guide/setup.html#grok)にWindows用コマンドとCLIの確認範囲をまとめています。

接続先・モデル名を変更する場合は、アプリ直下の`.env`へ次の2行を追加・編集します。新規作成時は`.env.example`をコピーし、既存の`.env`は上書きしないでください。以下は既定値です。

```dotenv
LOCAL_AI_URL=http://127.0.0.1:8080/v1/chat/completions
LOCAL_AI_MODEL=qwen3.8-flash-next-iq2_xs
```

処理を終えてStudioを停止し、編集後に再起動します。OSの同名環境変数が優先し、未設定・空欄は既定値になります。画面では引き続き「Strata」を選択します。設定先へ原作・台帳・指示が送られるため、管理している推論サーバーを指定してください。llama.cpp等の互換APIでの実生成は未検証です。[設定の詳細と環境差](web/guide/setup.html#local-settings)

### 3. 画像生成先を用意する

**NovelAIの場合：** `.env` がなければ `.env.example` をコピーして作成し、`NOVELAI_API_KEY=` の後ろへ永続的APIトークンを保存します。トークンはNovelAIの **設定 → アカウント → 永続的APIトークン**（英語表示では **User Settings → Account → Get Persistent API Token**）から取得します。[詳しい手順](web/guide/index.html#novelai-token)

トークンはNovelAIの接続・残量確認と画像生成に必要です。**アプリの起動、作品の編集、Strata／Grokの文章処理には不要**です。`.env` は共有・Git／SVNへの登録をしないでください。トークンを原作やプロンプトへ貼り付けないでください。

**Qwenの場合：** NovelAIのトークンは不要です。[必要モデルとComfyUIの準備](web/guide/qwen-image.html)を確認し、ComfyUIを `127.0.0.1:8188` で起動します。StrataとGPUメモリが競合する場合はStrataを止め、制作AIにGrokを選びます。

### 4. 起動して接続を確認する

`start-studio.cmd` をダブルクリックし、ブラウザで **http://127.0.0.1:8765** を開きます。PowerShellからも起動できます。

```powershell
powershell -ExecutionPolicy Bypass -File Start-Studio.ps1
```

起動ファイルが立ち上げるのはこのアプリだけです。Strataが応答しなくてもアプリは開きます。制作AIを使う操作には、選択したStrataまたはGrokが必要です。Grokの対話画面を開いたままにする必要はありません。

「汎用」タブで制作AIを選びます。NovelAIは右上の **「接続・残量を確認」**、Qwenは作品作成後に「作品専用」タブの **「接続確認」** で確認します。これらは画像を生成せず、制作AIの疎通確認とは別です。

## 最初の漫画を作る

1. 左の **「＋」** から作品名・保存用ID・原作を入力します。例は[迷子のカメラ](examples/lost-camera-12.txt)と[星を返す郵便局](examples/star-post-office.txt)。画像モデルと **「コマ別生成・後から組版」** を選びます。画像モデルは作成後に変更できません。
2. 「作品専用」タブで絵柄を選び、「1. ページ割り」で原作の割り当てを作ります。表の出来事・台詞を確認します。
3. 「2. 字コンテ」でまず2ページを作り、人物・衣装・背景・台詞を確認します。Qwenの参照モードでは、人物・背景を試し描きして **「参照画像に登録」** します。
4. **「このページを生成・組版」** で仕上がりを確認し、良ければ **「ページ割りの最後まで自動で作る」**、**「全ページを生成・組版」** と進めます。
5. 必要なコマを編集・再生成し、**「全ページを書き出す」** でPNGまたはPSD＋確認用PNGのZIPを保存します。

ボタン内の「Strata」は、Grokを選ぶと「Grok」に変わります。操作の詳細はアプリ右上の **「使い方」** で開く[基本の使い方](web/guide/index.html#steps)にあります。

### 2回目以降の起動・終了

選択したStrata／ComfyUIを起動（Grokはログイン済みなら常駐不要）し、`start-studio.cmd` → **http://127.0.0.1:8765** → 左の作品一覧から再開します。同じポートで二重起動しないでください。

処理が終わってから端末で **Ctrl+C**、または次のコマンドで終了します。Strata／ComfyUIはそれぞれの手順で停止します。

```powershell
powershell -ExecutionPolicy Bypass -File Stop-Studio.ps1
```

## Aで作った字コンテ・プロンプトを取り込む

MangaAgentsStudio-Aの個別作品を、SのNovelAI／コマ別作品へ取り込めます。S側の `manga-a-import` スキルが人物・衣装・台詞を整理し、`python -m studio.a_import` が検証と保存を行います。再取り込みではSの編集を保持し、同じ項目がAとSの両方で変わった場合は選択を求めます。A側の変更は不要です。

スキルを使う場合は `skills/manga-a-import` を利用中のCodexのスキルフォルダへ置き、「`$manga-a-import` で、このA作品をSへ取り込んで」と作品の場所を指定します。手動操作は[移行JSONとコマンド](skills/manga-a-import/references/transfer.md)、画面編集と制限は[取り込み後の編集](web/guide/reference.html#a-import)を参照してください。画像・生成履歴・自由な文字配置の移行や、画像生成は含みません。

全入力／引継ぎ設定JSONは `manga-studio-inputs-v4` です。

## 主な機能と出力

- **作る・続ける：** 84種のコマ割り、11種の絵柄、1ページ1〜7コマ。字コンテは1回1〜4ページ、自動作成は同じ章の中で最大2ページずつ。同一作品は24ページまでで、それ以上は[設定を引き継いで新しい作品へ](web/guide/index.html#over24)進めます。
- **直す：** 日英プロンプト、外見・口調、1人最大10種類の衣装、表情、背景・小物を編集。翻訳の方向を指定でき、試し描き・過去候補の採用・画像の位置調整にも対応します。
- **書き出す：** 1200×1800のPNG、レイヤーPSD＋確認用PNG、台詞一覧md、設定JSON、配置情報をZIPにまとめます。**PSDの文字はラスターレイヤー**です。psd-toolsで再読込・合成一致の検証記録がありますが、Photoshop／CLIP STUDIOでの実読込は未検証、ネイティブ文字編集は未対応です。
- **保管する：** 作品は `work/<作品ID>/` に保存。自動・軽量バックアップには画像が入りません。「画像も含めてバックアップ」でも完成ページの `output/` と書出しの `exports/` は対象外です。復元後に再組版・再書出しします。[保存先とバックアップ](web/guide/reference.html#storage)

ページ全体を1枚で描く **「ページ一括生成（試験）」** も選べます。枠・文字位置が指定どおりになる保証はありません。画像に描き込まれた文字は、台詞なし書出しでも消せません。[方式と制限](web/guide/reference.html#whole)

## 費用・外部送信・制限

NovelAIは **V5 Curated・23ステップ・標準サイズ・1枚ずつ** に制限し、Opusの有効性とV5利用枠を確認します。不足・不明なら停止し、有料へ自動で切り替えません。予期しないAnlas減少を1回検知した場合も次の生成を止めます。サービス側の条件は変わるため、[公式料金案内](https://docs.novelai.net/en/subscription/)とアカウントの残量を確認してください。

右上の **「検証 0 / 5000 Anlas」** は、全作品のNovelAI生成前後の残量差を足した値と変更可能な上限です。初期値は0／5000で、0に戻した後はそこからの累計です。請求明細や生成枚数、V5利用枠、Grokの料金ではありません。他の画面で同時にNovelAIを使うと、その消費も差分に含まれます。結果不明や費用異常による停止は、合計を0に戻しても解除されません。[費用と停止時の確認](web/guide/index.html#cost)

| 制限の対象 | 上限と数え方 |
|---|---|
| 保存する原作全体 | 200,000文字 |
| 1回のページ割り | **入力JSON全体90,000文字**。開始章から最後までの原作、共通設定、章情報、引継ぎ、構成の希望等を含む |
| 1回の字コンテに渡す原作 | 共通設定＋対象章の合計30,000文字。ページ割りで複数章が対象ならその合計 |
| 1作品 | 24ページ。字コンテ1回は1〜4ページ |

保存できる量と一度に制作AIへ渡せる量は異なります。システム指示等を含めた別の入力長チェックもあります。[入力制限の詳細](web/guide/reference.html#limits)

APIトークンはサーバー側で読み、制作AIの入力には含めません。一方、作品の原作・プロンプト・履歴・設定JSON・書出しには作品内容が残ります。共有前に機密情報や第三者の個人情報が含まれていないか確認してください。

人物・背景・構図の完全な一致は保証しません。Qwenの画像参照も再現を保証するものではありません。制作AIは生成画像を見て評価しません。自由なコマ枠ドラッグ編集・縦組み・長編の自動章分割・小説生成APIのアプリ内操作は未対応です。

## 詳細ガイド・ライセンス

- [基本の使い方・困ったとき](web/guide/index.html)
- [制作AIの準備・接続条件・動作確認環境](web/guide/setup.html)
- [入力制限・保存先・出力・設定JSON・API補足](web/guide/reference.html)
- [NovelAIで小説を書く](web/guide/novelai-story.html)／[システム指示の補足](examples/NOVELAI_WRITING.md)
- [渡す小説のコツ](web/guide/source-tips.html)／[NovelAIの画像生成Tips](web/guide/tips.html)／[QwenImageの使い方](web/guide/qwen-image.html)

本ツールは [MIT License](LICENSE) です。NovelAI・Grokのサービス、Strata・Qwen・ComfyUI・Anima・LoRA等のモデル／ソフトウェア、Pillow・psd-toolsはそれぞれの規約・ライセンスに従います。原作・参照素材・生成物の利用や公開には、第三者の権利と各サービスの条件も確認してください。

コマ割り84種のうち79種と絵柄プリセットの一部は、作者の別プロジェクト [MangaAgentsStudio-A](https://github.com/potechitakusan/MangaAgentsStudio-A) に由来します。
