#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
비단길 주봉 가상계좌 (paper ledger)
────────────────────────────────────────────────────────────────
매주 확정된 주봉으로 RULES.md 규칙을 기계적으로 실행하고 결과를 쌓는다.
판정은 scanner.analyse() 를 그대로 쓴다. 이 파일은 '사고파는 쪽'만 담당한다.

  docs/data/ledger.json   ← 계좌 상태 + 신호 전수 기록 + 주간 자산 곡선 + 체결 이벤트

규칙 (RULES.md 확정본, 2026-10-08)
  진입  analyse() 상태가 '매수'인 주의 마지막 거래일 종가
  익절  매수가 +12% 예약매도. 주중 고가가 닿으면 체결, 시가가 이미 위면 시가 체결
  손절  진입 때 정한 손절선(직전 양봉 묶음 저가) 고정. 주봉 종가가 그 아래면 종가 청산
  우선  같은 주에 익절 터치와 손절 종가가 겹치면 익절 (예약매도가 먼저 체결)
  비중  매수 시점 계좌평가액의 5%, 동시 최대 8종목. 같은 종목 중복 보유 없음
  비용  왕복 0.3% (청산 시 일괄 차감)
  옵션  max_stop_pct — 손절선이 너무 먼 신호는 계좌가 건너뜀 (기본 꺼짐, 사용자 결정 사항)

신호 전수 기록
  슬롯이 차서 계좌가 못 산 신호도 같은 규칙으로 끝까지 추적한다(taken=False).
  → '계좌 성적'과 '신호 자체의 성적'을 따로 볼 수 있다.

안전장치
  · 처리한 마지막 주(last_week)를 저장해 같은 주를 두 번 처리하지 않는다
  · 실행이 몇 주 빠져도 빠진 주를 한 주씩 다시 판정해 따라잡는다 (최대 26주)
  · 미확정 주(주중 실행)는 처리하지 않는다
"""
import datetime as dt
import json
import math
import os

import numpy as np
import pandas as pd

from scanner import analyse
from kr_holidays import last_trading_day_of_week

CONFIG = {
    "capital":    30_000_000,
    "weight":     0.05,
    "slots":      8,
    "target_pct": 12.0,
    "cost_pct":   0.3,
    "start_week": "2026-08-03",   # 장부가 처음 만들어질 때 이 주부터 백필
    "max_stop_pct": None,         # 예: 30 → 손절선이 매수가보다 30% 넘게 멀면 계좌는 매수하지 않음(신호는 추적)
}
MAX_CATCHUP = 26
TAIL = 104 + 156          # 판정용으로 자르는 주봉 수


# ────────────────────────── 보조 ──────────────────────────
def _monday(d):
    d = pd.Timestamp(d).normalize()
    return d - pd.Timedelta(days=d.weekday())


def _trade_date(week):
    ltd = last_trading_day_of_week(week.date())
    return (ltd or (week + pd.Timedelta(days=4)).date()).isoformat()


def _bar(df, week):
    """그 주의 주봉 한 줄. 인덱스가 월요일이 아닐 수 있어 주 단위로 맞춘다."""
    if week in df.index:
        return df.loc[week]
    m = df.index[(df.index >= week) & (df.index < week + pd.Timedelta(days=7))]
    return df.loc[m[0]] if len(m) else None


def _close_asof(df, week):
    s = df["Close"][df.index < week + pd.Timedelta(days=7)]
    return float(s.iloc[-1]) if len(s) else None


def empty_ledger(cfg=None):
    cfg = {**CONFIG, **(cfg or {})}
    return {"version": 1, "config": cfg, "last_week": None,
            "cash": float(cfg["capital"]), "signals": [], "equity": [], "events": []}


def load(path, cfg=None):
    """저장된 장부. 없으면 새 장부. cfg 로 넘긴 설정은 기존 장부에도 덮어쓴다(앞으로의 주에만 적용)."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            led = json.load(f)
        led["config"] = {**CONFIG, **led.get("config", {}), **(cfg or {})}
        return led
    return empty_ledger(cfg)


def save(led, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, separators=(",", ":"))


# ────────────────────────── 주 단위 처리 ──────────────────────────
def _exit_check(sig, bar):
    """규칙대로 청산 여부. 반환 (가격, 사유) 또는 None."""
    o, h, c = float(bar["Open"]), float(bar["High"]), float(bar["Close"])
    tgt, stop = sig["target"], sig["stop"]
    if o >= tgt:
        return o, "익절(갭)"
    if h >= tgt:
        return tgt, "익절"
    if stop is not None and c < stop:
        return c, "손절"
    return None


