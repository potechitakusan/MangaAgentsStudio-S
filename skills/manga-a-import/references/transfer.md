# 移行JSONと操作

Sルートで実行する。`<A作品>`には制作キットでなく個別作品を指定する。出力先はAの外に置く。パスに空白があれば引用する。

```powershell
python -X utf8 -m studio.a_import inspect --source "<A作品>" --out .runtime/a-transfer.json
```

`project.json`がない字コンテだけのフォルダは、以後も同じ値を使う `--source-id a-my-story` を追加する。標準の読み取り対象は `project.json`、`input/novelai/characters.json`、`input/novelai/requests/*.json`、`input/pages/page-*.json`、ページから参照するlayout/typeset、`docs/story/BRIEF.md`・`TEXT-STORYBOARD.md`・`SCENE-TEMPLATE.md`、`docs/production/DIALOGUE.md`。変則の原稿は `--document input/storyboard.md` のように追加する（複数指定可）。input、docs/story、docs/production内のMarkdown／テキストだけが対象。選択文書もsnapshotに入り、変更検出・再取り込みで使われる。別の場所の原稿はAを変更せずS側の作業フォルダへ必要ファイルだけコピーして取込元を用意し、以後その同じ取込元を更新する。認証や環境設定は対象にしない。

ページ構成がなく `p01-01.json` と `p01-01a.json` 等が並ぶ場合、棚卸しでは最初の案を仮置きしalternatives警告を出す。選択した要求の内容をコマへ反映し、bindings.requestをそのパスへ合わせ、選択理由を記す。Aのファイルを削除・改名する必要はない。

生成される `manga-a-transfer-v1`:

| 項目 | 扱い |
|---|---|
| `source` | 元ファイルの本文・ハッシュ、作品id、digest。変更しない。Aを更新したらinspectを再実行する |
| `inputs` | Sの編集入力。出力モデルはnovelai、render_modeはpanels、text_modeはoverlay |
| `bindings` | 各Sコマについてpage_id/panel_id/request/source_page/source_orderを持つ。requestとsource_pageはsnapshot内の相対パスまたはnull。全コマに1件ずつ |
| `issues` | 棚卸し時の案内。削除しても原資料由来の検査は省略されない |
| `acknowledgements` | previewが示すcodeに対する、具体的な確認理由の辞書 |

`inputs`の共通欄はtitle/source、style/style_ja/style_negative/style_negative_ja/style_preset、characters/locations/props、output_method/image_settings/render_mode/text_mode、pages。systemや画面入力、画像パス、revision、import_stateを追加しない。sourceには今後の制作に必要な原作・字コンテを残し、キットの説明書を丸ごと使わない。

人物台帳はid/name/appearance/appearance_ja/voice/voice_ja、必要ならoutfits（id/name/prompt/prompt_ja）とdefault_outfit_id。場面や服を固定外見に混ぜない。背景・小物はid/name/description/description_ja。literalを保つ場合の台帳は名前とIDを確定し、根拠がない固定外見は空欄でも保存できる。

各ページはid/template/panels。コマはid/scene/prompt/prompt_ja/negative/negative_ja/characters/dialogue/speakers/seed/crop/image_transform/location_id/prop_ids/needs_edit/prompt_mode/conditioning。

- sceneを字コンテから記述し、未編集を解消してneeds_editをfalseにする。
- コマ人物はid/prompt/prompt_ja/negative/negative_ja/outfit_id/expression/x/y。初期expressionはcustom。人物別の除外を全体negativeへ移さない。
- `prompt_mode:"literal"`: 場面prompt、人物prompt、negativeをそのまま使う。台帳・画風・品質語を追加しない。`catalog`: 固定外見・衣装・背景・小物・画風とSの標準品質／除外を合成する。
- conditioningはuse_coords/use_order/negative_use_coords/negative_use_orderの真偽値。人物のx/yはピンが無効でも保持する。複数centersや正負で異なる座標は現行では受け付けない。
- 台詞は最大3件／コマ、各80文字。speakersは同じ個数で、コマ人物IDまたは空文字。画面外話者は空文字とし元資料に名前を残す。自動短縮・勝手な結合はしない。
- 24ページ／12人物／1コマ6人／衣装10種／1ページ人物登場合計22まで。templateとコマ数を一致させる。IDは英数字から始まる英数字・`-`・`_`の1〜64文字。ページ・コマのIDを再取り込み時に採番し直さない。

取り込み先を新規にする場合、原作をUTF-8の作業ファイルへ用意し、次で空の作品を作る。この操作は生成を行わない。

```powershell
python -X utf8 -m studio.cli create "<S作品ID>" --source .runtime/import-source.txt --title "作品名" --image-provider novelai
python -X utf8 -m studio.a_import preview --source "<A作品>" --transfer .runtime/a-transfer.json --project "<S作品ID>" --out .runtime/a-preview.json
```

構造不正は先に修正する。preview内の `unresolved` が空になるまで、内容を調べて対応または差分の理由を記録する。送信差分は `warnings` のbeforeがA、afterが今回のS変換結果。例: `"a-p001-c001:typeset": "台詞と話者を原文で照合。Sでは横書きの自動配置を使い、元の縦書き位置はsource snapshotに保持"`。生成寸法・モデル・sampler等はS側の固定設定へ変わる場合があり、同じ絵を再現したと報告しない。

再取込みの `conflicts` は、前回入力base・現在のS local・今回のA incomingの比較。`/pages/a-page-001/panels/a-p001-c001/prompt` のようなパスをキーに、選択JSONを作る。`@order`は配列の順序。選択できる値はlocal/incoming。手で書き直す場合は対象のS入力または移行JSONを直してpreviewをやり直す。

```json
{"/pages/a-page-001/panels/a-p001-c001/prompt":"local"}
```

```powershell
python -X utf8 -m studio.a_import preview --source "<A作品>" --transfer .runtime/a-transfer.json --project "<S作品ID>" --resolutions .runtime/a-resolutions.json --out .runtime/a-preview.json
python -X utf8 -m studio.a_import apply --source "<A作品>" --transfer .runtime/a-transfer.json --project "<S作品ID>" --revision 3 --resolutions .runtime/a-resolutions.json
```

revisionの3は例。必ず最新previewの値を使う。競合がなければresolutionsは省略する。applyはSの保存・競合検査を使い、同じコマIDの採用画像と候補を保持する。削除されたコマも旧projectの履歴と画像ファイルへ残る。同じ取込内容の再実行ではrevisionを増やさない。

前回の正規化入力、出典snapshot、確認理由、競合選択は `work/<ID>/imports/<内容ハッシュ>.json`、正本の `import_state` はその参照。原資料が先に保存されても正本の参照更新に失敗すれば未採用記録になり、次回の比較基準には使わない。バックアップと作品フォルダのコピー読込みはimportsを含む。通常の全入力設定JSONは編集入力だけで、取込履歴を別作品へ移す用途ではない。
