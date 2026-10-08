#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한주에 한번매매 · 주봉 스캐너
────────────────────────────────────────────────────────────────
매주 금요일 장 마감 뒤 한 번 돌린다.

  조건 ①  26주선 < 104주선 (역배열) 상태였다
  조건 ②  양봉으로 104주선 위로 올라왔다
  조건 ③  그 양봉이 직전 음봉(합봉)의 고점을 종가상 돌파했다
  조건 ④  26주선 > 52주선 (볼린저 중심선)

  → 네 조건 충족 후 '첫 음봉'이 나오는 주의 금요일 종가에 매수
  → 예약매도 +12% 지정가, 전량 청산 (러너 없음)
  → 손절: 음봉이 직전 양봉 블록의 저점을 종가상 이탈하면 청산

월봉판(6/24개월선)에서 주봉으로 옮긴 이유
  같은 종목·같은 기간으로 통제 비교한 결과
    월봉  승률 58.8% · 중앙 +4.16% · 손익비 2.35 · 보유 17.9개월
    주봉  승률 73.4% · 중앙 +9.39% · 손익비 2.84 · 보유 10.2개월
  포트폴리오(8종목) 기준 CAGR은 주봉 전량청산 12%가 가장 높았다.
  러너는 자리를 오래 점유해 새 신호를 놓치므로 쓰지 않는다.

손절 규칙 (목표12% · 8자리 · 종목당10% 기준 검증)
  손절 없음        CAGR +12.4% · MDD -25.8% · 샤프 0.76
  캔들 매도신호    CAGR +15.7% · MDD -20.6% · 샤프 1.06   ← 채택
  고정 -20%        CAGR +10.3% · MDD -28.9%
  고정 -15%        CAGR  +9.3% · MDD -38.2%
  고정 -10%        CAGR  +7.1% · MDD -26.4%
  캔들 손절만이 수익을 올리면서 낙폭을 줄인다. 고정 손절률은 전부 해롭다.

비중과 낙폭 (목표12% · 캔들손절 · 8자리)
  종목당 12.5% → CAGR +19.7% · MDD -25.2%
  종목당 10.0% → CAGR +15.7% · MDD -20.6%
  종목당  7.5% → CAGR +11.6% · MDD -15.8%
  종목당  5.0% → CAGR  +7.7% · MDD -10.8%

출력
  1. [매수] 이번 주가 첫 음봉인 종목        ← 오늘 주문할 것
  2. [대기] 조건 충족했고 첫 음봉 기다리는 중
  3. [신규] 이번 주 조건이 새로 충족된 종목
  4. [중단] 첫 음봉이 직전 양봉 저점을 이탈 — 진입하지 않음

사용법
  pip install finance-datareader yfinance pandas numpy
  python 한주에한번매매_스캐너.py                # 전 종목
  python 한주에한번매매_스캐너.py --top 800     # 시총 상위만
  python 한주에한번매매_스캐너.py --csv out.csv
