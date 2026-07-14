#!/usr/bin/env python3
# @feanor: persistent — подшивает полные ответы тулзов в трейсы bench.json
"""Обогащение трейсов бенча полными ответами инструментов.

Раннер снимает трейс с виджета (тул + аргумент + короткий статус), а ПОЛНЫЕ
ответы тулзов видны только серверу: каждый следующий /api/llm-запрос несёт их
в messages (role=tool). Сервер пишет это в лог диалогов (llm_log*.jsonl,
см. server.py `_log_convo`). Здесь мы джойним: кейс ↔ записи лога по окну
времени [t_start, t_end] из results.jsonl, берём последнюю запись кейса
(в ней вся история) и раскладываем tool-сообщения по вызовам трейса по порядку.

Используется bench_models.bench_one автоматически; вручную:
  python3 eval/attach_tool_results.py <bench_dir> <llm_log.jsonl>
  python3 eval/attach_tool_results.py --all      # все модели последней версии
"""
import json
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent


def _case_windows(res_path: Path):
    out = {}
    for l in res_path.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        out[r["case_id"]] = (r.get("t_start", 0), r.get("t_end", 0))
    return out


def _tool_msgs(rec):
    msgs = rec.get("request_messages") or []
    return [m.get("content") or "" for m in msgs if m.get("role") == "tool"]


def _tool_calls(rec):
    """Полные вызовы (имя + аргументы целиком, включая python-код) в порядке
    исполнения — виджетный трейс хранит только первую строку аргумента."""
    out = []
    for m in rec.get("request_messages") or []:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                out.append(fn.get("arguments") or "")
    return out


def attach(bench_dir: Path, convo_rows: list) -> int:
    """Подшить result в trace всех кейсов bench.json. Возвращает число кейсов."""
    bj = bench_dir / "bench.json"
    res = bench_dir / "results.jsonl"
    if not (bj.exists() and res.exists()):
        return 0
    windows = _case_windows(res)
    d = json.loads(bj.read_text(encoding="utf-8"))
    n = 0
    for c in d.get("cases", []):
        t0, t1 = windows.get(c["id"], (0, 0))
        if not t0:
            continue
        in_win = [r for r in convo_rows if t0 - 1 <= r.get("ts", 0) <= t1 + 3]
        if not in_win:
            continue
        # Нужна запись с САМОЙ ПОЛНОЙ историей (у финального /api/llm в messages
        # все вызовы и ответы тулзов). Брать просто последнюю запись окна нельзя:
        # в хвост окна (+3с) попадает первый запрос СЛЕДУЮЩЕГО кейса (system+user,
        # без тулов) — и кейс оставался без деталей (баг найден 2026-07-14).
        best = max(in_win, key=lambda r: (len(_tool_msgs(r)) + len(_tool_calls(r)),
                                          r.get("ts", 0)))
        tools = _tool_msgs(best)
        calls = _tool_calls(best)
        trace = c.get("trace") or []
        attached = False
        for i, t in enumerate(trace):
            if i < len(tools) and tools[i]:
                t["result"] = tools[i]
                attached = True
            if i < len(calls) and calls[i]:
                t["call_args"] = calls[i]
                attached = True
        if attached:
            n += 1
    bj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return n


def load_convo(path: Path):
    rows = []
    if not path.exists():
        return rows
    for l in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(l))
        except Exception:
            pass
    return rows


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--all":
        # --all [bench_v1] — явная версия; без неё — последняя bench_v*
        latest = (EVAL / args[1] if len(args) > 1
                  else sorted(EVAL.glob("bench_v*"), key=lambda p: (len(p.name), p.name))[-1])
        convo = load_convo(EVAL / "llm_log_dev.jsonl") + load_convo(EVAL / "llm_log.jsonl")
        for md in sorted(latest.iterdir()):
            if md.is_dir():
                n = attach(md, convo)
                if n:
                    print(f"{md.name}: подшито {n} кейсов")
    elif len(args) == 2:
        n = attach(Path(args[0]), load_convo(Path(args[1])))
        print(f"подшито {n} кейсов")
    else:
        sys.exit(__doc__)
