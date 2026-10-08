# bidangil-weekly — 비단길 주봉 매매판

매주 금요일 장 마감 후 "이번 주 매매 예정 종목군"을 폰에서 보는 정적 페이지.

| 파일 | 역할 |
|---|---|
| `scanner.py` | 판정 로직 원본 (**수정 금지**) |
| `build_site.py` | scanner.py 를 import 해 전 종목 스캔 → `docs/data/latest.json`, `docs/data/history/YYYY-MM-DD.json` |
| `kr_holidays.py` | KRX 휴장일(2026·2027) — 그 주 마지막 거래일·주봉 확정 판단 |
| `docs/index.html` | 매매판 화면 (단일 파일, latest.json 을 fetch) |
| `scan_bot.py` | 기존 텔레그램 알림 |
| `.github/workflows/scanner.yml` | 매주 금 16:30 KST: 텔레그램 → build_site → docs 커밋 |
| `trade_log.csv` | 가상매매 기록 (「주봉 신호」 트랙이 성적표로 표시됨) |

## 로컬 실행

```bash
pip install finance-datareader yfinance pandas numpy requests
python build_site.py            # 전 종목 (15~20분)
python build_site.py --top 300  # 빠른 확인
python build_site.py --demo     # 네트워크 없이 합성 데이터로 화면 확인 (history 미기록)
python -m http.server -d docs   # → http://localhost:8000
```

`docs/index.html` 을 파일로 직접 열면 브라우저가 fetch 를 막으므로 위처럼 로컬 서버로 연다.

## 휴장일 갱신

매년 12월 KRX 휴장일 공지를 보고 `kr_holidays.py` 에 다음 해를 추가한다.

## 가상계좌 (자동 모의 기록)

매주 확정 스캔 때 `ledger.py` 가 RULES.md 규칙을 기계적으로 실행해 결과를 쌓는다.

- 규칙: 3,000만 원 · 매수 시점 평가액 5% · 동시 8종목 · +12% 예약매도(갭은 시가) · 진입 손절선 고정, 주봉 종가 이탈 시 청산 · 같은 주 겹치면 익절 우선 · 왕복 비용 0.3%
- 슬롯이 차서 못 산 신호도 같은 규칙으로 끝까지 추적 → 화면의 「계좌 vs 신호 전체」
- 실행이 빠진 주는 다음 실행 때 한 주씩 다시 판정해 따라잡는다(최대 26주). 같은 주는 두 번 처리하지 않는다
- 전 종목(`top=0`) 실행일 때만 진행. 체결이 있으면 텔레그램으로 「[한주에한번매매 #N] 가상계좌 체결」 알림
- 설정은 `ledger_config.json` (예: `"max_stop_pct": 30` → 손절선이 30%보다 먼 신호는 계좌가 매수하지 않음). 바꾼 설정은 다음 주부터 적용
- 파일: `docs/data/ledger_state.json`(엔진 상태 — 지우면 처음부터 백필) · `docs/data/ledger.json`(화면용) · `docs/data/weeks.json`(주간 스캔 목록)
- 검증: 8/3주부터 백필한 결과가 수동 기록(티앤엘 익절 · 레이언스 익절 · 에스넷 손절)과 날짜·가격까지 일치
