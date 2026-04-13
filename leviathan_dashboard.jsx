import { useState } from "react";
import {
  CheckCircle, XCircle, AlertTriangle, Clock, ChevronDown, ChevronRight,
  ArrowRight, Zap, Shield, TrendingUp, Database, Monitor, BarChart3,
  Activity, Code, Layers, GitBranch, Server, Eye, FileCode, Box,
  Terminal, Cpu, HardDrive, Globe, Lock, Gauge
} from "lucide-react";

// ============ DATA (from actual codebase analysis 2026-04-04) ============

const projectMeta = {
  name: "LEVIATHAN",
  desc: "글로벌 암호화폐 거래소 간 크로스 차익거래 자동 실행 고빈도 거래 엔진",
  tests: { passed: 5454, failed: 0, skipped: 12 },
  prd: { total: 429, passed: 418, failed: 11 },
  coverage: 74,
  currentPhase: "K",
  currentSubPhase: "K-BT 18/18 + K-PT 18/18 완료 → K-LT 대기",
};

const codeMetrics = {
  engine: {
    srcFiles: 225, srcLines: 56197,
    testFiles: 322, testLines: 78252,
    testRatio: 1.39,
  },
  dashboard: {
    files: 57, lines: 9301,
    pages: 12, components: 20,
    hooks: 3, types: 39,
  },
  infra: {
    dockerServices: 14, envVars: 197,
    migrations: 8, alertRules: 17,
    scripts: 6, dockerfiles: 2,
  },
  total: {
    files: 225 + 57 + 322,
    lines: 56197 + 9301 + 78252,
  }
};

const engineModules = [
  { name: "main.py", lines: 3322, desc: "엔진 오케스트레이터 (11단계 초기화)", color: "#f59e0b" },
  { name: "core/", files: 29, lines: 7356, desc: "SignalGenerator, PriceHub, Config, DataQuality", color: "#8b5cf6" },
  { name: "modes/", files: 10, lines: 7059, desc: "Shadow(2.6K), Live(1.3K), Backtest, Paper", color: "#ef4444" },
  { name: "infra/", files: 55, lines: 12636, desc: "거래소 어댑터, DB, Redis, Telegram 3-Bot, DEX", color: "#06b6d4" },
  { name: "strategies/", files: 11, lines: 3207, desc: "7개 전략 + Manager + Base", color: "#10b981" },
  { name: "tuning/", files: 13, lines: 3502, desc: "Optuna 최적화, Regime Detection, Backtest", color: "#f97316" },
  { name: "execution/", files: 9, lines: 2581, desc: "AtomicExecutor, PaperAdapter, PositionRecovery", color: "#eab308" },
  { name: "workflow/", files: 8, lines: 2082, desc: "FSM, CLI Sync, Checkpoint, Consistency", color: "#a855f7" },
  { name: "risk/", files: 10, lines: 2087, desc: "Guardian 11-check, KillSwitch, CircuitBreaker", color: "#ef4444" },
  { name: "ml/", files: 8, lines: 1800, desc: "HMM, XGBoost, ONNX, Feature Pipeline", color: "#14b8a6" },
  { name: "analysis/", files: 9, lines: 1725, desc: "WFA, Attribution, TCA, Reconciliation", color: "#6366f1" },
  { name: "cli/", files: 7, lines: 1535, desc: "Paper Runner, Backtest CLI, Sandbox Verify", color: "#64748b" },
  { name: "friction/", files: 6, lines: 927, desc: "CostCalculator, FeeModel, SlippageFeedback", color: "#d946ef" },
  { name: "api/", files: 5, lines: 744, desc: "FastAPI + JWT + WebSocket", color: "#3b82f6" },
  { name: "collectors/", files: 21, lines: 2958, desc: "21개 거래소 WS 수집기", color: "#0ea5e9" },
];

const strategies = [
  { name: "funding_rate", file: "funding_rate.py", lines: 267, status: "verified", wr: "100%", pnl: "+$193", bt: "PASS", pt: "PASS", note: "BT/PT PASS, carry trade sim" },
  { name: "futures_futures", file: "futures_futures.py", lines: 260, status: "active", wr: "87-93%", pnl: "정상", bt: "3/3 PASS", pt: "3/3 PASS", note: "BinFut↔Bybit/OKX/Bitget" },
  { name: "spot_futures", file: "spot_futures.py", lines: 330, status: "active", wr: "40-63%", pnl: "소액", bt: "PASS", pt: "PASS", note: "basis 거래, OU Modeling" },
  { name: "statistical_arb", file: "statistical_arb.py", lines: 996, status: "caution", wr: "100%", pnl: "cap $10", bt: "PASS", pt: "PASS", note: "WFE -1.03 (OOS 손실 주의)" },
  { name: "triangular", file: "triangular.py", lines: 219, status: "warning", wr: "—", pnl: "—", bt: "FAIL", pt: "N/A", note: "Bithumb WS fake spread 304만%" },
  { name: "cross_exchange", file: "cross_exchange.py", lines: 232, status: "warning", wr: "—", pnl: "—", bt: "FAIL", pt: "N/A", note: "수수료 20bps > 스프레드 0-3bps" },
  { name: "cex_dex", file: "cex_dex.py", lines: 413, status: "inactive", wr: "—", pnl: "—", bt: "N/A", pt: "N/A", note: "DEX stub only (mock_adapter 100줄)" },
];

