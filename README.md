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
