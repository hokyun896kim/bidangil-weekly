// 비단길 주봉 — Scriptable 위젯
// 데이터: https://bidangil-weekly.vercel.app (매주 금요일 자동 갱신)
// 크기: 소·중·대 모두 지원. 위젯을 누르면 웹 매매판이 열린다.
// 설치: Scriptable 앱 → + → 이 코드 전체 붙여넣기 → 이름 "비단길" → 홈 화면 위젯 추가 → Script: 비단길

const BASE = "https://bidangil-weekly.vercel.app";
const C = {
  bg:    Color.dynamic(new Color("#ffffff"), new Color("#1c1c21")),
  fill:  Color.dynamic(new Color("#f2f4f6"), new Color("#2b2b32")),
  text:  Color.dynamic(new Color("#191f28"), new Color("#f2f2f4")),
  muted: Color.dynamic(new Color("#6b7684"), new Color("#9e9ea7")),
  faint: Color.dynamic(new Color("#b0b8c1"), new Color("#5d5d66")),
  up:    Color.dynamic(new Color("#f04452"), new Color("#ff5c69")),
  down:  Color.dynamic(new Color("#3182f6"), new Color("#4c94ff")),
  blue:  Color.dynamic(new Color("#3182f6"), new Color("#4c94ff")),
  buy:   Color.dynamic(new Color("#f04452"), new Color("#ff5c69")),
  wait:  Color.dynamic(new Color("#f59f00"), new Color("#ffb53d")),
  new:   Color.dynamic(new Color("#03b26c"), new Color("#2fd08a")),
  halt:  Color.dynamic(new Color("#6b7684"), new Color("#9e9ea7")),
};
const ST = {
  "매수":   { label: "매수", col: C.buy },
  "대기":   { label: "대기", col: C.wait },
  "신규조건": { label: "신규", col: C.new },
  "중단":   { label: "중단", col: C.halt },
};
const ORDER = ["매수", "대기", "신규조건", "중단"];
const VERD = { "통과": C.new, "주의": C.wait, "제외": C.buy, "미확인": C.muted };

// ── 데이터 (실패하면 마지막 저장본) ──
const fm = FileManager.local();
const cacheDir = fm.joinPath(fm.documentsDirectory(), "bidangil");
if (!fm.fileExists(cacheDir)) fm.createDirectory(cacheDir);
async function load(name) {
  const path = fm.joinPath(cacheDir, name);
  try {
    const r = new Request(`${BASE}/data/${name}?t=${Date.now()}`);
    r.timeoutInterval = 12;
    const j = await r.loadJSON();
    fm.writeString(path, JSON.stringify(j));
    return { data: j, stale: false };
  } catch (e) {
    if (fm.fileExists(path)) return { data: JSON.parse(fm.readString(path)), stale: true };
    return { data: null, stale: true };
  }
}

