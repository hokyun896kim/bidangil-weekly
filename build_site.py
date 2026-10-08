#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
비단길 주봉 매매판 · 사이트 데이터 생성
────────────────────────────────────────────────────────────────
scanner.py 의 판정 로직(analyse 등)을 그대로 import 해서 전 종목을 스캔하고
GitHub Pages 가 읽을 JSON 을 만든다. 판정 로직은 여기서 바꾸지 않는다.

  docs/data/latest.json               ← 화면이 fetch 하는 파일
  docs/data/history/YYYY-MM-DD.json   ← 스캔일별 누적 (지난 주 대비 계산용)

사용법
  python build_site.py               # 전 종목 (--top 0)
  python build_site.py --top 300     # 시총 상위 300 (동작 확인용)
  python build_site.py --demo        # 오프라인 샘플 데이터로 스키마·화면 확인
"""
import argparse, csv, datetime as dt, json, os, sys

import numpy as np
import pandas as pd

import scanner
from scanner import MA_S, MA_M, MA_L, TARGET_PCT, analyse, bull_block_low, data_flag
from kr_holidays import last_trading_day_of_week
import ledger

KST       = dt.timezone(dt.timedelta(hours=9))
ROOT      = os.path.dirname(os.path.abspath(__file__))
OUT_DIR   = os.path.join(ROOT, "docs", "data")
HIST_DIR  = os.path.join(OUT_DIR, "history")
TRADE_LOG = os.path.join(ROOT, "trade_log.csv")
STATES    = ["매수", "대기", "신규조건", "중단"]
WEEKS     = 20                     # 미니 캔들 주 수
CLOSE_HHMM = (15, 40)              # 장 마감(15:30) + 여유. 이후면 그 주 주봉 확정
TRACK_NAME = "주봉 신호"
LEDGER_STATE = os.path.join(OUT_DIR, "ledger_state.json")   # 엔진 상태 (다음 실행이 이어서 씀)
LEDGER_VIEW  = os.path.join(OUT_DIR, "ledger.json")         # 화면용 요약
WEEKS_INDEX  = os.path.join(OUT_DIR, "weeks.json")          # 주간 스캔 기록 목록
LEDGER_CFG   = os.path.join(ROOT, "ledger_config.json")     # 사용자 설정 (선택)


# ────────────────────────── 보조 ──────────────────────────
def _int(x):
    return None if x is None or x == "" or (isinstance(x, float) and np.isnan(x)) else int(x)


def _pct(a, b):
    return None if a is None or not b else round((a / b - 1) * 100, 1)


def bar_final_flag(base_monday: dt.date, now: dt.datetime):
    """기준 주봉이 확정됐는가 — 그 주 마지막 거래일 장 마감 이후면 True.
       금요일이 공휴일이면 목요일(등) 마감 후부터 확정으로 본다."""
    ltd = last_trading_day_of_week(base_monday)
    if ltd is None:
        return True, None
    close_at = dt.datetime(ltd.year, ltd.month, ltd.day, *CLOSE_HHMM, tzinfo=KST)
    return now >= close_at, ltd


# ────────────────────────── 종목별 항목 ──────────────────────────
def build_item(sym, name, df, res):
    """analyse() 결과 + 화면용 부가 정보(52주선·손절 거리·최근 20주 OHLC)."""
    d = df.copy()
    c = d["Close"]
    ma26  = c.rolling(MA_S).mean()
    ma52  = c.rolling(MA_M).mean()
    ma104 = c.rolling(MA_L).mean()
    up    = (c >= d["Open"]).values            # scanner 와 동일: 도지는 양봉
    lo    = d["Low"].values
    last  = len(d) - 1
    state = res["상태"]

    # 손절선: 매수·신규는 analyse 값 그대로.
    # 대기는 analyse 가 비워 두므로, 진행 중인 양봉 블록 저가(=첫 음봉 때의 손절선)를 표시용으로 계산.
    # 중단은 이탈한 그 선(직전 양봉 블록 저가)을 참고로 보여준다.
    stop = _int(res["손절선"])
    if state == "대기" and up[last]:
        stop = _int(bull_block_low(up, lo, last + 1))
    elif state == "중단":
        stop = _int(bull_block_low(up, lo, last))

    close = int(res["종가"])
    tail = d.tail(WEEKS)
    weekly = []
    for idx, row in tail.iterrows():
        m = ma104.loc[idx]
        weekly.append([idx.strftime("%m/%d"),
                       int(row["Open"]), int(row["High"]), int(row["Low"]), int(row["Close"]),
                       None if np.isnan(m) else int(m)])

    return {
        "name":      name,
        "code":      sym.split(".")[0],
        "market":    "KS" if sym.endswith(".KS") else "KQ",
        "state":     state,
        "cond_week": res["조건주"],
        "bar":       res["주봉"],
        "close":     close,
        "ma26":      int(ma26.iloc[-1]),
        "ma52":      int(ma52.iloc[-1]),
        "ma104":     int(ma104.iloc[-1]),
        "vs104_pct": _pct(close, ma104.iloc[-1]),
        "target":    _int(res["목표가"]),
        "stop":      stop,
        "stop_pct":  _pct(stop, close),
        "flag":      res["확인"],
        "weekly":    weekly,               # [날짜, 시, 고, 저, 종, 104주선]
    }


# ────────────────────────── 지난 주 대비 ──────────────────────────
def load_prev(scan_date):
    """이번 스캔일보다 앞선 '확정' 히스토리 중 가장 최근 것."""
    if not os.path.isdir(HIST_DIR):
        return None
    for fn in sorted(os.listdir(HIST_DIR), reverse=True):
        if not fn.endswith(".json") or fn[:-5] >= scan_date:
            continue
        try:
            h = json.load(open(os.path.join(HIST_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        if h.get("bar_final"):
            return h
    return None


def make_diff(items, prev):
    if prev is None:
        return {"base": None, "new": [], "dropped": [], "changed": []}
    old = {r["code"]: r for r in prev.get("items", [])}
    cur = {r["code"]: r for r in items}
    pick = lambda r, **kw: {"name": r["name"], "code": r["code"], **kw}
    return {
        "base":    prev.get("scan_date"),
        "new":     [pick(r, state=r["state"]) for k, r in cur.items() if k not in old],
        "dropped": [pick(r, state=r["state"]) for k, r in old.items() if k not in cur],
        "changed": [pick(r, before=old[k]["state"], state=r["state"])
                    for k, r in cur.items() if k in old and old[k]["state"] != r["state"]],
    }


# ────────────────────────── 신호 성적표 ──────────────────────────
def load_track():
    if not os.path.exists(TRADE_LOG):
        return []
    num = lambda s: float(s.replace(",", "")) if s and s.strip() else None
    out = []
    with open(TRADE_LOG, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("트랙") != TRACK_NAME:
                continue
            out.append({
                "no":         r["번호"],
                "name":       r["종목"],
                "buy_date":   r["매수일"],
                "buy_price":  _int(num(r["매수가"])),
                "target":     _int(num(r["익절가(+12%)"])),
                "stop":       _int(num(r["손절선"])),
                "invest":     _int(num(r["투입금(원)"])),
                "exit_date":  r["청산일"] or None,
                "exit_price": _int(num(r["청산가"])),
                "reason":     r["사유"] or None,
                "ret_pct":    num(r["수익률(%)"]),
                "pnl":        _int(num(r["손익(원)"])),
                "note":       r["비고"],
            })
    return out


# ────────────────────────── 데모 데이터 ──────────────────────────
def demo_data(n=4000, seed=7):
    """네트워크 없이 스키마·화면을 확인하기 위한 합성 주봉. 실제 종목 아님."""
    rng = np.random.default_rng(seed)
    end = dt.date.today()
    idx = pd.date_range(end=end - dt.timedelta(days=end.weekday()), periods=300, freq="W-MON")
    data, names = {}, {}
    for k in range(n):
        # 하락 → 바닥 → 반등 국면이 섞이도록 드리프트를 구간별로 바꾼다
        drift = np.repeat(rng.normal(0, 0.012, 6), 50)
        r = drift + rng.normal(0, 0.05, len(idx))
        close = 5000 * np.exp(np.cumsum(r))
        opn = close * np.exp(rng.normal(0, 0.03, len(idx)))
        hi = np.maximum(opn, close) * np.exp(np.abs(rng.normal(0, 0.02, len(idx))))
        lo = np.minimum(opn, close) * np.exp(-np.abs(rng.normal(0, 0.02, len(idx))))
        df = pd.DataFrame({"Open": opn.round(), "High": hi.round(),
                           "Low": lo.round(), "Close": close.round()}, index=idx)
        sym = f"{900000 + k:06d}." + ("KS" if k % 3 == 0 else "KQ")
        data[sym], names[sym] = df, f"샘플{k:04d}"
    return names, data, []


# ────────────────────────── 가상계좌 ──────────────────────────
def fetch_bench():
    """KOSPI·KOSDAQ 주봉 종가 (자산 곡선 비교용). 실패해도 계속."""
    try:
        import yfinance as yf
        out = {}
        for k, t in (("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")):
            d = yf.download(t, start="2025-01-01", interval="1wk", progress=False, auto_adjust=False)
            if len(d):
                c = d["Close"]
                out[k] = (c.iloc[:, 0] if hasattr(c, "columns") else c).dropna()
        return out
    except Exception as e:
        print(f"      지수 수집 실패: {e}")
        return {}


def run_ledger(data, tick, base_monday, final):
    """확정된 주까지 가상계좌를 진행하고 화면용 요약을 저장. 새 체결 이벤트 반환."""
    cfg = None
    if os.path.exists(LEDGER_CFG):
        try:
            cfg = json.load(open(LEDGER_CFG, encoding="utf-8"))
        except Exception as e:
            print(f"      ledger_config.json 읽기 실패 — 기본값 사용 ({e})")
    led = ledger.load(LEDGER_STATE, cfg)
    upto = pd.Timestamp(base_monday) - (pd.Timedelta(0) if final else pd.Timedelta(days=7))
    print(f"[가상계좌] {led['last_week'] or '새 장부(' + led['config']['start_week'] + '부터 백필)'} → {upto.date()} 처리", flush=True)
    weeks, new_events = ledger.advance(led, data, tick, upto)
    ledger.save(led, LEDGER_STATE)
    view = ledger.summarize(led, data, fetch_bench())
    view["generated_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    view["processed_now"] = [w.date().isoformat() for w in weeks]
    view["new_events"] = new_events
    ledger.save(view, LEDGER_VIEW)
    if new_events:
        ok = ledger.notify(new_events, view, len(led["equity"]))
        print(f"      체결 {len(new_events)}건" + (" · 텔레그램 전송" if ok else ""))
    return view


def write_weeks_index():
    """history/*.json 을 훑어 주간 기록 목록을 만든다 (확정 스캔만)."""
    rows = []
    if os.path.isdir(HIST_DIR):
        for fn in sorted(os.listdir(HIST_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                h = json.load(open(os.path.join(HIST_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            if not h.get("bar_final"):
                continue
            rows.append({"scan_date": h.get("scan_date"), "base_week": h.get("base_week"),
                         "last_trading_day": h.get("last_trading_day"), "counts": h.get("counts", {}),
                         "items": [{"name": x["name"], "code": x["code"], "state": x["state"]}
                                   for x in h.get("items", [])]})
    # 같은 주를 여러 번 돌렸으면 마지막 것만
    by_week = {}
    for r in rows:
        by_week[r["base_week"]] = r
    out = sorted(by_week.values(), key=lambda r: r["base_week"] or "", reverse=True)
    json.dump(out, open(WEEKS_INDEX, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


# ────────────────────────── 실행 ──────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=int(os.environ.get("SCAN_TOP", "0")),
                    help="시총 상위 N종목 (0=전체)")
    ap.add_argument("--demo", action="store_true", help="오프라인 합성 데이터 (history 미기록)")
    a = ap.parse_args()

    now = dt.datetime.now(KST)
    scan_date = now.strftime("%Y-%m-%d")

    if a.demo:
        print("[데모] 합성 데이터 사용 — 실제 종목 아님")
        tick, data, failed = demo_data()
    else:
        print(f"[1/3] 유니버스 (top={a.top or '전체'})", flush=True)
        tick = scanner.load_universe(a.top)
        print(f"      {len(tick)}종목", flush=True)
        print("[2/3] 주봉 수집", flush=True)
        data, failed = scanner.fetch_weekly(tick)
        print(f"\n      확보 {len(data)} · 실패 {len(failed)}", flush=True)
        if not data:
            print("수집된 데이터가 없어 latest.json 을 갱신하지 않는다.", file=sys.stderr)
            return 1

    print("[3/3] 판정", flush=True)
    excl, items = 0, []
    for s, df in data.items():
        f, r = data_flag(df)
        if f >= 8 or r >= 13:              # 거래정지 의심 배제 (scanner.main 과 동일)
            excl += 1
            continue
        res = analyse(df)
        if res:
            items.append(build_item(s, tick.get(s, s), df, res))

    order = {s: i for i, s in enumerate(STATES)}
    items.sort(key=lambda x: (order.get(x["state"], 9), x["name"]))

    base_monday = max(v.index[-1] for v in data.values()).date()
    final, ltd = bar_final_flag(base_monday, now)

    out = {
        "scan_date":     scan_date,
        "generated_at":  now.isoformat(timespec="seconds"),
        "base_week":     base_monday.isoformat(),
        "last_trading_day": ltd.isoformat() if ltd else None,
        "bar_final":     final,
        "demo":          a.demo,
        "universe":      len(tick),
        "fetched":       len(data),
        "excluded":      excl,
        "failed":        len(failed),
        "target_pct":    TARGET_PCT,
        "counts":        {s: sum(1 for x in items if x["state"] == s) for s in STATES},
        "items":         items,
        "diff":          make_diff(items, None if a.demo else load_prev(scan_date)),
        "track":         load_track(),
    }

    os.makedirs(HIST_DIR, exist_ok=True)
    dump = lambda p: json.dump(out, open(p, "w", encoding="utf-8"),
                               ensure_ascii=False, separators=(",", ":"))
    dump(os.path.join(OUT_DIR, "latest.json"))
    if not a.demo:
        dump(os.path.join(HIST_DIR, f"{scan_date}.json"))
        write_weeks_index()
        if a.top == 0:            # 가상계좌는 전 종목 스캔일 때만 진행 (부분 스캔은 신호 누락)
            run_ledger(data, tick, base_monday, final)
        else:
            print("[가상계좌] --top 지정 실행이라 건너뜀 (전 종목일 때만 진행)")

    print(f"      기준 주봉 {out['base_week']} · 확정 {final} · 정지의심 {excl} 배제")
    print("      " + " · ".join(f"{k} {v}" for k, v in out["counts"].items()))
    print(f"저장: docs/data/latest.json" + ("" if a.demo else f", history/{scan_date}.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