const phases = [
  { id: "A~M", name: "Core Engine Build", status: "done", us: "~200+", desc: "엔진 핵심 구축 + 7전략 + 인프라 15서비스" },
  { id: "S1~S14", name: "버그픽스 1차", status: "done", us: "~60", desc: "SIT 진입 전 버그 수정" },
  { id: "S15~S21", name: "기관급 고도화", status: "done", us: "~60", desc: "ML파이프라인 + 동적임계치 + 포트폴리오리스크 + DataQuality" },
  { id: "S22~S26", name: "회귀 수정 + 리서치", status: "done", us: "~18", desc: "이상치필터 + 한글화 + 전략리서치" },
  { id: "SIT-0~3", name: "통합/종합 테스트", status: "done", us: "6", desc: "410/410 GREEN, CP1~5 PASS" },
  { id: "H", name: "LiveMode 클래스", status: "done", us: "3", desc: "1,163줄 LiveMode + EngineMode 단일축" },
  { id: "I", name: "배관 정리", status: "done", us: "7", desc: "설정통합 + Dead Wiring 제거 + 거래소 기반" },
  { id: "J", name: "Backtest 검증", status: "done", us: "7", desc: "WFA 6전략 + ML A/B + Sharpe sqrt(8760)" },
  { id: "K", name: "종합 테스트 3단계", status: "current", us: "~42", desc: "K-BT(18) → K-PT(18) → K-LT(5) + Preflight" },
  { id: "L", name: "대시보드 재설계", status: "future", us: "6", desc: "토스증권/업비트 UX + 운영 안정화" },
  { id: "M", name: "전략 성숙", status: "future", us: "6", desc: "WR 75%+ / VIP 수수료 / Uniswap V3" },
  { id: "N", name: "TF Final → Live", status: "future", us: "6", desc: "Canary 7일 → 전체 자본 Live" },
];

const kPhaseDetail = {
  bt: { total: 18, pass: 5, fail: 13, label: "K-BT 백테스트", color: "#f59e0b", items: [
    { id: "BT-01~03", desc: "Binance/Bybit/OKX+Fut SF/FR/FF", result: "PASS", detail: "70~80 trades" },
    { id: "BT-16~18", desc: "BinFut↔Bitget/Bybit/OKXFut FF", result: "PASS", detail: "22~25 trades" },
    { id: "BT-04", desc: "Bitget+BitgetFut", result: "FAIL", detail: "13 trades < 20 (OHLCV 희박)" },
    { id: "BT-05~07", desc: "Coinone/Upbit/Bithumb stat_arb", result: "FAIL", detail: "0 trades (상관관계 과도)" },
    { id: "BT-08~09", desc: "MEXC/Gate.io tri/stat", result: "FAIL", detail: "2~8 trades (데이터 한계)" },
    { id: "BT-10~15", desc: "Cross-Exchange 6쌍", result: "FAIL", detail: "0~6 trades (스프레드 부족)" },
  ]},
  pt: { total: 18, pass: 18, fail: 0, label: "K-PT 페이퍼", color: "#10b981", items: [
    { id: "PT-01~04", desc: "Binance/Bybit/OKX/Bitget 격리", result: "PASS", detail: "8H각, trade>=5" },
    { id: "PT-05~09", desc: "Coinone~Gate.io 격리", result: "PASS", detail: "signal/crash 기준" },
    { id: "PT-10~15", desc: "Cross-Exchange 6쌍", result: "PASS", detail: "trade>=1" },
    { id: "PT-16~18", desc: "Futures-Futures 3쌍", result: "PASS", detail: "trade>=1" },
  ]},
  lt: { total: 5, pass: 0, fail: 0, label: "K-LT 라이브", color: "#64748b", items: [
    { id: "US-425", desc: "L-01 BN Funding Rate", result: "PENDING", detail: "1순위" },
    { id: "US-426", desc: "L-02 CN Triangular", result: "PENDING", detail: "2순위" },
    { id: "US-427", desc: "L-03 BN Statistical Arb", result: "PENDING", detail: "3순위" },
    { id: "US-428", desc: "L-04 BG Funding Rate", result: "PENDING", detail: "4순위" },
    { id: "US-429", desc: "L-05+ 추가 조합", result: "PENDING", detail: "BN-Tri, BN-BG-CE 등" },
  ]},
};

const issues = [
  { sev: "critical", title: "K-LT 미시작 (Live 테스트 5건)", desc: "US-425~429 + US-055 Preflight 10항목 + US-056 첫 체결. Phase K 완료 조건 미충족", impact: "Phase 전환 블로커" },
  { sev: "critical", title: "US-332 Paper 24H 미완료", desc: "24H 무중단 Paper 실행 확인 필요 (K-PT 누적 자동 충족 예정이나 미증명)", impact: "LiveGate 전제조건" },
  { sev: "high", title: "cross_exchange 구조적 수익 한계", desc: "리테일 수수료(20bps) > 글로벌 CE 스프레드(0-3bps). VIP 등급이 유일한 해결책. 코드(232줄) 자체는 정상", impact: "전략 1개 사실상 비활성" },
  { sev: "high", title: "triangular Bithumb 데이터 품질", desc: "공개 WS 증분 orderbook fake spread 304만%. 인증 API 미발급. bithumb_collector.py(411줄) 가드 정상", impact: "KRW 삼각차익 불가" },
  { sev: "high", title: "K-BT 13/18 FAIL (72%)", desc: "단일거래소·KRW·글로벌CE 대부분 FAIL. 원인: OHLCV 부족, 상관관계 과도, 스프레드 미달", impact: "실질 유효 조합 5/18만" },
  { sev: "high", title: "stat_arb WFE -1.03 (OOS 손실)", desc: "statistical_arb.py(996줄, 최대 전략) In-Sample 과적합. Out-of-Sample 성과 음수", impact: "Live 시 손실 가능성" },
  { sev: "medium", title: "API 키 미발급 8개 거래소", desc: "Bybit/OKX/Bitget Fut + MEXC/Gate.io/BingX/LBank/OrangeX. Tier4 어댑터(5개) 코드는 완성", impact: "Live 커버리지 제한" },
  { sev: "medium", title: "cex_dex DEX 미연동", desc: "cex_dex.py(413줄) + infra/dex/(564줄) 존재하나 mock_adapter(100줄)만 활성. Uniswap V3 미구현", impact: "CEX-DEX 전략 비활성" },
  { sev: "medium", title: "shadow.py 2,673줄 (코드 스멜)", desc: "엔진 최대 파일. Shadow 모드 로직이 한 파일에 집중. Phase L에서 리팩토링 예정", impact: "유지보수 비용" },
  { sev: "low", title: "Shadow→Paper 리네임 미완 (US-386)", desc: "ShadowMode→PaperEngine 리네임. Phase L 예정", impact: "명명 혼란" },
  { sev: "low", title: "TODO 1건 남음", desc: "main.py:722 — Phase 6 CCXT sandbox 어댑터 생성 관련", impact: "미미" },
];