// ── 포맷 ──
const won = n => n == null ? "—" : Math.round(n).toLocaleString("ko-KR");
const pct = (n, plus = true) => n == null ? "—" : (plus && n > 0 ? "+" : "") + Number(n).toFixed(1) + "%";
const man = n => n == null ? "—" : (Math.round(n / 1000) / 10).toLocaleString("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "만";
const sgnCol = n => n > 0 ? C.up : (n < 0 ? C.down : C.muted);
function md(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return `${d.getMonth() + 1}/${d.getDate()}(${"일월화수목금토"[d.getDay()]})`;
}

// ── 그리기 도우미 ──
function txt(st, s, size, color, bold = false) {
  const t = st.addText(String(s));
  t.font = bold ? Font.boldSystemFont(size) : Font.mediumSystemFont(size);
  t.textColor = color; t.lineLimit = 1; t.minimumScaleFactor = 0.7;
  return t;
}
function header(w, d, compact) {
  const h = w.addStack(); h.centerAlignContent();
  txt(h, "비단길 주봉", compact ? 12 : 13, C.muted, true);
  h.addSpacer();
  if (d) txt(h, md(d.last_trading_day || d.scan_date) + (d.bar_final ? "" : " 미확정"), 11, d.bar_final ? C.faint : C.wait);
}
function headline(d) {
  const c = d.counts || {};
  if (c["매수"]) return { s: `매수 ${c["매수"]}종목`, col: C.buy };
  if (c["대기"]) return { s: `대기 ${c["대기"]}종목`, col: C.text };
  return { s: "이번 주 쉬어가요", col: C.text };
}
function countsRow(st, d, size) {
  const c = d.counts || {};
  ORDER.forEach((k, i) => {
    const n = c[k] || 0, box = st.addStack();
    box.layoutVertically(); box.backgroundColor = C.fill; box.cornerRadius = 10; box.setPadding(5, 0, 5, 0);
    const top = box.addStack(); top.addSpacer(); txt(top, n, size, n ? ST[k].col : C.faint, true); top.addSpacer();
    const bot = box.addStack(); bot.addSpacer(); txt(bot, ST[k].label, 10, C.muted); bot.addSpacer();
    if (i < ORDER.length - 1) st.addSpacer(5);
  });
}
function itemRow(w, it, detailed) {
  const r = w.addStack(); r.centerAlignContent();
  const dot = r.addStack(); dot.size = new Size(6, 22); dot.cornerRadius = 3;
  dot.backgroundColor = (ST[it.state] || ST["대기"]).col;
  r.addSpacer(8);
  const l = r.addStack(); l.layoutVertically();
  const t = l.addStack(); t.centerAlignContent();
  txt(t, it.name, 14, C.text, true); t.addSpacer(5);
  txt(t, (ST[it.state] || {}).label || it.state, 11, (ST[it.state] || ST["대기"]).col, true);
  if (it.check && it.check.verdict) { t.addSpacer(5); txt(t, it.check.verdict, 11, VERD[it.check.verdict] || C.muted, true); }
  if (detailed) {
    const tgt = it.target != null ? it.target : (it.state === "중단" ? null : Math.round(it.close * 1.12));
    txt(l, `목표 ${won(tgt)} · 손절 ${won(it.stop)} (${pct(it.stop_pct)})`, 11, C.muted);
  }
  r.addSpacer();
  const rr = r.addStack(); rr.layoutVertically();
  const a = rr.addStack(); a.addSpacer(); txt(a, won(it.close), 14, C.text, true);
  if (!detailed) { const b = rr.addStack(); b.addSpacer(); txt(b, `손절 ${pct(it.stop_pct)}`, 10, C.down); }
}
// ── 크기별 ──
function empty(w) {
  txt(w, "이번 주 신호 없음", 13, C.muted, true);
  txt(w, "몇 주 연속 0건도 흔해요", 11, C.faint);
}
function small(w, d) {
  header(w, d, true); w.addSpacer(6);
  const hl = headline(d); txt(w, hl.s, 18, hl.col, true);
  w.addSpacer(6);
  const items = (d.items || []).slice(0, 2);
  if (!items.length) txt(w, "신호 없음 · 정상", 11, C.muted);
  items.forEach(it => {
    const s = w.addStack(); s.centerAlignContent();
    const dot = s.addStack(); dot.size = new Size(5, 14); dot.cornerRadius = 2.5;
    dot.backgroundColor = (ST[it.state] || ST["대기"]).col;
    s.addSpacer(5); txt(s, it.name, 12, C.text, true); s.addSpacer(4);
    if (it.check) txt(s, it.check.verdict, 10, VERD[it.check.verdict] || C.muted, true);
    txt(w, `손절 ${pct(it.stop_pct)} · ${won(it.close)}`, 10, C.muted);
    w.addSpacer(3);
  });
  w.addSpacer();
}
function medium(w, d) {
  header(w, d); w.addSpacer(6);
  const row = w.addStack(); row.topAlignContent();
  const left = row.addStack(); left.layoutVertically(); left.size = new Size(118, 0);
  const hl = headline(d); txt(left, hl.s, 17, hl.col, true); left.addSpacer(8);
  const cr = left.addStack(); countsRow(cr, d, 15);
  row.addSpacer(12);
  const right = row.addStack(); right.layoutVertically();
  const items = (d.items || []).slice(0, 4);
  if (!items.length) { right.addSpacer(10); empty(right); }
  items.forEach((it, i) => { itemRow(right, it, false); if (i < items.length - 1) right.addSpacer(5); });
  w.addSpacer();
}
function large(w, d) {
  header(w, d); w.addSpacer(6);
  const hl = headline(d); txt(w, hl.s, 22, hl.col, true); w.addSpacer(10);
  const cr = w.addStack(); countsRow(cr, d, 18);
  w.addSpacer(14);
  const items = (d.items || []).slice(0, 5);
  if (!items.length) empty(w);
  items.forEach((it, i) => {
    itemRow(w, it, true);
    const rs = ((it.check || {}).reasons || []).slice(0, 2);
    rs.forEach(r => { const l = w.addStack(); l.addSpacer(14); txt(l, `${r.level} · ${r.text}${r.date ? " " + r.date : ""}`, 10, r.level === "제외" ? C.buy : C.wait); });
    if (i < items.length - 1) w.addSpacer(8);
  });
  w.addSpacer();
}

// ── 실행 ──
const fam = config.widgetFamily || "large";
const w = new ListWidget();
w.backgroundColor = C.bg;
w.setPadding(14, 14, 12, 14);
w.url = BASE;
w.refreshAfterDate = new Date(Date.now() + 3 * 3600 * 1000);   // 3시간마다 새로고침 시도

const A = await load("latest.json");
const d = A.data;
if (!d) {
  txt(w, "비단길 주봉", 13, C.muted, true); w.addSpacer(6);
  txt(w, "데이터를 못 불러왔어요", 14, C.text, true);
  txt(w, "인터넷 연결 후 다시 시도", 11, C.muted);
} else {
  if (fam === "small") small(w, d);
  else if (fam === "medium") medium(w, d);
  else large(w, d);
  if (A.stale) { w.addSpacer(2); txt(w, "오프라인 · 마지막 저장본", 9, C.faint); }
}

if (config.runsInWidget) Script.setWidget(w);
else if (fam === "small") await w.presentSmall();
else if (fam === "medium") await w.presentMedium();
else await w.presentLarge();
Script.complete();
