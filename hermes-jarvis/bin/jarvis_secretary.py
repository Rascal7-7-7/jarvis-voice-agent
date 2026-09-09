"""音声から秘書（プロジェクト横断ディスパッチャ）を呼ぶ経路。

到達経路は決定的なキーワードだけ
--------------------------------
``DELEGATION_SECURITY.md`` の中心原則は「**LLM Router ≠ security boundary**」。
router は gemma4:e2b で ``dangerous_action_accuracy 0.75`` と実測されている
（「gitで強制プッシュして」を CODEX と分類した）。したがって:

  * ``SECRETARY`` を ``jarvis_router.LABELS`` に**入れない**。
    LABELS は LLM 出力の検証に使われるので、入れなければモデルは
    この宛先を発明できない。
  * 到達は ``_OVERRIDES`` の決定的パターンのみ。
  * ``jarvis_gate.check()`` は override より前に走る
    （``jarvis_router.route`` の順序）。危険発話は SECRETARY に到達しない。

音声からは書き込まない（v1 の境界）
----------------------------------
JARVIS の委譲は ``--permission-mode plan`` / ``--sandbox read-only`` が argv に
ピン留めされ、``DELEGATION_SECURITY.md`` は「Write delegation. Not enabled.
There is no write path in either wrapper.」と明記している。

一方 ``secretary dispatch --headless`` は書き込む。両者を直結すると
**JARVIS が実質的な書き込み経路を獲得**してしまう。そこで v1 では:

  * 読み取り系（状況・一覧・レポート）は即実行する
  * dispatch は**キュー登録のみ**。実行はテキスト側の明示操作に委ねる

音声は「指示を取りこぼさず捕まえる」ところまでを担い、実行の引き金は人が引く。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Iterable, Mapping

SECRETARY_CLI = os.path.expanduser("~/work/scripts/secretary/secretary")
WORK_DIR = os.path.expanduser("~/work")

INTENT_BRIEF = "BRIEF"
INTENT_LIST = "LIST"
INTENT_REPORTS = "REPORTS"
INTENT_DISPATCH = "DISPATCH"
INTENT_UNKNOWN = "UNKNOWN"

# survey.sh がコミット 0 件のリポジトリに入れる番兵値
_NO_COMMIT_DAYS = 999

# 決定的なトリガ。router の _OVERRIDES に同じものを登録する。
TRIGGER = re.compile(r"(秘書|ひしょ)")

_BRIEF_WORDS = re.compile(
    r"(状況|停滞|止まって|とまって|進捗|どうなって|残ってる|放置)")
_LIST_WORDS = re.compile(r"(一覧|リスト|全部|どんなプロジェクト)")
_REPORTS_WORDS = re.compile(r"(レポート|報告書|結果を見)")


def matches(utterance: str | None) -> bool:
    """秘書宛かどうか。名前を呼ばれた時だけ True。"""
    if not utterance:
        return False
    return bool(TRIGGER.search(utterance))


def known_projects(work_dir: str = WORK_DIR) -> list[str]:
    """~/work 直下のプロジェクト名。設定ではなく実体から取る。"""
    skip = {"_assets", "logs", "scripts"}
    try:
        return sorted(n for n in os.listdir(work_dir)
                      if n not in skip and not n.startswith(".")
                      and os.path.isdir(os.path.join(work_dir, n)))
    except OSError:
        return []


def _find_project(utterance: str, projects: Iterable[str]) -> str | None:
    """発話に含まれるプロジェクト名。**最長一致**で選ぶ。

    短い名前が長い名前の部分文字列になっていることがあるため
    （例: 'trading' と 'tradingview-mcp'）、長い順に照合する。
    """
    lowered = utterance.lower()
    for name in sorted(projects, key=len, reverse=True):
        if name.lower() in lowered:
            return name
    return None


def classify(utterance: str,
             projects: Iterable[str] | None = None) -> tuple[str, dict[str, Any]]:
    """発話を秘書の意図に落とす。すべて決定的で、モデルを使わない。

    返り値の ``action`` が実行方針:
      ``read``    … 読み取りのみ。即実行してよい
      ``enqueue`` … 書き込みを伴う。キュー登録だけ行い実行はしない
    """
    text = utterance or ""
    names = list(projects) if projects is not None else known_projects()

    if _BRIEF_WORDS.search(text):
        return INTENT_BRIEF, {"action": "read"}
    if _LIST_WORDS.search(text):
        return INTENT_LIST, {"action": "read"}
    if _REPORTS_WORDS.search(text):
        return INTENT_REPORTS, {"action": "read"}

    project = _find_project(text, names)
    if project:
        # プロジェクト名とトリガ語を落として指示本文にする
        instruction = TRIGGER.sub("", text)
        instruction = re.sub(re.escape(project), "", instruction,
                             flags=re.IGNORECASE)
        instruction = instruction.strip(" 、。,.の・").strip()
        return INTENT_DISPATCH, {"action": "enqueue", "project": project,
                                 "instruction": instruction or text}

    return INTENT_UNKNOWN, {"action": "read"}


def summarize_brief(survey: Mapping[str, Any] | None) -> str:
    """survey --json を読み上げ可能な1文にする。TTS 用なので短く。"""
    projects = list((survey or {}).get("projects") or [])
    stalled = [p for p in projects if p.get("stalled")]
    if not stalled:
        return f"停滞しているプロジェクトはありません。全{len(projects)}件を確認しました。"
    # survey.sh はコミットが 1 件も無いリポジトリに idle_days=999 を入れる。
    # これは「日数が不明」を表す番兵なので、そのまま「999日放置」と読み上げると
    # 事実と違うことを言うことになる。日数のあるものと分けて扱う。
    dated = [p for p in stalled if int(p.get("idle_days") or 0) < _NO_COMMIT_DAYS]
    never = [p for p in stalled if int(p.get("idle_days") or 0) >= _NO_COMMIT_DAYS]

    head = f"停滞は{len(stalled)}件です。"
    if dated:
        worst = max(dated, key=lambda p: int(p.get("idle_days") or 0))
        head += (f"最長は{worst.get('name')}で{worst.get('idle_days')}日放置、"
                 f"未コミット{worst.get('dirty')}件です。")
    if never:
        head += f"うち{len(never)}件はまだ一度もコミットされていません。"
    return head


# ---------------------------------------------------------------- execution


def _run_cli(args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    """秘書 CLI を叩く。

    ``errors="replace"`` は必須。呼び先はシェルスクリプトで、ロケールや
    プロファイルの都合で不正バイトが混ざり得る（実測: strict デコードで
    ``UnicodeDecodeError: invalid continuation byte``）。読み上げ文を作るために
    呼んでいるのだから、1 バイトの欠けで全体を落としてはいけない。
    LC_ALL も明示して、呼び先が UTF-8 で出すようにする。
    """
    env = dict(os.environ, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8")
    try:
        p = subprocess.run([SECRETARY_CLI] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, check=False, env=env)
        return p.returncode, (p.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 70, f"秘書コマンドを実行できませんでした: {exc}"


def handle(utterance: str,
           projects: Iterable[str] | None = None) -> str:
    """発話を処理して、読み上げる文を返す。

    書き込みは行わない。dispatch はキュー登録までで止める。
    """
    intent, payload = classify(utterance, projects=projects)

    if intent == INTENT_BRIEF:
        rc, out = _run_cli(["survey", "--json"])
        if rc != 0:
            return "状況の取得に失敗しました。"
        try:
            return summarize_brief(json.loads(out))
        except (ValueError, TypeError):
            return "状況の解析に失敗しました。"

    if intent == INTENT_LIST:
        rc, out = _run_cli(["list"])
        if rc != 0:
            return "一覧の取得に失敗しました。"
        rows = [l for l in out.splitlines() if l.strip()][2:]
        return f"プロジェクトは{len(rows)}件あります。詳細は画面で確認してください。"

    if intent == INTENT_REPORTS:
        rc, out = _run_cli(["reports"])
        if rc != 0:
            return "レポートの取得に失敗しました。"
        n = len([l for l in out.splitlines() if l.strip().endswith(".md")])
        return (f"レポートは{n}件あります。" if n
                else "レポートはまだありません。")

    if intent == INTENT_DISPATCH:
        # キュー登録のみ。--headless は音声からは呼ばない。
        rc, _out = _run_cli(["dispatch", payload["project"],
                             payload["instruction"]])
        if rc != 0:
            return f"{payload['project']} への登録に失敗しました。"
        return (f"{payload['project']} への指示をキューに入れました。"
                "実行は画面から確認してください。")

    return ("秘書にできるのは、状況の確認、プロジェクト一覧、"
            "レポートの確認、それにプロジェクト名を指定した指示の登録です。")