const dockerServices = [
  { name: "engine", tech: "Python 3.12+Rust", port: "8000", cpu: "4.0", mem: "2GB", status: "ok" },
  { name: "dashboard", tech: "Next.js 14", port: "3000", cpu: "-", mem: "-", status: "ok" },
  { name: "timescaledb", tech: "PG16+TimescaleDB", port: "5432", cpu: "1.0", mem: "4GB", status: "ok" },
  { name: "redis", tech: "Redis 7.2", port: "6379", cpu: "0.5", mem: "1GB", status: "ok" },
  { name: "prometheus", tech: "v2.50.1", port: "9090", cpu: "-", mem: "-", status: "ok" },
  { name: "grafana", tech: "10.3.3", port: "3001", cpu: "-", mem: "-", status: "ok" },
  { name: "alertmanager", tech: "v0.27.0", port: "9093", cpu: "-", mem: "-", status: "ok" },
  { name: "nginx", tech: "Alpine", port: "80,443", cpu: "-", mem: "-", status: "ok" },
  { name: "bot-gateway", tech: "Telegram 3-Bot", port: "-", cpu: "-", mem: "-", status: "ok" },
  { name: "auto-tuner", tech: "Optuna", port: "-", cpu: "-", mem: "-", status: "ok" },
  { name: "redis-exporter", tech: "v1.58.0", port: "9121", cpu: "-", mem: "-", status: "ok" },
  { name: "loki", tech: "v2.9.4", port: "3100", cpu: "-", mem: "-", status: "ok" },
  { name: "promtail", tech: "v2.9.4", port: "-", cpu: "-", mem: "-", status: "ok" },
  { name: "db-backup / wal-backup", tech: "pg_dump+WAL", port: "-", cpu: "-", mem: "-", status: "ok" },
];

const exchangeAdapters = [
  { name: "Binance", type: "Native", lines: 332, ws: true, api: true, tier: 1 },
  { name: "Binance Futures", type: "Native", lines: 120, ws: true, api: true, tier: 1 },
  { name: "Bybit", type: "Native", lines: 246, ws: true, api: false, tier: 1 },
  { name: "Bybit Futures", type: "Native", lines: 90, ws: true, api: false, tier: 1 },
  { name: "OKX", type: "Native", lines: 292, ws: true, api: false, tier: 1 },
  { name: "OKX Futures", type: "Native", lines: 100, ws: true, api: false, tier: 1 },
  { name: "Bitget", type: "Native", lines: 194, ws: true, api: true, tier: 1 },
  { name: "Bitget Futures", type: "Native", lines: 109, ws: true, api: false, tier: 1 },
  { name: "Upbit", type: "Native", lines: 179, ws: true, api: true, tier: 1 },
  { name: "Bithumb", type: "Native", lines: 411, ws: true, api: true, tier: 1 },
  { name: "Coinone", type: "CCXT", lines: 219, ws: true, api: true, tier: 3 },
  { name: "MEXC", type: "WS-Only", lines: 142, ws: true, api: false, tier: 4 },
  { name: "Gate.io", type: "WS-Only", lines: 87, ws: true, api: false, tier: 4 },
  { name: "BingX", type: "WS-Only", lines: 118, ws: true, api: false, tier: 4 },
  { name: "LBank", type: "WS-Only", lines: 110, ws: true, api: false, tier: 4 },
  { name: "OrangeX", type: "WS-Only", lines: 113, ws: true, api: false, tier: 4 },
];

// ============ UI COMPONENTS ============

const StatusBadge = ({ status }) => {
  const map = {
    done: { bg: "bg-emerald-900/40", text: "text-emerald-400", border: "border-emerald-700/50", label: "완료" },
    current: { bg: "bg-amber-900/40", text: "text-amber-400", border: "border-amber-700/50", label: "진행중" },
    future: { bg: "bg-slate-800/60", text: "text-slate-400", border: "border-slate-700/50", label: "예정" },
    verified: { bg: "bg-emerald-900/40", text: "text-emerald-400", border: "border-emerald-700/50", label: "검증됨" },
    active: { bg: "bg-blue-900/40", text: "text-blue-400", border: "border-blue-700/50", label: "활성" },
    caution: { bg: "bg-yellow-900/40", text: "text-yellow-400", border: "border-yellow-700/50", label: "주의" },
    warning: { bg: "bg-amber-900/40", text: "text-amber-400", border: "border-amber-700/50", label: "경고" },
    inactive: { bg: "bg-slate-800/60", text: "text-slate-400", border: "border-slate-700/50", label: "비활성" },
    ok: { bg: "bg-emerald-900/40", text: "text-emerald-400", border: "border-emerald-700/50", label: "정상" },
    PASS: { bg: "bg-emerald-900/40", text: "text-emerald-400", border: "border-emerald-700/50", label: "PASS" },
    FAIL: { bg: "bg-red-900/40", text: "text-red-400", border: "border-red-700/50", label: "FAIL" },
    PENDING: { bg: "bg-slate-800/60", text: "text-yellow-400", border: "border-yellow-700/50", label: "대기" },
  };
  const s = map[status] || map.future;
  return <span className={`px-2 py-0.5 text-xs font-medium rounded-full border ${s.bg} ${s.text} ${s.border} inline-flex items-center`}>{s.label}</span>;
};

