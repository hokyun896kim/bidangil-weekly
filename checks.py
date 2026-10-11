#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
매수 전 체크 자동 판정
────────────────────────────────────────────────────────────────
화면의 체크리스트 4개를 공개 데이터로 자동 판정한다. 매수·대기·신규 종목에만 돌린다(주당 몇 건).

  ① 관리종목·투자경고·위험·거래정지   KRX 소속부 + 네이버 공시 제목(최근 4주)
  ② 감사의견 비적정·자본잠식         공시 제목(최근 13개월) + 연간 유보율 < 0
  ③ 유상증자·감자·분할/병합          공시 제목(최근 6개월, 자회사 공시 제외)
  ④ 급등 사유                       8주 상승률 + 최근 뉴스 제목 (사유 자체는 사람이 읽고 판단)

판정
  제외  하나라도 걸리면 매수하지 않는다 (가상계좌도 건너뜀)
  주의  매수는 규칙대로 하되 화면에 경고 (투자주의, 전환사채 발행, 급등 등)
  통과  걸린 항목 없음
  미확인  데이터를 못 받음 → 사람이 직접 확인

원천: m.stock.naver.com 공개 API (키 불필요), FinanceDataReader KRX 목록의 소속부(Dept)
"""
import datetime as dt
import json
import re
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (bidangil-weekly scanner)"}
NAVER = "https://m.stock.naver.com/api"

# (정규식, 단계, 항목번호, 조회기간(일), 표시 문구)
RULES = [
    (r"관리종목\s*지정|관리종목 지정", "제외", 1, 400, "관리종목 지정"),
    (r"투자위험종목\s*지정(?!예고|해제)", "제외", 1, 28, "투자위험종목 지정"),
    (r"(?<!\])투자경고종목\s*지정(?!예고|해제)", "제외", 1, 28, "투자경고종목 지정"),
    (r"매매거래\s*정지", "제외", 1, 28, "매매거래정지(예고 포함)"),
    (r"상장폐지|상장적격성", "제외", 1, 400, "상장폐지·적격성 심사"),
    (r"의견\s*거절|부적정|한정\s*의견|감사의견", "제외", 2, 400, "감사의견 관련 공시"),
    (r"자본\s*잠식", "제외", 2, 400, "자본잠식 공시"),
    (r"유상증자\s*결정", "제외", 3, 183, "유상증자 결정"),
    (r"감자\s*결정", "제외", 3, 183, "감자 결정"),
    (r"주식\s*분할|액면\s*분할|주식\s*병합|액면\s*병합", "제외", 3, 183, "액면분할·병합"),
    (r"\[투자주의\]|투자경고종목\s*지정\s*예고|투자주의\s*환기", "주의", 1, 28, "투자주의·경고 예고"),
    (r"불성실\s*공시", "주의", 1, 183, "불성실공시"),
    (r"조회공시\s*요구", "주의", 4, 28, "현저한 시황변동 조회공시"),
    (r"전환사채|신주인수권부사채|교환사채|추가상장\(.*CB", "주의", 3, 183, "CB·BW 발행/전환 (물량 부담)"),
    (r"무상증자", "주의", 3, 183, "무상증자"),
]
SKIP_TITLE = re.compile(r"종속회사|자회사|해제")    # 자회사 공시·해제 공시는 판정에서 뺀다
SURGE_PCT = 60        # 8주 저점 대비 +60% 이상이면 '급등' 주의


def _get(url, tries=2):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception:
            time.sleep(1 + k)
    return None


def _disclosures(code, days=400, max_pages=5):
    out, cutoff = [], dt.datetime.now() - dt.timedelta(days=days)
    for p in range(1, max_pages + 1):
        rows = _get(f"{NAVER}/stock/{code}/disclosure?pageSize=30&page={p}")
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            try:
                t = dt.datetime.fromisoformat(r["datetime"][:19])
            except Exception:
                continue
            out.append((t, r.get("title", "")))
        if out and out[-1][0] < cutoff:
            break
    return out if out or rows is not None else None


def _news(code, n=3):
    rows = _get(f"{NAVER}/news/stock/{code}?pageSize={n}&page=1")
    out = []
    if isinstance(rows, list):
        for g in rows:
            for it in g.get("items", [])[:1]:
                d = it.get("datetime", "")
                out.append({"date": f"{d[4:6]}/{d[6:8]}" if len(d) >= 8 else "",
                            "title": it.get("titleFull") or it.get("title", ""),
                            "office": it.get("officeName", ""),
                            "url": it.get("mobileNewsUrl", "")})
    return out[:n]


def _reserve_ratio(code):
    """가장 최근 확정 연도 유보율(%). 음수면 자본잠식."""
    f = _get(f"{NAVER}/stock/{code}/finance/annual")
    try:
        info = f["financeInfo"]
        keys = [t["key"] for t in info["trTitleList"] if t.get("isConsensus") == "N"]
        for row in info["rowList"]:
            if row["title"] == "유보율":
                for k in reversed(keys):
                    v = row["columns"].get(k, {}).get("value")
                    if v not in (None, "", "-"):
                        return float(v.replace(",", "")), k
    except Exception:
        pass
    return None, None


def check(code, dept="", weekly=None):
    """한 종목 판정. weekly: [[날짜,시,고,저,종,...], ...] 최근 주봉(급등 계산용)."""
    now = dt.datetime.now()
    reasons, ok_sources = [], 0

    if dept and "관리종목" in dept:
        reasons.append({"level": "제외", "item": 1, "text": "관리종목 (KRX 소속부)", "date": ""})
    elif dept and "투자주의환기" in dept:
        reasons.append({"level": "주의", "item": 1, "text": "투자주의환기종목 (KRX 소속부)", "date": ""})

    disc = _disclosures(code)
    if disc is not None:
        ok_sources += 1
        seen = set()
        for t, title in disc:
            if SKIP_TITLE.search(title):
                continue
            for pat, lvl, item, days, label in RULES:
                if (now - t).days <= days and re.search(pat, title):
                    if label in seen:
                        break
                    seen.add(label)
                    reasons.append({"level": lvl, "item": item, "text": label,
                                    "date": t.strftime("%m/%d"), "title": title})
                    break

    rr, yr = _reserve_ratio(code)
    if rr is not None:
        ok_sources += 1
        if rr < 0:
            reasons.append({"level": "제외", "item": 2, "text": f"자본잠식 의심 (유보율 {rr:.0f}%, {yr[:4]})", "date": ""})

    surge = None
    if weekly and len(weekly) >= 2:
        w8 = weekly[-8:]
        lo = min(b[3] for b in w8)
        surge = round((weekly[-1][4] / lo - 1) * 100, 1) if lo else None
        if surge is not None and surge >= SURGE_PCT:
            reasons.append({"level": "주의", "item": 4, "text": f"8주 저점 대비 +{surge:.0f}% 급등 — 뉴스로 사유 확인", "date": ""})

    news = _news(code)

    lv = [r["level"] for r in reasons]
    if "제외" in lv:
        verdict = "제외"
    elif ok_sources == 0:
        verdict = "미확인"
    elif "주의" in lv:
        verdict = "주의"
    else:
        verdict = "통과"
    order = {"제외": 0, "주의": 1}
    reasons.sort(key=lambda r: (order.get(r["level"], 2), r["item"]))
    return {"verdict": verdict, "reasons": reasons, "news": news, "surge_8w": surge,
            "checked_at": now.strftime("%Y-%m-%d %H:%M")}


def dept_map():
    """종목코드 → KRX 소속부. 실패하면 빈 dict."""
    try:
        import FinanceDataReader as fdr
        lst = fdr.StockListing("KRX")
        return dict(zip(lst["Code"], lst["Dept"].fillna("")))
    except Exception:
        return {}


if __name__ == "__main__":
    import sys
    dm = dept_map()
    for c in sys.argv[1:] or ["127710", "001210"]:
        print(c, json.dumps(check(c, dm.get(c, "")), ensure_ascii=False, indent=1))
