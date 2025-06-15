"""dependency_extractor.py  🚀 2025‑06‑14

JAIST 情報科学系（石川キャンパス）のシラバス CSV 2 つを読み込み、
科目間の依存グラフ **source,target,label** を生成するスクリプト。

❗ **モデル名は CLI で変更可能** (`--model`).
   - デフォルト: `gpt-4o`  
   - o3 を使いたい場合は OpenAI 組織を Verify したうえで `--model o3` を指定してください。

-----------------------------------------------------------------------
ハイライト
-----------------------------------------------------------------------
1. キーワード抽出を正規表現で拡張 (「特論」の有無を許容)
2. 講義コードを正規化 (全角→半角・空白除去)
3. GPT 呼び出しはフォールバック付き (temperature 非対応 / 未検証モデル)
4. キャッシュ `.gpt_cache.json` により再実行トークン削減
5. `--debug` で行ごとのヒット数を表示

-----------------------------------------------------------------------
PowerShell 実行例 (Windows)
-----------------------------------------------------------------------
```powershell
pip install pandas openai python-dotenv regex
setx OPENAI_API_KEY "sk-..."
# モデル指定なし → gpt‑4o
python dependency_extractor.py --details syllabus_details.csv --master jaist_syllabus_cs_ishikawa_2025.csv
# o3 を試す (組織 Verify 済みのとき)
python dependency_extractor.py --model o3 --details syllabus_details.csv --master jaist_syllabus_cs_ishikawa_2025.csv
```
"""