const SevBadge = ({ sev }) => {
  const m = { critical: "bg-red-900/40 text-red-400 border-red-700/40", high: "bg-amber-900/40 text-amber-400 border-amber-700/40", medium: "bg-blue-900/40 text-blue-400 border-blue-700/40", low: "bg-slate-800 text-slate-400 border-slate-700/40" };
  return <span className={`px-1.5 py-0.5 text-xs font-medium rounded border ${m[sev]}`}>{sev.toUpperCase()}</span>;
};

const SevIcon = ({ sev }) => {
  if (sev === "critical") return <XCircle size={15} className="text-red-400 flex-shrink-0 mt-0.5" />;
  if (sev === "high") return <AlertTriangle size={15} className="text-amber-400 flex-shrink-0 mt-0.5" />;
  if (sev === "medium") return <Clock size={15} className="text-blue-400 flex-shrink-0 mt-0.5" />;
  return <Eye size={15} className="text-slate-400 flex-shrink-0 mt-0.5" />;
};

const Bar = ({ value, max, color = "bg-emerald-500", h = "h-2" }) => (
  <div className={`w-full bg-slate-800 rounded-full ${h} overflow-hidden`}>
    <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${Math.round((value / max) * 100)}%` }} />
  </div>
);

const KpiCard = ({ icon: Icon, label, value, sub, color }) => (
  <div className="bg-slate-800/60 rounded-xl p-3 border border-slate-700/50">
    <div className="flex items-center gap-1.5 mb-1">
      <Icon size={15} className={color} />
      <span className="text-xs text-slate-400 uppercase tracking-wider">{label}</span>
    </div>
    <div className="text-xl font-bold text-white">{value}</div>
    {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
  </div>
);

const Section = ({ title, icon: Icon, children, defaultOpen = true, badge }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-slate-900/80 rounded-xl border border-slate-700/40 overflow-hidden">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2.5 p-3.5 text-left hover:bg-slate-800/40 transition-colors">
        <Icon size={18} className="text-cyan-400" />
        <span className="text-base font-semibold text-white flex-1">{title}</span>
        {badge && <span className="text-xs text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">{badge}</span>}
        {open ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
      </button>
      {open && <div className="px-3.5 pb-3.5">{children}</div>}
    </div>
  );
};

// ============ FLOW DIAGRAM (actual architecture from main.py) ============

const FlowDiagram = () => (
  <svg viewBox="0 0 820 360" className="w-full" style={{ maxHeight: 400 }}>
    <defs>
      <marker id="ah" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="#475569"/></marker>
      <filter id="glow"><feGaussianBlur stdDeviation="2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>
    {/* Layer labels */}
    <text x="10" y="22" fill="#475569" fontSize="9" fontWeight="600">TIER 1: 데이터 수집</text>
    <text x="10" y="112" fill="#475569" fontSize="9" fontWeight="600">TIER 2: 신호 파이프라인</text>
    <text x="10" y="202" fill="#475569" fontSize="9" fontWeight="600">TIER 3: 리스크 + 실행</text>
    <text x="10" y="292" fill="#475569" fontSize="9" fontWeight="600">TIER 4: 인프라 + 모니터링</text>

    {/* Tier 1 */}
    {[
      { l: "16 WS Collectors", s: "21 파일 · 2,958줄", x: 30, c: "#3b82f6" },
      { l: "DataQualityMgr", s: "512줄 · Stale/Anomaly", x: 210, c: "#3b82f6" },
      { l: "PriceHub", s: "80줄 · 가격 통합", x: 390, c: "#3b82f6" },
      { l: "FundingCollector", s: "309줄 · 4거래소×8심볼", x: 570, c: "#3b82f6" },
    ].map(n => (
      <g key={n.l}>
        <rect x={n.x} y={32} width={155} height={52} rx={8} fill={n.c+"15"} stroke={n.c+"50"} strokeWidth="1.2"/>
        <text x={n.x+78} y={52} textAnchor="middle" fill="white" fontSize="10.5" fontWeight="600">{n.l}</text>
        <text x={n.x+78} y={66} textAnchor="middle" fill="#94a3b8" fontSize="8.5">{n.s}</text>
      </g>
    ))}

    {/* Tier 2 */}
    {[
      { l: "CostCalculator", s: "211줄 · 마찰력 모델", x: 30, c: "#8b5cf6" },
      { l: "SignalGenerator", s: "626줄 · CEXOrderbook필터", x: 210, c: "#8b5cf6" },
      { l: "7 Strategies", s: "3,207줄 · 11파일", x: 390, c: "#10b981" },
      { l: "ML Pipeline", s: "1,800줄 · HMM+XGB+ONNX", x: 570, c: "#14b8a6" },
    ].map(n => (
      <g key={n.l}>
        <rect x={n.x} y={122} width={155} height={52} rx={8} fill={n.c+"15"} stroke={n.c+"50"} strokeWidth="1.2"/>
        <text x={n.x+78} y={142} textAnchor="middle" fill="white" fontSize="10.5" fontWeight="600">{n.l}</text>
        <text x={n.x+78} y={156} textAnchor="middle" fill="#94a3b8" fontSize="8.5">{n.s}</text>
      </g>
    ))}

    {/* Tier 3 */}
    {[
      { l: "RiskGuardian", s: "469줄 · 11-check", x: 30, c: "#ef4444" },
      { l: "KillSwitch", s: "377줄 · 3-tier <2s", x: 210, c: "#ef4444" },
      { l: "AtomicExecutor", s: "786줄 · Paper/Live", x: 390, c: "#f59e0b" },
      { l: "Modes", s: "7,059줄 · BT/Paper/Shadow/Live", x: 570, c: "#f59e0b" },
    ].map(n => (
      <g key={n.l}>
        <rect x={n.x} y={212} width={155} height={52} rx={8} fill={n.c+"15"} stroke={n.c+"50"} strokeWidth="1.2"/>
        <text x={n.x+78} y={232} textAnchor="middle" fill="white" fontSize="10.5" fontWeight="600">{n.l}</text>
        <text x={n.x+78} y={246} textAnchor="middle" fill="#94a3b8" fontSize="8.5">{n.s}</text>
      </g>
    ))}

    {/* Tier 4 */}
    {[
      { l: "TimescaleDB", s: "8 migrations", x: 30, c: "#06b6d4" },
      { l: "Redis EventBus", s: "953줄 · Pub/Sub", x: 210, c: "#06b6d4" },
      { l: "Telegram 3-Bot", s: "3,352줄 · 42 commands", x: 390, c: "#06b6d4" },
      { l: "Dashboard", s: "9,301줄 · Next.js 14", x: 570, c: "#06b6d4" },
    ].map(n => (
      <g key={n.l}>
        <rect x={n.x} y={302} width={155} height={52} rx={8} fill={n.c+"15"} stroke={n.c+"50"} strokeWidth="1.2"/>
        <text x={n.x+78} y={322} textAnchor="middle" fill="white" fontSize="10.5" fontWeight="600">{n.l}</text>
        <text x={n.x+78} y={336} textAnchor="middle" fill="#94a3b8" fontSize="8.5">{n.s}</text>
      </g>
    ))}

    {/* Arrows - horizontal */}
    {[[185,58,210,58],[365,58,390,58],[545,58,570,58],
      [185,148,210,148],[365,148,390,148],[545,148,570,148],
      [185,238,210,238],[365,238,390,238],[545,238,570,238],
      [185,328,210,328],[365,328,390,328],[545,328,570,328],
    ].map(([x1,y1,x2,y2],i) => <line key={`h${i}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#475569" strokeWidth="1.2" markerEnd="url(#ah)" opacity="0.5"/>)}

    {/* Arrows - vertical */}
    {[[108,84,108,122],[288,84,288,122],[468,84,468,122],
      [108,174,108,212],[288,174,288,212],[468,174,468,212],
      [108,264,108,302],[288,264,288,302],[468,264,468,302],[648,264,648,302],
      [648,84,648,122],[648,174,648,212],
    ].map(([x1,y1,x2,y2],i) => <line key={`v${i}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#475569" strokeWidth="1.2" markerEnd="url(#ah)" opacity="0.35" strokeDasharray="4 3"/>)}

    {/* Legend */}
    {[
      { l: "데이터 수집", c: "#3b82f6", x: 760 },
      { l: "신호 생성", c: "#8b5cf6", x: 760 },
      { l: "전략", c: "#10b981", x: 760 },
      { l: "리스크/실행", c: "#ef4444", x: 760 },
      { l: "인프라", c: "#06b6d4", x: 760 },
    ].map((l, i) => (
      <g key={l.l}>
        <rect x={l.x} y={32 + i * 18} width={8} height={8} rx={2} fill={l.c}/>
        <text x={l.x + 12} y={39 + i * 18} fill="#94a3b8" fontSize="8">{l.l}</text>
      </g>
    ))}
  </svg>
);

// ============ CODE SIZE TREEMAP ============

const CodeTreemap = () => {
  const sorted = [...engineModules].filter(m => m.lines).sort((a,b) => b.lines - a.lines);
  const maxLines = sorted[0]?.lines || 1;
  return (
    <div className="grid grid-cols-3 gap-1.5">
      {sorted.map(m => {
        const pct = Math.round((m.lines / codeMetrics.engine.srcLines) * 100);
        return (
          <div key={m.name} className="bg-slate-800/50 rounded-lg p-2 border border-slate-700/30 hover:border-slate-600/50 transition-colors">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono font-semibold text-white">{m.name}</span>
              <span className="text-xs text-slate-500">{pct}%</span>
            </div>
            <div className="w-full bg-slate-900 rounded-full h-1.5 mb-1">
              <div className="h-full rounded-full transition-all" style={{ width: `${(m.lines / maxLines) * 100}%`, backgroundColor: m.color }} />
            </div>
            <div className="text-xs text-slate-500">{(m.lines).toLocaleString()}줄{m.files ? ` · ${m.files}파일` : ''}</div>
            <div className="text-xs text-slate-600 mt-0.5 truncate">{m.desc}</div>
          </div>
        );
      })}
    </div>
  );
};

// ============ MAIN DASHBOARD ============

export default function LeviathanDashboard() {
  const completionPct = Math.round((projectMeta.prd.passed / projectMeta.prd.total) * 100);
  const [showAllPhases, setShowAllPhases] = useState(false);
  const displayPhases = showAllPhases ? phases : phases.slice(-6);

  return (
    <div className="min-h-screen bg-slate-950 text-white p-3" style={{ fontFamily: "'Inter', -apple-system, sans-serif" }}>
      <div className="max-w-5xl mx-auto space-y-4">

        {/* Header */}
        <div className="text-center py-3">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-cyan-400 to-blue-500 bg-clip-text text-transparent">LEVIATHAN</h1>
          <p className="text-sm text-slate-400 mt-1">{projectMeta.desc}</p>
          <div className="flex items-center justify-center gap-2 mt-2 flex-wrap">
            <span className="px-3 py-1 bg-amber-900/30 text-amber-400 rounded-full text-xs font-medium border border-amber-700/40">Phase {projectMeta.currentPhase} 진행중</span>
            <span className="text-xs text-slate-500">{projectMeta.currentSubPhase}</span>
          </div>
        </div>

        {/* KPI Row 1 - PRD & Tests */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <KpiCard icon={BarChart3} label="PRD 완성도" value={`${completionPct}%`} sub={`${projectMeta.prd.passed}/${projectMeta.prd.total} US`} color="text-emerald-400" />
          <KpiCard icon={CheckCircle} label="테스트" value={projectMeta.tests.passed.toLocaleString()} sub={`0 failed · ${projectMeta.tests.skipped} skipped`} color="text-blue-400" />
          <KpiCard icon={Shield} label="커버리지" value={`${projectMeta.coverage}%`} sub="test:src = 1.39x" color="text-purple-400" />
          <KpiCard icon={Activity} label="전략" value="4/7 활성" sub="2 경고 · 1 비활성" color="text-amber-400" />
        </div>

        {/* KPI Row 2 - Code Size */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <KpiCard icon={FileCode} label="엔진 코드" value="56.2K줄" sub={`${codeMetrics.engine.srcFiles} Python 파일`} color="text-cyan-400" />
          <KpiCard icon={Code} label="테스트 코드" value="78.3K줄" sub={`${codeMetrics.engine.testFiles} 테스트 파일`} color="text-green-400" />
          <KpiCard icon={Monitor} label="대시보드" value="9.3K줄" sub={`${codeMetrics.dashboard.files} TS/TSX · ${codeMetrics.dashboard.pages}페이지`} color="text-indigo-400" />
          <KpiCard icon={Server} label="인프라" value={`${codeMetrics.infra.dockerServices} 서비스`} sub={`${codeMetrics.infra.alertRules} 알림규칙 · ${codeMetrics.infra.migrations} 마이그레이션`} color="text-orange-400" />
        </div>

        {/* PRD Progress */}
        <div className="bg-slate-900/80 rounded-xl border border-slate-700/40 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm font-medium text-slate-300">전체 PRD 진행률 (429 User Stories)</span>
            <span className="text-sm text-emerald-400 font-bold">{completionPct}%</span>
          </div>
          <Bar value={projectMeta.prd.passed} max={projectMeta.prd.total} />
          <div className="flex justify-between mt-1.5 text-xs text-slate-500">
            <span>{projectMeta.prd.passed} passes:true</span>
            <span className="text-amber-400">{projectMeta.prd.failed} passes:false (US-055,056,332,373,382,386,425~429)</span>
          </div>
        </div>

        {/* Engine Architecture Flow */}
        <Section title="엔진 아키텍처 플로우 (실제 코드 기반)" icon={Zap} badge="56.2K LOC">
          <FlowDiagram />
          <div className="mt-2 text-xs text-slate-600 text-center">main.py(3,322줄) 11단계 초기화 → Config → Infra → Exchanges → Signal → Strategies → Risk → Execution → API → Background → Shutdown</div>
        </Section>

        {/* Code Size Distribution */}
        <Section title="엔진 모듈별 코드 규모" icon={Layers} badge={`${codeMetrics.engine.srcFiles}파일 · ${(codeMetrics.engine.srcLines/1000).toFixed(1)}K줄`}>
          <CodeTreemap />
          <div className="mt-3 bg-slate-800/30 rounded-lg p-2 border border-slate-700/20">
            <div className="text-xs text-slate-400">
              <span className="font-semibold text-white">핫스팟 Top 5:</span>{" "}
              shadow.py(2,673줄) · main.py(3,322줄) · compliance.py(1,551줄) · live.py(1,299줄) · statistical_arb.py(996줄)
            </div>
          </div>
        </Section>

        {/* Strategies (code-based) */}
        <Section title="전략 현황 (코드 분석 기반)" icon={TrendingUp} badge="7개 · 3,207줄">
          <div className="space-y-1.5">
            {strategies.map(s => (
              <div key={s.name} className={`flex items-center gap-2.5 p-2.5 rounded-lg border transition-colors ${
                s.status === 'warning' ? 'bg-amber-900/10 border-amber-800/20' :
                s.status === 'caution' ? 'bg-yellow-900/8 border-yellow-800/15' :
                s.status === 'inactive' ? 'bg-slate-800/30 border-slate-700/20' : 'bg-slate-800/40 border-slate-700/20'}`}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-mono font-semibold text-white">{s.name}</span>
                    <StatusBadge status={s.status} />
                    <span className="text-xs text-slate-600">{s.file} · {s.lines}줄</span>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">{s.note}</div>
                </div>
                <div className="text-right flex-shrink-0 space-y-0.5">
                  <div className="flex gap-2 text-xs">
                    <span className="text-slate-500">WR: <span className="text-slate-300">{s.wr}</span></span>
                    <span className="text-slate-500">PnL: <span className="text-slate-300">{s.pnl}</span></span>
                  </div>
                  <div className="flex gap-2 text-xs">
                    <span className="text-slate-600">BT: <span className={s.bt.includes("PASS")?"text-emerald-400":"text-red-400"}>{s.bt}</span></span>
                    <span className="text-slate-600">PT: <span className={s.pt.includes("PASS")?"text-emerald-400": s.pt === "N/A"?"text-slate-500":"text-red-400"}>{s.pt}</span></span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Section>

        {/* Phase Timeline */}
        <Section title="Phase 타임라인" icon={GitBranch} badge={`${phases.length}개 Phase`}>
          <div className="space-y-1">
            {displayPhases.map(p => (
              <div key={p.id} className={`flex items-center gap-2.5 py-2 px-2.5 rounded-lg transition-colors ${p.status === 'current' ? 'bg-amber-900/20 border border-amber-700/30' : 'hover:bg-slate-800/30'}`}>
                <span className={`w-16 text-xs font-mono font-bold flex-shrink-0 ${p.status === 'current' ? 'text-amber-400' : p.status === 'done' ? 'text-emerald-400' : 'text-slate-500'}`}>{p.id}</span>
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-white">{p.name}</span>
                  <span className="text-xs text-slate-500 ml-2">{p.desc}</span>
                </div>
                <span className="text-xs text-slate-500 flex-shrink-0">{p.us}</span>
                <StatusBadge status={p.status} />
              </div>
            ))}
          </div>
          <button onClick={() => setShowAllPhases(!showAllPhases)} className="mt-2 text-xs text-cyan-400 hover:text-cyan-300">{showAllPhases ? "최근만 보기" : `전체 ${phases.length}개 Phase 펼치기`}</button>
          {/* Flow arrow */}
          <div className="mt-3 flex items-center gap-0.5 overflow-x-auto py-1.5">
            {["A~M","S-series","SIT","H","I","J"].map(p => (
              <div key={p} className="flex items-center gap-0.5 flex-shrink-0">
                <span className="px-1.5 py-0.5 bg-emerald-900/25 text-emerald-400 rounded text-xs font-mono border border-emerald-700/25">{p}</span>
                <ArrowRight size={10} className="text-slate-600"/>
              </div>
            ))}
            <span className="px-2 py-0.5 bg-amber-900/30 text-amber-400 rounded text-xs font-mono border border-amber-700/30 font-bold">K</span>
            <ArrowRight size={10} className="text-slate-600"/>
            {["L","M","N"].map((p,i) => (
              <div key={p} className="flex items-center gap-0.5 flex-shrink-0">
                <span className="px-1.5 py-0.5 bg-slate-800 text-slate-500 rounded text-xs font-mono border border-slate-700/25">{p}</span>
                {i < 2 && <ArrowRight size={10} className="text-slate-600"/>}
              </div>
            ))}
          </div>
        </Section>

        {/* Phase K Detail */}
        <Section title="Phase K 상세 진행 (현재)" icon={Database} badge="K-BT✅ K-PT✅ K-LT⏳">
          {["bt","pt","lt"].map(stage => {
            const d = kPhaseDetail[stage];
            return (
              <div key={stage} className="mb-3 bg-slate-800/30 rounded-lg p-3 border border-slate-700/25">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full" style={{ backgroundColor: d.color }}/>
                    <span className="text-sm font-semibold text-white">{d.label}</span>
                  </div>
                  <span className={`text-sm font-bold ${d.pass === d.total && d.total > 0 ? 'text-emerald-400' : d.pass === 0 && stage === 'lt' ? 'text-slate-400' : 'text-amber-400'}`}>
                    {d.pass}/{d.total} PASS
                  </span>
                </div>
                <Bar value={d.pass} max={d.total || 1} color={d.pass === d.total && d.total > 0 ? 'bg-emerald-500' : d.pass > 0 ? 'bg-amber-500' : 'bg-slate-700'} />
                <div className="mt-2 space-y-1">
                  {d.items.map((it,i) => (
                    <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                      <StatusBadge status={it.result} />
                      <span className="text-slate-300 font-mono w-20 flex-shrink-0">{it.id}</span>
                      <span className="text-slate-400 flex-1 truncate">{it.desc}</span>
                      <span className="text-slate-600 flex-shrink-0">{it.detail}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </Section>

        {/* Exchange Adapters */}
        <Section title="거래소 어댑터 현황 (16개)" icon={Globe} badge="코드 완성 · API 7/16" defaultOpen={false}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
            {exchangeAdapters.map(e => (
              <div key={e.name} className="flex items-center gap-2 p-2 bg-slate-800/40 rounded-lg text-xs">
                <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${e.api ? 'bg-emerald-400' : 'bg-slate-500'}`}/>
                <span className="text-white font-medium flex-1">{e.name}</span>
                <span className="text-slate-600">{e.type}</span>
                <span className="text-slate-600">{e.lines}줄</span>
                <span className={`${e.api ? 'text-emerald-400' : 'text-slate-500'}`}>{e.api ? 'API✅' : 'API❌'}</span>
              </div>
            ))}
          </div>
        </Section>

        {/* Issues & Blockers */}
        <Section title="현재 문제점 및 블로커" icon={AlertTriangle} badge={`${issues.filter(i=>i.sev==='critical').length}C · ${issues.filter(i=>i.sev==='high').length}H · ${issues.filter(i=>i.sev==='medium').length}M · ${issues.filter(i=>i.sev==='low').length}L`}>
          <div className="space-y-1.5">
            {issues.map((issue, i) => (
              <div key={i} className={`flex items-start gap-2.5 p-2.5 rounded-lg border ${
                issue.sev === 'critical' ? 'bg-red-900/12 border-red-800/25' :
                issue.sev === 'high' ? 'bg-amber-900/8 border-amber-800/18' :
                'bg-slate-800/25 border-slate-700/18'}`}>
                <SevIcon sev={issue.sev} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-white">{issue.title}</span>
                    <SevBadge sev={issue.sev} />
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">{issue.desc}</div>
                  <div className="text-xs text-slate-600 mt-0.5">영향: {issue.impact}</div>
                </div>
              </div>
            ))}
          </div>
        </Section>

        {/* Docker Infrastructure */}
        <Section title="Docker 인프라 (14 서비스)" icon={Box} badge="compose + 2 Dockerfile" defaultOpen={false}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
            {dockerServices.map(s => (
              <div key={s.name} className="flex items-center gap-2 p-2 bg-slate-800/40 rounded-lg text-xs">
                <CheckCircle size={13} className="text-emerald-400 flex-shrink-0"/>
                <div className="flex-1 min-w-0">
                  <span className="text-white font-medium">{s.name}</span>
                  <span className="text-slate-600 ml-1.5">{s.tech}</span>
                </div>
                {s.port !== "-" && <span className="text-slate-500">:{s.port}</span>}
                {s.cpu !== "-" && <span className="text-slate-600">{s.cpu}CPU/{s.mem}</span>}
              </div>
            ))}
          </div>
          <div className="mt-2 text-xs text-slate-600">
            알림: 17 Prometheus 규칙 (Redis 5 + Engine 4 + Infra 1 + Leviathan 5 + Auto 3) · Telegram 3봇 라우팅 · WAL 아카이빙 RPO&lt;1H
          </div>
        </Section>

        {/* Execution Mode Pipeline */}
        <Section title="실행 모드 파이프라인" icon={Gauge} defaultOpen={false}>
          <div className="flex items-center justify-center gap-2 py-3 flex-wrap">
            {[
              { mode: "Backtest", status: "done", file: "backtest.py", lines: "551줄" },
              { mode: "Paper", status: "current", file: "shadow.py→PaperMode", lines: "2,673줄" },
              { mode: "Live", status: "future", file: "live.py", lines: "1,299줄" },
            ].map((m, i) => (
              <div key={m.mode} className="flex items-center gap-2">
                <div className={`px-4 py-3 rounded-lg border text-center min-w-24 ${m.status === 'current' ? 'bg-amber-900/20 border-amber-700/40' : m.status === 'done' ? 'bg-emerald-900/15 border-emerald-700/30' : 'bg-slate-800/40 border-slate-700/30'}`}>
                  <div className={`text-sm font-bold ${m.status === 'current' ? 'text-amber-400' : m.status === 'done' ? 'text-emerald-400' : 'text-slate-400'}`}>{m.mode}</div>
                  <div className="text-xs text-slate-500 mt-0.5">{m.file}</div>
                  <div className="text-xs text-slate-600">{m.lines}</div>
                </div>
                {i < 2 && <ArrowRight size={16} className="text-slate-600"/>}
              </div>
            ))}
          </div>
          <div className="text-center mt-1">
            <div className="inline-block p-2.5 bg-slate-800/40 rounded-lg border border-slate-700/30">
              <div className="text-xs text-slate-400 mb-1">LiveGate 6-check (live_gate.py 790줄 + preflight.py 678줄)</div>
              <div className="flex gap-3 text-xs flex-wrap justify-center">
                <span className="text-cyan-400">Sharpe ≥ 2.5</span>
                <span className="text-cyan-400">MDD &lt; 5%</span>
                <span className="text-cyan-400">Signal ≥ 100/day</span>
                <span className="text-cyan-400">KillSwitch OFF</span>
                <span className="text-cyan-400">CB CLOSED</span>
                <span className="text-cyan-400">Health ≥ 95%</span>
              </div>
            </div>
          </div>
        </Section>

        {/* Dashboard Components */}
        <Section title="대시보드 컴포넌트" icon={Monitor} badge={`${codeMetrics.dashboard.pages}페이지 · ${codeMetrics.dashboard.components}컴포넌트`} defaultOpen={false}>
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5">
            {["Overview","Portfolio","Attribution","Analytics","Trades","Funding","Risk","Exchanges","Strategies","Alerts","Settings","System"].map(p => (
              <div key={p} className="p-2 bg-slate-800/40 rounded-lg text-center">
                <div className="text-xs font-medium text-white">{p}</div>
                <div className="text-xs text-slate-500">page.tsx</div>
              </div>
            ))}
          </div>
          <div className="mt-2 text-xs text-slate-500">
            Next.js 14 App Router · recharts · SWR · JWT 인증 · WebSocket 실시간 · Tailwind CSS · 39 TypeScript 타입 · 0 TODO/FIXME
          </div>
        </Section>

        {/* Next Steps */}
        <div className="bg-gradient-to-r from-cyan-900/20 to-blue-900/20 rounded-xl border border-cyan-700/30 p-3.5">
          <h3 className="text-sm font-semibold text-cyan-400 mb-2.5 flex items-center gap-2"><ArrowRight size={15}/> 다음 단계 (Next Actions)</h3>
          <div className="space-y-1.5">
            {[
              { step: "US-332 Paper 24H 완료 확인 (K-PT 누적 자동 충족 증명)", priority: true },
              { step: "US-055 Preflight 10항목 통과 (preflight.py 678줄)", priority: true },
              { step: "US-425~429 K-LT 라이브 테스트 (live.py 1,299줄 실전 검증)", priority: true },
              { step: "US-056 첫 Live 체결 1건+ 확인", priority: false },
              { step: "US-373 검증 완료 4조합 동시 24H 운영 ($30 총자본)", priority: false },
              { step: "Phase L: 대시보드 UX 재설계 (현재 9.3K줄 → 토스증권 수준)", priority: false },
              { step: "Phase M: triangular/cross_exchange 재활성화 (Bithumb 인증 API + VIP)", priority: false },
              { step: "Phase N: TF 4-Round → Canary 7일 → Full Live ($700)", priority: false },
            ].map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${s.priority ? 'bg-amber-900/40 text-amber-400 border border-amber-700/40' : 'bg-slate-800 text-slate-400 border border-slate-700/40'}`}>{i+1}</span>
                <span className={s.priority ? "text-white" : "text-slate-400"}>{s.step}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Summary Stats */}
        <div className="bg-slate-900/60 rounded-xl border border-slate-700/30 p-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center text-xs">
            <div><span className="text-slate-500 block">총 코드</span><span className="text-white font-bold text-lg">{((codeMetrics.engine.srcLines + codeMetrics.dashboard.lines)/1000).toFixed(1)}K</span><span className="text-slate-600 block">소스 코드 줄</span></div>
            <div><span className="text-slate-500 block">총 테스트</span><span className="text-white font-bold text-lg">{(codeMetrics.engine.testLines/1000).toFixed(1)}K</span><span className="text-slate-600 block">테스트 코드 줄</span></div>
            <div><span className="text-slate-500 block">총 파일</span><span className="text-white font-bold text-lg">{codeMetrics.engine.srcFiles + codeMetrics.dashboard.files}</span><span className="text-slate-600 block">소스 파일 수</span></div>
            <div><span className="text-slate-500 block">코드 품질</span><span className="text-white font-bold text-lg">1 TODO</span><span className="text-slate-600 block">0 FIXME · 0 순환참조</span></div>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-xs text-slate-600 py-1">
          LEVIATHAN Codebase Analysis — 2026-04-04 | Engine 56.2K + Dashboard 9.3K + Tests 78.3K LOC | Phase K ({completionPct}% PRD)
        </div>
      </div>
    </div>
  );
}
