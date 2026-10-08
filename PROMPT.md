이 폴더의 SPEC.md 대로 "비단길 주봉 매매판" 웹앱을 만들어줘.

- scanner.py 의 판정 로직(analyse 등)은 절대 수정하지 말고 import 해서 써.
- build_site.py 를 새로 만들어서 전 종목 스캔 → docs/data/latest.json 과 history 를 생성해.
- docs/index.html 은 단일 파일(인라인 CSS/JS)로, latest.json 을 fetch 해서 렌더해.
- scanner.yml 을 수정해서 기존 텔레그램 알림 + build_site.py 실행 + docs/ 커밋까지 하게 해.
- 먼저 build_site.py 를 로컬에서 돌려 latest.json 이 스키마대로 나오는지 보여주고, 그다음 화면을 만들어.
- 마지막에 GitHub Pages 켜는 법을 단계별로 알려줘.
