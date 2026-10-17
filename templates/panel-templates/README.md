# Sで使うコマ枠

ユーザー提供の `参考/MangaAgentsStudio-A/templates/manga-project/templates/panel-templates` からcatalog.jsonと79個のSVGを複製した。元データは変更していない。Sはこのフォルダだけを読み、参考フォルダへの実行時依存を持たない。

座標・読み順・斜め枠・名称はカタログを使用する。組版は多角形のマスク、ブラウザのプレビューは同じ頂点から作る。元のSVGも編集素材として保持する。Aの制作割合や大型スクリプト・全スキルは取り込まない。

カタログ内のPNGや個別JSON等のassetパスは参照元のメタデータ。Sに同梱する実体はcatalog.jsonとsvgのみ。SのUIでは存在しないassetパスを使わない。
