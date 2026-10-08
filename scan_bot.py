#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한주에 한번매매 · 스캐너 봇
────────────────────────────────────────────────────────────────
깃허브 액션 크론으로 매주 금요일 장 마감 뒤 돌리고
결과를 텔레그램으로 보낸다.

환경변수
  TELEGRAM_TOKEN    봇 토큰
  TELEGRAM_CHAT_ID  받을 채팅방 ID
  SCAN_TOP          0이면 전 종목 (기본), 숫자면 시총 상위 N
"""
import os, sys, json, time, datetime as dt
import numpy as np
import pandas as pd
import requests
import warnings

warnings.filterwarnings("ignore")

KST     = dt.timezone(dt.timedelta(hours=9))
TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TOP     = int(os.environ.get("SCAN_TOP", "0"))
STATE   = "scan_state.json"
LOGCSV  = "scan_log.csv"


MA_S, MA_M, MA_L = 26, 52, 104      # 주봉: 6개월 / 12개월 / 24개월 대응
TARGET_PCT = 12                      # 1차 목표 (전량청산)
MIN_BARS = MA_L + 6                  # 최소 필요 주봉 수


# ────────────────────────── 데이터 ──────────────────────────


# ────────────────────────── 판정 ──────────────────────────
def prev_bear_high(o, c, h, i):
    """i(양봉) 직전의 연속 음봉 블록 고가 최대값.
       도지(시가=종가)는 양봉으로 보아 블록을 끊는다."""
    j = i - 1
    if j < 0 or c[j] >= o[j]:
        return None
    hi = -1.0
    while j >= 0 and c[j] < o[j]:
        hi = max(hi, h[j]); j -= 1
    return hi if hi > 0 else None


def corp_action_flag(df, look=104):
    """액면분할·병합·감자 등 자본변동 의심.
       조정되지 않은 데이터는 주간 배수가 정수에 가깝게 튄다."""
    c = df["Close"].tail(look + 1).values
    if len(c) < 3:
        return 0.0, False
    r = c[1:] / c[:-1]
    mx = float(np.nanmax(np.abs(np.log(r))))
    exact = False
    for x in r:
        if x <= 0 or np.isnan(x):
            continue
        for k in (2, 3, 4, 5, 10, 20):
            if abs(x - k) / k < 0.01 or abs(x - 1.0 / k) * k < 0.01:
                exact = True
    return float(np.expm1(mx)), exact


def data_flag(df, look=130):
    """거래정지·데이터 오염 의심.
       정지 구간이 마지막 가격으로 채워지면 이평선이 눌려
       가짜 '역배열 → 돌파' 신호가 만들어진다."""
    d = df.tail(look)
    flat = ((d["Open"] == d["High"]) & (d["High"] == d["Low"]) &
            (d["Low"] == d["Close"])).sum()
    ch = (d["Close"].diff() != 0).cumsum()
    run = d.groupby(ch).size().max()
    return int(flat), int(run)


def bull_block_low(up, lo_, k):
    """k 직전의 연속 양봉 블록 저가 최소값 = 손절선"""
    j = k - 1
    if j < 0 or not up[j]:
        return None
    v = float("inf")
    while j >= 0 and up[j]:
        v = min(v, lo_[j]); j -= 1
    return None if v == float("inf") else v


def analyse(df):
    d = df.copy()
    c = d["Close"]
    d["maS"] = c.rolling(MA_S).mean()
    d["maM"] = c.rolling(MA_M).mean()
    d["maL"] = c.rolling(MA_L).mean()
    d["up"]  = c >= d["Open"]          # 도지는 양봉
    if len(d) < MIN_BARS or np.isnan(d["maL"].iloc[-1]):
        return None

    flat, run = data_flag(df)
    if flat >= 8 or run >= 13:         # 주봉 기준(약 3개월분)
        return None
    mxmove, exact = corp_action_flag(df)

    o, h, lo_, cl = (d["Open"].values, d["High"].values,
                     d["Low"].values, d["Close"].values)
    maS, maM, maL, up = (d["maS"].values, d["maM"].values,
                         d["maL"].values, d["up"].values)
    n = len(d)

    def trig(i):
        if i < MA_L or np.isnan(maL[i]) or np.isnan(maM[i]):
            return False
        if not (cl[i-1] <= maL[i-1] and maS[i-1] < maL[i-1]):
            return False
        if not (up[i] and cl[i] > maL[i]):
            return False
        pbh = prev_bear_high(o, cl, h, i)
        if pbh is None or cl[i] <= pbh:
            return False
        return maS[i] > maM[i]

    last = n - 1
    res = {
        "확인":    ("자본변동?" if exact else ("급변동" if mxmove >= 1.5 else "")),
        "종가":    int(cl[last]),
        "26주선":  int(maS[last]),
        "104주선": int(maL[last]),
        "주봉":    "양봉" if up[last] else "음봉",
        "상태":    "",
        "조건주":  "",
        "목표가":  "",
        "손절선":  "",
    }

    # 보유 중이라면 이번 주 손절선 (직전 양봉 블록 저가)
    _sl = bull_block_low(up, lo_, last) if not up[last] else (
          bull_block_low(up, lo_, last + 1) if False else None)
    if _sl is None and up[last]:
        # 양봉 진행 중이면 이번 블록 저가가 다음 주 손절선이 된다
        j = last; v = float("inf")
        while j >= 0 and up[j]:
            v = min(v, lo_[j]); j -= 1
        _sl = None if v == float("inf") else v

    if trig(last):
        res["상태"]   = "신규조건"
        res["조건주"] = d.index[last].strftime("%Y-%m-%d")
        if _sl is not None:
            res["손절선"] = int(_sl)
        return res

    for i in range(last - 1, MA_L - 1, -1):
        if not trig(i):
            continue
        seg = up[i + 1:last + 1]
        if len(seg) == 0:
            break
        if seg.all():
            res["상태"]   = "대기"
            res["조건주"] = d.index[i].strftime("%Y-%m-%d")
        elif (not up[last]) and up[i + 1:last].all():
            bl = bull_block_low(up, lo_, last)
            if bl is not None and cl[last] < bl:
                res["상태"] = "중단"
            else:
                res["상태"]   = "매수"
                res["목표가"] = int(cl[last] * (1 + TARGET_PCT / 100))
                res["손절선"] = int(bl) if bl is not None else ""
            res["조건주"] = d.index[i].strftime("%Y-%m-%d")
        break
    return res if res["상태"] else None




# ══════════════ 데이터 ══════════════
def load_universe(top=0):
    import FinanceDataReader as fdr
    lst = fdr.StockListing("KRX")
    lst = lst[lst["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    bad = lst["Name"].str.contains("스팩|우$|우B$|우C$|리츠", regex=True, na=False)
    lst = lst[~bad]
    if top > 0 and "Marcap" in lst.columns:
        lst = lst.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False).head(top)
    return {r["Code"] + (".KS" if r["Market"] == "KOSPI" else ".KQ"): r["Name"]
            for _, r in lst.iterrows()}


def fetch_weekly(tickers):
    import yfinance as yf
    data, B = {}, 80
    failed = list(tickers)
    for attempt in (1, 2):
        target, failed = failed, []
        for i in range(0, len(target), B):
            ch = [s for s in target[i:i + B] if s not in data]
            if not ch:
                continue
            try:
                df = yf.download(ch, start="2016-01-01", interval="1wk",
                                 progress=False, group_by="ticker",
                                 auto_adjust=False, threads=True, timeout=25)
                for s in ch:
                    try:
                        sub = df[s].dropna(subset=["Open", "High", "Low", "Close"])
                        sub = sub[(sub["Close"] > 0) &
                                  (sub["High"] >= sub[["Open", "Close"]].max(axis=1)) &
                                  (sub["Low"] <= sub[["Open", "Close"]].min(axis=1))]
                        if len(sub) >= MIN_BARS:
                            data[s] = sub[["Open", "High", "Low", "Close"]]
                        else:
                            failed.append(s)
                    except Exception:
                        failed.append(s)
            except Exception:
                failed.extend(ch)
            time.sleep(0.4)
        print(f"  수집 {len(data)}종목 · 실패 {len(failed)}", flush=True)
        if not failed:
            break
    return data, failed


# ══════════════ 텔레그램 ══════════════
def send(text):
    if not TOKEN or not CHAT_ID:
        print("텔레그램 설정 없음. 출력만 합니다.\n"); print(text); return
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=30)
    print("전송:", r.status_code, r.text[:120])


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {"seq": 0, "last": {}}


# ══════════════ 실행 ══════════════
def main():
    now = dt.datetime.now(KST)
    st = load_state()
    st["seq"] = st.get("seq", 0) + 1
    seq = st["seq"]

    print(f"[1/3] 유니버스 (top={TOP or '전체'})", flush=True)
    tick = load_universe(TOP)
    print(f"      {len(tick)}종목", flush=True)

    print("[2/3] 주봉 수집", flush=True)
    data, failed = fetch_weekly(tick)
    print(f"      확보 {len(data)} · 실패 {len(failed)}", flush=True)

    print("[3/3] 판정", flush=True)
    excl, rows = 0, []
    for s, df in data.items():
        f, r = data_flag(df)
        if f >= 8 or r >= 13:
            excl += 1
            continue
        res = analyse(df)
        if res:
            res["종목"] = tick.get(s, s)
            res["코드"] = s.split(".")[0]
            rows.append(res)

    base = max(v.index[-1] for v in data.values()).strftime("%Y-%m-%d") if data else "?"
    buy   = [r for r in rows if r["상태"] == "매수"]
    wait  = [r for r in rows if r["상태"] == "대기"]
    fresh = [r for r in rows if r["상태"] == "신규조건"]
    halt  = [r for r in rows if r["상태"] == "중단"]

    L = [f"📊 <b>[한주에한번매매 #{seq}] 주간 스캔</b>",
         f"{now:%Y-%m-%d %H:%M} · 기준 주봉 {base}",
         f"대상 {len(data)}종목 · 정지의심 {excl} 배제"
         + (f" · <b>수집실패 {len(failed)}</b>" if failed else ""), ""]

    def block(title, items, note="", show_target=False):
        L.append(f"<b>{title}</b> ({len(items)}건)")
        if items:
            for r in items:
                mark = f" ⚠{r['확인']}" if r["확인"] else ""
                line = f"  · {r['종목']} ({r['코드']}) {r['종가']:,}원"
                if show_target and r["목표가"]:
                    line += f"\n     목표 {int(r['목표가']):,} · 손절 {r['손절선'] if r['손절선'] else '—'}"
                elif r["손절선"]:
                    line += f" / 손절선 {int(r['손절선']):,}"
                L.append(line + mark)
            if note:
                L.append(f"  <i>{note}</i>")
        else:
            L.append("  없음")
        L.append("")

    block("🔴 매수", buy, "금요일 종가 매수 → 예약매도 목표가 지정가", show_target=True)
    block("🟡 대기", wait, "다음 음봉이 나오는 주에 매수")
    block("🟢 신규조건", fresh, "다음 음봉을 기다린다")
    if halt:
        block("⛔ 중단", halt, "첫 음봉이 직전 양봉 저점 이탈 — 진입하지 않음")

    # 지난 회차 대비
    prev = st.get("last", {})
    cur = {r["코드"]: r["상태"] for r in rows}
    new  = [k for k in cur if k not in prev]
    chg  = [k for k in cur if k in prev and prev[k] != cur[k]]
    gone = [k for k in prev if k not in cur]
    if new or chg or gone:
        L.append("<b>지난 주 대비</b>")
        nm = {r["코드"]: r["종목"] for r in rows}
        for k in new:  L.append(f"  + {nm[k]} ({k}) → {cur[k]}")
        for k in chg:  L.append(f"  ~ {nm[k]} ({k}) {prev[k]} → {cur[k]}")
        for k in gone: L.append(f"  − {k} 조건 이탈")
        L.append("")

    L.append("⚠ 매수 전 확인: 관리종목 · 투자주의환기 · 감사의견 · 자본잠식")
    L.append("<i>종목당 계좌의 5% · 동시 8종목 · 손절은 금요일 종가로만 판정</i>")

    # 로그 누적
    if rows:
        df = pd.DataFrame(rows)
        df.insert(0, "스캔일", now.strftime("%Y-%m-%d"))
        df.insert(1, "기준주", base)
        cols = ["스캔일", "기준주", "상태", "종목", "코드", "조건주", "주봉",
                "종가", "목표가", "손절선", "확인"]
        df = df[cols]
        hdr = not os.path.exists(LOGCSV)
        df.to_csv(LOGCSV, mode="a", header=hdr, index=False, encoding="utf-8-sig")

    st["last"] = cur
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    send("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