"""
import argparse, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

MA_S, MA_M, MA_L = 26, 52, 104      # 주봉: 6개월 / 12개월 / 24개월 대응
TARGET_PCT = 12                      # 1차 목표 (전량청산)
MIN_BARS = MA_L + 6                  # 최소 필요 주봉 수


# ────────────────────────── 데이터 ──────────────────────────
def load_universe(top: int):
    import FinanceDataReader as fdr
    lst = fdr.StockListing("KRX")
    lst = lst[lst["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    bad = lst["Name"].str.contains("스팩|우$|우B$|우C$|리츠", regex=True, na=False)
    lst = lst[~bad]
    if top > 0 and "Marcap" in lst.columns:
        lst = lst.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False).head(top)
    return {r["Code"] + (".KS" if r["Market"] == "KOSPI" else ".KQ"): r["Name"]
            for _, r in lst.iterrows()}


def fetch_weekly(tickers, start="2016-01-01"):
    """주봉 수집. 실패분은 한 번 재시도하고 남은 목록을 함께 반환한다."""
    import yfinance as yf
    data, syms, B = {}, list(tickers), 80
    failed = list(syms)
    for attempt in (1, 2):
        target, failed = failed, []
        for i in range(0, len(target), B):
            ch = [s for s in target[i:i + B] if s not in data]
            if not ch:
                continue
            try:
                df = yf.download(ch, start=start, interval="1wk", progress=False,
                                 group_by="ticker", auto_adjust=False,
                                 threads=True, timeout=25)
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
            print(f"  수집 {min(i+B, len(target))}/{len(target)}  확보 {len(data)}",
                  end="\r", flush=True)
        if not failed:
            break
        print(f"\n  재시도 {len(failed)}종목", flush=True)
    print(" " * 50, end="\r")
    return data, failed


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


# ────────────────────────── 실행 ──────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0,
                    help="시총 상위 N종목 (0=전체). 순위는 '오늘' 기준이라 "
                         "과거 성과 비교에 look-ahead가 섞인다. 기본은 전체.")
    ap.add_argument("--csv", type=str, default="")
    a = ap.parse_args()

    print(f"[1/3] 유니버스 (시총 상위 {a.top or '전체'})")
    tick = load_universe(a.top)
    print(f"      {len(tick)}종목")

    print("[2/3] 주봉 수집")
    data, failed = fetch_weekly(tick)
    print(f"      확보 {len(data)}종목" + (f" · 수집실패 {len(failed)}" if failed else ""))

    print("[3/3] 판정")
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
    print(f"      기준 주봉 {base} · 거래정지 의심 {excl}종목 배제\n")

    if not rows:
        print("조건 충족 종목 없음. 정상이다 — 주당 몇 건 수준이다.")
        return

    out = pd.DataFrame(rows)[["상태", "종목", "코드", "조건주", "주봉",
                              "종가", "목표가", "손절선", "26주선", "104주선", "확인"]]
    order = {"매수": 0, "대기": 1, "신규조건": 2, "중단": 3}
    out = (out.assign(_o=out["상태"].map(order).fillna(9))
              .sort_values(["_o", "종목"]).drop(columns="_o"))

    for st, title in [("매수",     f"■ 매수  — 오늘 금요일 종가에 주문, 예약매도 +{TARGET_PCT}%"),
                      ("대기",     "■ 대기  — 첫 음봉이 나오면 매수"),
                      ("신규조건", "■ 신규  — 이번 주 조건 충족, 다음 음봉 대기"),
                      ("중단",     "■ 중단  — 첫 음봉이 직전 양봉 저점 이탈, 진입하지 않음")]:
        sub = out[out["상태"] == st]
        print(f"{title}   ({len(sub)}건)")
        print(sub.drop(columns=["상태"]).to_string(index=False) if len(sub) else "  없음")
        print()

    print("─" * 68)
    print("반드시 수기 확인할 것 — 자동 판정 불가")
    print("  · 관리종목 / 투자주의환기종목")
    print("  · 감사의견 비적정 / 자본잠식")
    print("  · 최근 유상증자 · 감자")
    print("")
    print("자동 배제 · 표시")
    print("  · 거래정지 의심 (시=고=저=종 8주 이상 또는 같은 종가 13주 연속)")
    print("  · '자본변동?' — 주간 배수가 정수에 근접 (액면분할·병합 의심)")
    print("  · '급변동'    — 최근 2년 중 주간 +150% 이상")
    print("")
    print("운용 기준")
    print(f"  · 목표 +{TARGET_PCT}% 전량 청산, 러너 없음")
    print("  · 손절 = 음봉이 직전 양봉 블록 저점을 종가상 이탈 (고정 손절률 쓰지 않음)")
    print("  · 손절선은 매주 갱신된다. 금요일 종가로만 판정한다.")
    print("  · 동시 8종목 · 종목당 계좌의 10%")
    print("  · 신호는 주당 평균 1.7건(전 종목 기준). 몇 주 연속 0건인 것이 정상이다.")
    print("  · 진입 신호 자체는 랜덤 대비 우위가 확인되지 않았다.")
    print("    성과는 청산 규칙을 지키는 데서 나온다.")
    print("─" * 68)

    if a.csv:
        out.to_csv(a.csv, index=False, encoding="utf-8-sig")
        print(f"\n저장: {a.csv}")


if __name__ == "__main__":
    sys.exit(main())