def _close_signal(led, sig, week, px, why):
    cfg = led["config"]
    sig["exit_week"] = week.date().isoformat()
    sig["exit_date"] = _trade_date(week)
    sig["exit_price"] = round(px, 2)
    sig["reason"] = why
    sig["ret_pct"] = round((px / sig["entry_price"] - 1) * 100 - cfg["cost_pct"], 2)
    sig["weeks"] = int((week - pd.Timestamp(sig["entry_week"])).days // 7)
    if sig["taken"]:
        gross = sig["qty"] * px
        net = gross * (1 - cfg["cost_pct"] / 100)
        led["cash"] += net
        sig["pnl"] = int(round(net - sig["qty"] * sig["entry_price"]))
    led["events"].append({"week": sig["exit_week"], "date": sig["exit_date"], "type": why,
                          "name": sig["name"], "code": sig["code"], "price": round(px),
                          "ret_pct": sig["ret_pct"], "pnl": sig.get("pnl"), "taken": sig["taken"]})


def process_week(led, data, names, week, log=print):
    cfg = led["config"]
    wk_iso = week.date().isoformat()

    # 1) 열린 신호 청산 판정 (진입한 주 다음 주부터)
    for sig in led["signals"]:
        if sig.get("exit_week") or sig["entry_week"] >= wk_iso:
            continue
        df = data.get(sig["sym"])
        if df is None:
            continue
        bar = _bar(df, week)
        if bar is None:                       # 거래 없음(정지 등) → 다음 주로
            continue
        hit = _exit_check(sig, bar)
        if hit:
            _close_signal(led, sig, week, *hit)

    # 2) 신규 신호: 이 주까지 자른 데이터로 판정
    held = {s["sym"] for s in led["signals"] if not s.get("exit_week") and s["taken"]}
    open_sym = {s["sym"] for s in led["signals"] if not s.get("exit_week")}
    new = []
    for sym, df in data.items():
        if sym in open_sym:
            continue
        cut = df[df.index < week + pd.Timedelta(days=7)]
        if not len(cut) or _monday(cut.index[-1]) != week:
            continue
        last = cut.iloc[-1]
        if last["Close"] >= last["Open"]:     # '매수'는 그 주가 음봉일 때만 나온다 → 양봉 주는 건너뜀
            continue
        # 판정에 필요한 구간만 남긴다. 매수 신호는 최근 조건주 + 연속 양봉 뒤 첫 음봉이므로
        # 104주선 + 넉넉한 여유(156주)면 전체 이력으로 판정한 결과와 같다 (verify_tail 로 확인).
        cut = cut.tail(TAIL)
        try:
            res = analyse(cut)
        except Exception:
            continue
        if not res or res["상태"] != "매수" or res["손절선"] in ("", None):
            continue
        new.append((sym, res))
    new.sort(key=lambda x: x[0])

    # 3) 계좌 진입 — 평가액 기준 5%, 슬롯 순서는 종목코드 순
    equity_now = led["cash"] + sum(
        s["qty"] * (_close_asof(data[s["sym"]], week) or s["entry_price"])
        for s in led["signals"] if not s.get("exit_week") and s["taken"] and s["sym"] in data)
    for sym, res in new:
        px = float(res["종가"])
        stop = float(res["손절선"])
        sig = {"sym": sym, "code": sym.split(".")[0], "name": names.get(sym, sym),
               "cond_week": res["조건주"], "entry_week": wk_iso, "entry_date": _trade_date(week),
               "entry_price": px, "target": round(px * (1 + cfg["target_pct"] / 100), 2),
               "stop": stop, "stop_pct": round((stop / px - 1) * 100, 1),
               "flag": res.get("확인", ""), "taken": False, "qty": 0, "skip": None}
        msp = cfg.get("max_stop_pct")
        if msp and sig["stop_pct"] < -abs(msp):
            sig["skip"] = f"손절선 {sig['stop_pct']:.0f}% (기준 -{abs(msp):.0f}%)"
        elif len(held) >= cfg["slots"]:
            sig["skip"] = "슬롯 부족"
        else:
            qty = math.floor(equity_now * cfg["weight"] / px)
            if qty < 1 or qty * px > led["cash"]:
                sig["skip"] = "현금 부족"
            else:
                sig["taken"], sig["qty"] = True, qty
                led["cash"] -= qty * px
                held.add(sym)
        led["signals"].append(sig)
        led["events"].append({"week": wk_iso, "date": sig["entry_date"],
                              "type": "매수" if sig["taken"] else "미진입",
                              "name": sig["name"], "code": sig["code"], "price": round(px),
                              "qty": sig["qty"], "stop": round(stop), "target": round(sig["target"]),
                              "note": sig["skip"], "taken": sig["taken"]})

    # 4) 주말 평가
    pos = [s for s in led["signals"] if not s.get("exit_week") and s["taken"]]
    invested = sum(s["qty"] * (_close_asof(data[s["sym"]], week) or s["entry_price"])
                   for s in pos if s["sym"] in data)
    led["equity"].append({"week": wk_iso, "date": _trade_date(week),
                          "equity": int(round(led["cash"] + invested)),
                          "cash": int(round(led["cash"])), "invested": int(round(invested)),
                          "positions": len(pos)})
    led["last_week"] = wk_iso
    log(f"      {wk_iso} 신호 {len(new)} · 보유 {len(pos)} · 평가 {led['equity'][-1]['equity']:,}")


def advance(led, data, names, upto_week, log=print):
    """last_week 다음 주부터 upto_week(확정된 주)까지 처리. 처리한 주 목록 반환."""
    upto = _monday(upto_week)
    if led["last_week"]:
        start = pd.Timestamp(led["last_week"]) + pd.Timedelta(days=7)
    else:
        start = _monday(led["config"]["start_week"])
    weeks = list(pd.date_range(start, upto, freq="7D"))
    if len(weeks) > MAX_CATCHUP:
        log(f"      ⚠ 밀린 주 {len(weeks)}개 — 최근 {MAX_CATCHUP}주만 처리")
        weeks = weeks[-MAX_CATCHUP:]
    before = len(led["events"])
    for w in weeks:
        process_week(led, data, names, w, log)
    return weeks, led["events"][before:]


# ────────────────────────── 화면용 요약 ──────────────────────────
def summarize(led, data, bench=None):
    """현재 보유 평가·통계를 붙인 사본. bench: {"KOSPI": Series, "KOSDAQ": Series} 주봉 종가."""
    cfg = led["config"]
    out = json.loads(json.dumps(led))
    last = pd.Timestamp(led["last_week"]) if led["last_week"] else None

    for s in out["signals"]:
        if s.get("exit_week") or last is None:
            continue
        df = data.get(s["sym"])
        cur = _close_asof(df, last) if df is not None else None
        s["last_close"] = cur
        if cur:
            s["unreal_pct"] = round((cur / s["entry_price"] - 1) * 100, 2)
            s["to_target_pct"] = round((s["target"] / cur - 1) * 100, 1)
            s["to_stop_pct"] = round((s["stop"] / cur - 1) * 100, 1)
            s["weeks"] = int((last - pd.Timestamp(s["entry_week"])).days // 7)

    def stats(rows):
        done = [r for r in rows if r.get("exit_week")]
        wins = [r for r in done if r["ret_pct"] > 0]
        loss = [r for r in done if r["ret_pct"] <= 0]
        avg = lambda xs: round(float(np.mean(xs)), 2) if xs else None
        return {"n": len(rows), "closed": len(done), "open": len(rows) - len(done),
                "wins": len(wins), "losses": len(loss),
                "win_rate": round(len(wins) / len(done) * 100, 1) if done else None,
                "avg_ret": avg([r["ret_pct"] for r in done]),
                "avg_win": avg([r["ret_pct"] for r in wins]),
                "avg_loss": avg([r["ret_pct"] for r in loss]),
                "avg_weeks": avg([r["weeks"] for r in done]),
                "pnl": sum(r.get("pnl") or 0 for r in done)}

    out["stats"] = {"account": stats([s for s in out["signals"] if s["taken"]]),
                    "all_signals": stats(out["signals"])}

    eq = out["equity"]
    if eq:
        peak, mdd = 0, 0.0
        for r in eq:
            peak = max(peak, r["equity"])
            r["dd_pct"] = round((r["equity"] / peak - 1) * 100, 2)
            mdd = min(mdd, r["dd_pct"])
        out["stats"]["equity"] = {"start": cfg["capital"], "now": eq[-1]["equity"],
                                  "ret_pct": round((eq[-1]["equity"] / cfg["capital"] - 1) * 100, 2),
                                  "mdd_pct": mdd, "weeks": len(eq)}
        if bench:
            for k, ser in bench.items():
                ser = ser.dropna()
                if ser.empty:
                    continue
                base = None
                for r in eq:
                    w = pd.Timestamp(r["week"])
                    v = ser[ser.index < w + pd.Timedelta(days=7)]
                    if not len(v):
                        continue
                    v = float(v.iloc[-1])
                    if base is None:
                        base = v
                    r[k] = round((v / base - 1) * 100, 2)
    out["open_positions"] = [s for s in out["signals"] if not s.get("exit_week") and s["taken"]]
    return out


# ────────────────────────── 텔레그램 ──────────────────────────
def notify(new_events, summary, week_no):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat) or not new_events:
        return False
    import requests
    st = summary["stats"]
    L = [f"💼 <b>[한주에한번매매 #{week_no}] 가상계좌 체결</b>"]
    for e in new_events:
        if e["type"] == "매수":
            L.append(f"🔴 매수 {e['name']} {e['price']:,}원 × {e['qty']}주 · 목표 {e['target']:,} · 손절 {e['stop']:,}")
        elif e["type"] == "미진입":
            L.append(f"⚪ 미진입 {e['name']} ({e['note']}) — 신호만 추적")
        elif e.get("taken"):
            mark = "✅" if e["ret_pct"] > 0 else "❌"
            L.append(f"{mark} {e['type']} {e['name']} {e['price']:,}원 · {e['ret_pct']:+.1f}% · {e['pnl']:+,}원")
    eq = st.get("equity", {})
    a = st["account"]
    L.append("")
    L.append(f"평가 {eq.get('now', 0):,}원 ({eq.get('ret_pct', 0):+.2f}%) · MDD {eq.get('mdd_pct', 0):.1f}%")
    if a["closed"]:
        L.append(f"계좌 {a['wins']}승 {a['losses']}패 · 승률 {a['win_rate']}% · 보유 {a['open']}")
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": "\n".join(L), "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=20)
        return True
    except Exception:
        return False
