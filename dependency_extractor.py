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

from __future__ import annotations

# ────────────────────────────── 標準ライブラリ ──────────────────────────────
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List

# ────────────────────────────── サードパーティ ─────────────────────────────
import pandas as pd
from openai import OpenAI
import regex as re2  # 正規表現強化版

# ---------------------------------------------------------------------------
# 設定値
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "gpt-4o"            # o3 が使えない場合でも動く安全デフォルト
CACHE_FILE = Path(".gpt_cache.json")
PATTERN_CODE = re.compile(r"(I\d{3,7})（([^）]+)）")
BASE_KEYWORDS = [
    "微分積分", "線形代数", "確率統計", "情報理論",
    "アルゴリズムとデータ構造", "論理回路",
    "オペレーティングシステム特論", "コンピュータアーキテクチャ",
]
KEYWORDS_REGEX = [re2.compile(fr"{kw}(?:特論)?") for kw in BASE_KEYWORDS]

# ---------------------------------------------------------------------------
# キャッシュユーティリティ
# ---------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def _load(path: Path) -> Dict[str, str]:
    return json.loads(path.read_text()) if path.exists() else {}

def _save(obj: Dict[str, str], path: Path):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))

# ---------------------------------------------------------------------------
# GPT ラベル判定 (フォールバック+温度自動調整)
# ---------------------------------------------------------------------------

def classify(client: OpenAI, text: str, item_type: str, cache: Dict[str, str], model: str) -> str:
    """GPT で 6 ラベルに分類。モデル不可/temperature 非対応を自動フォールバック"""
    key = _sha(f"{item_type}|{text}|{model}")
    if key in cache:
        return cache[key]

    # ——★ ここが *最初* に提示した sys_prompt ★——
    sys_prompt = (
        "あなたは大学シラバスの記述から科目間の依存関係ラベルを判定する専門家です。\n"
        "以下の 6 種類のラベルのいずれかを **必ず 1 単語だけ** 出力してください。\n"
        "unrelated : 他の科目と直接の関連はない\n"
        "recommended : 履修が望ましい（準備学習）\n"
        "equivalent : 相当する知識が必要（前提知識）\n"
        "required : 履修が必須（前提条件）\n"
        "exclusive : 履修していると受講不可（排他関係）\n"
        "related   : 上記に当てはまらないが関連がある科目\n\n"
        "与えられた 'item_type' (related_items/prerequisites) も考慮してください。\n"
        "返答はラベル名のみ。解説や句読点は不要です。"
    )
    usr_prompt = f"item_type: {item_type}\ntext: {text}"

    # モデル候補チェーン
    chain = [model, "gpt-4o", "gpt-3.5-turbo"]
    last_err: Exception | None = None

    for m in chain:
        for attempt in ("with_temp", "no_temp"):
            try:
                payload = dict(model=m, messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": usr_prompt},
                ])
                if attempt == "with_temp":
                    payload["temperature"] = 0
                resp = client.chat.completions.create(**payload)
                label = resp.choices[0].message.content.strip()
                cache[key] = label
                return label
            except Exception as e:
                last_err = e
                msg = str(e)
                # temperature unsupported → 次の attempt
                if "unsupported_value" in msg and "temperature" in msg and attempt == "with_temp":
                    continue
                # model not found → 次のモデル
                if "model_not_found" in msg or "must be verified" in msg:
                    break
                raise  # その他のエラーは即終了
    raise last_err if last_err else RuntimeError("全モデル失敗")

# ---------------------------------------------------------------------------
# マスタ辞書作成 & 正規化
# ---------------------------------------------------------------------------

def _norm(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def build_map(df: pd.DataFrame) -> Dict[str, str]:
    mapping = {}
    for _, r in df.iterrows():
        mapping[_norm(str(r["科目コード"]))] = r["講義名称"]
        mapping[_norm(str(r["講義コード"]))] = r["講義名称"]
    return mapping

# ---------------------------------------------------------------------------
# 依存エッジ抽出
# ---------------------------------------------------------------------------

def extract(df: pd.DataFrame, mapping: Dict[str, str], client: OpenAI, model: str, debug: bool) -> pd.DataFrame:
    cache = _load(CACHE_FILE)
    edges: List[Dict[str, str]] = []

    for idx, row in df.iterrows():
        tgt_code = _norm(str(row["科目コード"]))
        tgt_name = mapping.get(tgt_code, tgt_code)
        sentence = str(row["内容"])
        relation = classify(client, sentence, row["項目"], cache, model)

        hit = 0
        # A) explicit Iコード
        for code, _ in PATTERN_CODE.findall(sentence):
            edges.append({"source": mapping.get(_norm(code), code), "target": tgt_name, "label": relation})
            hit += 1
        # B) keyword
        for rx in KEYWORDS_REGEX:
            if rx.search(sentence):
                edges.append({"source": rx.pattern.split("(?:特論)?")[0], "target": tgt_name, "label": relation})
                hit += 1
        if debug:
            print(f"row {idx}: hits={hit} label={relation}")

    _save(cache, CACHE_FILE)
    return pd.DataFrame(edges).drop_duplicates().reset_index(drop=True)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="JAIST シラバス CSV → 依存グラフ (source,target,label)")
    ap.add_argument("--details", required=True, help="syllabus_details.csv")
    ap.add_argument("--master", required=True, help="jaist_syllabus_cs_ishikawa_2025.csv")
    ap.add_argument("--out", default="dependency_graph.csv", help="出力 CSV ファイル名")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="ChatGPT model (例: gpt-4o / o3)")
    ap.add_argument("--debug", action="store_true", help="行単位のヒット数を表示")
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("❌ OPENAI_API_KEY を環境変数に設定してください")

    client = OpenAI()
    details_df = pd.read_csv(args.details)
    master_df = pd.read_csv(args.master)
    mapping = build_map(master_df)
    dep_df = extract(details_df, mapping, client, args.model, args.debug)

    if dep_df.empty:
        sys.exit("⚠️  依存関係が 0 件でした。キーワード/正規表現を確認してください。")

    dep_df.to_csv(args.out, index=False)
    print(f"✅ 完了: {args.out} (rows={len(dep_df)})")

if __name__ == "__main__":
    main()
