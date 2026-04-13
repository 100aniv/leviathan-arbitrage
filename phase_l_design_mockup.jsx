import { useState, useEffect } from "react";

// Phase L 대시보드 리디자인 목업
// 현재 구현: JetBrains Mono 터미널 스타일 + DESIGN-kraken.md/DESIGN-linear.md 참조
// 목표: 토스증권/업비트 수준 UX + 기존 터미널 미학 유지

// ============ MOCK DATA ============

const mockEquity = Array.from({ length: 48 }, (_, i) => {
  const base = 10000 + i * 15 + Math.sin(i * 0.3) * 80 + Math.random() * 40;
  return { t: i, v: base, pnl: (base - 10000).toFixed(2) };
});

const mockStrategies = [
  { id: "funding_rate", name: "Funding Rate", pnl: 193.42, wr: 100, trades: 47, status: "active", alloc: 30 },
  { id: "futures_futures", name: "Futures-Futures", pnl: 87.15, wr: 91, trades: 156, status: "active", alloc: 25 },
  { id: "spot_futures", name: "Spot-Futures", pnl: 12.30, wr: 52, trades: 83, status: "active", alloc: 20 },
  { id: "statistical_arb", name: "Statistical Arb", pnl: -3.20, wr: 48, trades: 210, status: "active", alloc: 15 },
  { id: "triangular", name: "Triangular", pnl: 0, wr: 0, trades: 0, status: "paused", alloc: 10 },
  { id: "cross_exchange", name: "Cross Exchange", pnl: 0, wr: 0, trades: 0, status: "paused", alloc: 0 },
  { id: "cex_dex", name: "CEX-DEX", pnl: 0, wr: 0, trades: 0, status: "disabled", alloc: 0 },
];

const mockExchanges = [
  { name: "Binance", status: "connected", latency: 12, balance: 5243.50, health: 99 },
  { name: "Binance Fut", status: "connected", latency: 15, balance: 3120.00, health: 98 },
  { name: "Upbit", status: "connected", latency: 45, balance: 1847000, health: 95, currency: "KRW" },
  { name: "Bithumb", status: "connected", latency: 62, balance: 920000, health: 87, currency: "KRW" },
  { name: "Coinone", status: "connected", latency: 38, balance: 540000, health: 96, currency: "KRW" },
  { name: "Bitget", status: "connected", latency: 28, balance: 2150.00, health: 97 },
  { name: "Bybit", status: "idle", latency: 0, balance: 0, health: 0 },
  { name: "OKX", status: "idle", latency: 0, balance: 0, health: 0 },
];

const mockTrades = [
  { time: "14:23:05", strategy: "FR", pair: "BTC/USDT", side: "LONG", exchange: "Binance Fut", pnl: 2.15, status: "filled" },
  { time: "14:22:41", strategy: "FF", pair: "ETH/USDT", side: "SHORT", exchange: "Bybit Fut", pnl: -0.42, status: "filled" },
  { time: "14:21:58", strategy: "SF", pair: "BTC/USDT", side: "LONG", exchange: "Binance", pnl: 0.87, status: "filled" },
  { time: "14:21:12", strategy: "SA", pair: "SOL/USDT", side: "SHORT", exchange: "Binance", pnl: 1.23, status: "filled" },
  { time: "14:20:33", strategy: "FR", pair: "ETH/USDT", side: "LONG", exchange: "Binance Fut", pnl: 3.41, status: "filled" },
  { time: "14:19:47", strategy: "FF", pair: "BTC/USDT", side: "LONG", exchange: "OKX Fut", pnl: 0.56, status: "filled" },
];

const mockAlerts = [
  { time: "14:23", type: "info", msg: "Funding Rate 시그널: BTC +0.012% (Binance→OKX)" },
  { time: "14:20", type: "warn", msg: "Bithumb BTC orderbook stale 3.2s — REST 재동기화" },
  { time: "14:15", type: "info", msg: "OU Half-life ETH basis: 2.3H (진입 가능)" },
  { time: "14:10", type: "success", msg: "일일 PnL 목표 달성: +$289.67 (Target: $200)" },
];

// ============ DESIGN TOKENS ============
// 기존 터미널 팔레트 유지 + 토스증권 스타일 카드/레이아웃 반영

const T = {
  bg: "#0a0c0f",
  surface: "#111419",
  surfaceHover: "#161a21",
  border: "#1e2329",
  borderLight: "#2a303a",
  text: "#c9d1d9",
  subtle: "#6e7681",
  muted: "#484f58",
  profit: "#00ff88",
  profitDim: "#00ff8830",
  loss: "#ff4d4d",
  lossDim: "#ff4d4d30",
  accent: "#3b82f6",
  accentDim: "#3b82f620",
  warn: "#f59e0b",
  warnDim: "#f59e0b20",
};

// ============ REUSABLE COMPONENTS ============

const Mono = ({ children, style, className = "" }) => (
  <span style={{ fontFamily: "'JetBrains Mono', 'SF Mono', 'Fira Code', monospace", ...style }} className={className}>{children}</span>
);

const PnlText = ({ value, size = 14 }) => {
  const color = value > 0 ? T.profit : value < 0 ? T.loss : T.subtle;
  const prefix = value > 0 ? "+" : "";
  return <Mono style={{ color, fontSize: size, fontWeight: 600 }}>{prefix}${Math.abs(value).toFixed(2)}</Mono>;
};

const StatusDot = ({ status }) => {
  const colors = { connected: T.profit, active: T.profit, idle: T.muted, paused: T.warn, disabled: T.muted, filled: T.profit };
  const pulse = status === "connected" || status === "active";
  return (
    <span style={{
      width: 6, height: 6, borderRadius: "50%", display: "inline-block",
      backgroundColor: colors[status] || T.muted,
      boxShadow: pulse ? `0 0 6px ${colors[status]}60` : "none",
      animation: pulse ? "pulse 2s ease-in-out infinite" : "none",
    }} />
  );
};

const Card = ({ children, style, onClick, hover = false }) => (
  <div
    onClick={onClick}
    style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10,
      padding: 16, transition: "all 0.15s ease",
      cursor: onClick ? "pointer" : "default",
      ...style,
    }}
    onMouseEnter={e => { if (hover) { e.currentTarget.style.background = T.surfaceHover; e.currentTarget.style.borderColor = T.borderLight; } }}
    onMouseLeave={e => { if (hover) { e.currentTarget.style.background = T.surface; e.currentTarget.style.borderColor = T.border; } }}
  >
    {children}
  </div>
);

const Label = ({ children }) => (
  <Mono style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.5, fontWeight: 600 }}>{children}</Mono>
);

const BigNum = ({ children, color = T.text }) => (
  <Mono style={{ fontSize: 26, fontWeight: 700, color, lineHeight: 1.1 }}>{children}</Mono>
);

const MiniBar = ({ value, max = 100, color = T.accent, h = 3 }) => (
  <div style={{ width: "100%", height: h, background: T.border, borderRadius: h }}>
    <div style={{ width: `${Math.min((value / max) * 100, 100)}%`, height: "100%", background: color, borderRadius: h, transition: "width 0.5s ease" }} />
  </div>
);

// ============ TOP BAR (Mission Control Strip) ============

const TopBar = ({ mode, setMode }) => (
  <div style={{
    display: "flex", alignItems: "center", gap: 12, padding: "8px 20px",
    background: T.surface, borderBottom: `1px solid ${T.border}`,
    position: "sticky", top: 0, zIndex: 50,
  }}>
    {/* Logo */}
    <Mono style={{ fontSize: 13, fontWeight: 800, color: T.accent, letterSpacing: 2 }}>LEVIATHAN</Mono>

    <div style={{ width: 1, height: 20, background: T.border }} />

    {/* Live indicator */}
    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
      <StatusDot status="connected" />
      <Mono style={{ fontSize: 10, color: T.subtle }}>WS 6/8</Mono>
    </div>

    <div style={{ flex: 1 }} />

    {/* KPIs inline */}
    {[
      { label: "EQUITY", value: "$10,289", color: T.text },
      { label: "DAY P&L", value: "+$289.67", color: T.profit },
      { label: "WIN%", value: "73%", color: T.profit },
      { label: "STRATEGIES", value: "4/7", color: T.warn },
    ].map(k => (
      <div key={k.label} style={{ textAlign: "right", marginLeft: 16 }}>
        <Mono style={{ fontSize: 8, color: T.muted, textTransform: "uppercase", letterSpacing: 1, display: "block" }}>{k.label}</Mono>
        <Mono style={{ fontSize: 12, fontWeight: 700, color: k.color }}>{k.value}</Mono>
      </div>
    ))}

    <div style={{ width: 1, height: 20, background: T.border, margin: "0 4px" }} />

    {/* Mode switcher */}
    <div style={{ display: "flex", gap: 2, background: T.bg, borderRadius: 6, padding: 2 }}>
      {["PAPER", "LIVE"].map(m => (
        <button
          key={m}
          onClick={() => m === "LIVE" ? null : setMode(m)}
          style={{
            padding: "4px 10px", borderRadius: 4, border: "none", cursor: m === "LIVE" ? "not-allowed" : "pointer",
            background: mode === m ? (m === "LIVE" ? T.loss : T.accent) : "transparent",
            color: mode === m ? "#fff" : T.subtle,
            fontSize: 9, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700,
            opacity: m === "LIVE" ? 0.4 : 1,
          }}
        >
          {m}
        </button>
      ))}
    </div>

    {/* Kill Switch */}
    <button style={{
      padding: "5px 12px", borderRadius: 6, border: `1px solid ${T.loss}40`,
      background: T.lossDim, color: T.loss,
      fontSize: 9, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700,
      cursor: "pointer", letterSpacing: 1,
    }}>
      KILL
    </button>
  </div>
);

// ============ SIDEBAR (토스증권 스타일 — 아이콘+텍스트 미니멀) ============

const SidebarItem = ({ label, active, icon, count }) => (
  <div style={{
    display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
    borderRadius: 8, cursor: "pointer", transition: "all 0.12s",
    background: active ? T.accentDim : "transparent",
    borderLeft: active ? `2px solid ${T.accent}` : "2px solid transparent",
  }}>
    <Mono style={{ fontSize: 14, opacity: 0.6 }}>{icon}</Mono>
    <Mono style={{ fontSize: 11, color: active ? T.text : T.subtle, fontWeight: active ? 600 : 400, flex: 1 }}>{label}</Mono>
    {count > 0 && <Mono style={{ fontSize: 9, color: T.warn, background: T.warnDim, padding: "1px 5px", borderRadius: 8 }}>{count}</Mono>}
  </div>
);

const Sidebar = ({ page, setPage }) => (
  <div style={{
    width: 200, background: T.surface, borderRight: `1px solid ${T.border}`,
    padding: "16px 8px", display: "flex", flexDirection: "column", gap: 2,
    flexShrink: 0, height: "100%",
  }}>
    <Label>모니터</Label>
    <SidebarItem icon="◉" label="Overview" active={page === "overview"} />
    <SidebarItem icon="◎" label="포트폴리오" active={page === "portfolio"} />
    <SidebarItem icon="⊞" label="히트맵" active={page === "heatmap"} />

    <div style={{ height: 12 }} />
    <Label>분석</Label>
    <SidebarItem icon="⊿" label="전략" active={page === "strategies"} />
    <SidebarItem icon="⊡" label="거래 내역" active={page === "trades"} />
    <SidebarItem icon="◈" label="펀딩레이트" active={page === "funding"} />
    <SidebarItem icon="⊕" label="Attribution" active={page === "attribution"} />

    <div style={{ height: 12 }} />
    <Label>관리</Label>
    <SidebarItem icon="⊛" label="리스크" active={page === "risk"} count={0} />
    <SidebarItem icon="⊙" label="거래소" active={page === "exchanges"} />
    <SidebarItem icon="⊘" label="설정" active={page === "settings"} />
    <SidebarItem icon="⊗" label="시스템" active={page === "system"} />

    <div style={{ flex: 1 }} />
    <div style={{ padding: "8px 14px", borderTop: `1px solid ${T.border}`, marginTop: 8 }}>
      <Mono style={{ fontSize: 8, color: T.muted }}>ENGINE v2.4.0</Mono>
      <br />
      <Mono style={{ fontSize: 8, color: T.muted }}>PHASE K · 5,454 TESTS</Mono>
    </div>
  </div>
);

// ============ EQUITY CURVE (SVG — 토스증권 스타일) ============

const EquityCurve = () => {
  const data = mockEquity;
  const w = 560, h = 140, pad = { t: 10, b: 25, l: 50, r: 10 };
  const minV = Math.min(...data.map(d => d.v)) - 20;
  const maxV = Math.max(...data.map(d => d.v)) + 20;
  const xScale = (i) => pad.l + (i / (data.length - 1)) * (w - pad.l - pad.r);
  const yScale = (v) => pad.t + (1 - (v - minV) / (maxV - minV)) * (h - pad.t - pad.b);

  const pathD = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(d.v).toFixed(1)}`).join(' ');
  const areaD = pathD + ` L${xScale(data.length - 1)},${h - pad.b} L${pad.l},${h - pad.b} Z`;

  const last = data[data.length - 1];
  const isUp = last.v > data[0].v;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: h }}>
      <defs>
        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={isUp ? T.profit : T.loss} stopOpacity="0.15" />
          <stop offset="100%" stopColor={isUp ? T.profit : T.loss} stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* Grid lines */}
      {[0.25, 0.5, 0.75].map(pct => {
        const y = pad.t + pct * (h - pad.t - pad.b);
        const val = maxV - pct * (maxV - minV);
        return (
          <g key={pct}>
            <line x1={pad.l} y1={y} x2={w - pad.r} y2={y} stroke={T.border} strokeWidth="0.5" />
            <text x={pad.l - 4} y={y + 3} textAnchor="end" fill={T.muted} fontSize="8" fontFamily="JetBrains Mono">${Math.round(val / 1000)}K</text>
          </g>
        );
      })}
      {/* Area */}
      <path d={areaD} fill="url(#eqGrad)" />
      {/* Line */}
      <path d={pathD} fill="none" stroke={isUp ? T.profit : T.loss} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      {/* Current point */}
      <circle cx={xScale(data.length - 1)} cy={yScale(last.v)} r="3" fill={isUp ? T.profit : T.loss} />
      <circle cx={xScale(data.length - 1)} cy={yScale(last.v)} r="6" fill={isUp ? T.profit : T.loss} opacity="0.2" />
    </svg>
  );
};

// ============ HEATMAP MINI (거래소×전략 스프레드) ============

const HeatmapMini = () => {
  const exchanges = ["BN", "BNF", "UP", "BH", "CO", "BG"];
  const symbols = ["BTC", "ETH", "SOL", "XRP", "DOGE"];
  return (
    <div style={{ overflowX: "auto" }}>
      <div style={{ display: "grid", gridTemplateColumns: `40px repeat(${symbols.length}, 1fr)`, gap: 2 }}>
        <div />
        {symbols.map(s => <Mono key={s} style={{ fontSize: 8, color: T.muted, textAlign: "center" }}>{s}</Mono>)}
        {exchanges.map(ex => (
          <>
            <Mono key={`l-${ex}`} style={{ fontSize: 8, color: T.subtle, lineHeight: "22px" }}>{ex}</Mono>
            {symbols.map(s => {
              const spread = Math.random() * 30 - 5;
              const intensity = Math.min(Math.abs(spread) / 20, 1);
              const bg = spread > 0
                ? `rgba(0, 255, 136, ${intensity * 0.4})`
                : `rgba(255, 77, 77, ${intensity * 0.3})`;
              return (
                <div key={`${ex}-${s}`} style={{
                  background: bg, borderRadius: 3, height: 22,
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <Mono style={{ fontSize: 8, color: spread > 0 ? T.profit : T.loss }}>
                    {spread > 0 ? "+" : ""}{spread.toFixed(1)}
                  </Mono>
                </div>
              );
            })}
          </>
        ))}
      </div>
    </div>
  );
};

// ============ STRATEGY ROW ============

const StrategyRow = ({ s }) => (
  <div style={{
    display: "flex", alignItems: "center", gap: 12, padding: "10px 12px",
    borderRadius: 8, background: T.bg, border: `1px solid ${T.border}`,
    opacity: s.status === "disabled" ? 0.4 : 1,
  }}>
    <StatusDot status={s.status} />
    <div style={{ flex: 1, minWidth: 0 }}>
      <Mono style={{ fontSize: 11, color: T.text, fontWeight: 600 }}>{s.name}</Mono>
      <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
        <Mono style={{ fontSize: 9, color: T.subtle }}>{s.trades} trades</Mono>
        <Mono style={{ fontSize: 9, color: T.subtle }}>WR {s.wr}%</Mono>
      </div>
    </div>
    <div style={{ textAlign: "right" }}>
      <PnlText value={s.pnl} size={12} />
      {s.alloc > 0 && (
        <div style={{ marginTop: 3, width: 60 }}>
          <MiniBar value={s.alloc} max={30} color={s.status === "active" ? T.accent : T.muted} />
        </div>
      )}
    </div>
  </div>
);

// ============ TRADE FEED ============

const TradeFeed = () => (
  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
    {mockTrades.map((t, i) => (
      <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 6, background: i === 0 ? `${T.accent}08` : "transparent" }}>
        <Mono style={{ fontSize: 9, color: T.muted, width: 55, flexShrink: 0 }}>{t.time}</Mono>
        <Mono style={{
          fontSize: 8, fontWeight: 700, width: 22, textAlign: "center", flexShrink: 0,
          color: T.accent, background: T.accentDim, padding: "1px 4px", borderRadius: 3,
        }}>{t.strategy}</Mono>
        <Mono style={{ fontSize: 10, color: T.text, flex: 1 }}>{t.pair}</Mono>
        <Mono style={{ fontSize: 9, color: t.side === "LONG" ? T.profit : T.loss, width: 40, flexShrink: 0 }}>{t.side}</Mono>
        <Mono style={{ fontSize: 9, color: T.subtle, width: 70, flexShrink: 0, textAlign: "right" }}>{t.exchange}</Mono>
        <PnlText value={t.pnl} size={10} />
      </div>
    ))}
  </div>
);

// ============ ALERT FEED ============

const AlertFeed = () => (
  <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
    {mockAlerts.map((a, i) => {
      const typeColor = { info: T.accent, warn: T.warn, success: T.profit, error: T.loss };
      return (
        <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "5px 8px", borderRadius: 6 }}>
          <Mono style={{ fontSize: 9, color: T.muted, flexShrink: 0, marginTop: 1 }}>{a.time}</Mono>
          <div style={{ width: 4, height: 4, borderRadius: 2, background: typeColor[a.type], flexShrink: 0, marginTop: 5 }} />
          <Mono style={{ fontSize: 10, color: T.subtle, lineHeight: 1.4 }}>{a.msg}</Mono>
        </div>
      );
    })}
  </div>
);

// ============ EXCHANGE STATUS ============

const ExchangeStrip = () => (
  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
    {mockExchanges.filter(e => e.status === "connected").map(e => (
      <div key={e.name} style={{
        display: "flex", alignItems: "center", gap: 6, padding: "6px 10px",
        borderRadius: 6, background: T.bg, border: `1px solid ${T.border}`,
      }}>
        <StatusDot status={e.status} />
        <Mono style={{ fontSize: 10, color: T.text, fontWeight: 500 }}>{e.name}</Mono>
        <Mono style={{ fontSize: 8, color: e.latency < 30 ? T.profit : e.latency < 50 ? T.warn : T.loss }}>
          {e.latency}ms
        </Mono>
        <Mono style={{ fontSize: 8, color: T.subtle }}>
          {e.currency === "KRW" ? `₩${(e.balance / 1000).toFixed(0)}K` : `$${e.balance.toFixed(0)}`}
        </Mono>
      </div>
    ))}
  </div>
);

// ============ RISK MINI PANEL ============

const RiskPanel = () => {
  const checks = [
    { name: "Kill Switch", value: "OFF", ok: true },
    { name: "Circuit Breaker", value: "CLOSED", ok: true },
    { name: "Max Drawdown", value: "1.2%", ok: true, limit: "5%" },
    { name: "Daily Loss", value: "$4.20", ok: true, limit: "$15" },
    { name: "Net Exposure", value: "$120", ok: true, limit: "$5,000" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {checks.map(c => (
        <div key={c.name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 6, height: 6, borderRadius: 3, background: c.ok ? T.profit : T.loss }} />
          <Mono style={{ fontSize: 10, color: T.subtle, flex: 1 }}>{c.name}</Mono>
          <Mono style={{ fontSize: 10, color: c.ok ? T.profit : T.loss, fontWeight: 600 }}>{c.value}</Mono>
          {c.limit && <Mono style={{ fontSize: 8, color: T.muted }}>/ {c.limit}</Mono>}
        </div>
      ))}
    </div>
  );
};

// ============ PAGE: OVERVIEW ============

const OverviewPage = () => (
  <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, flex: 1, overflowY: "auto" }}>
    {/* Row 1: 4 KPI Cards (토스증권 스타일) */}
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
      {[
        { label: "총 자산", value: "$10,289.67", change: "+2.9%", up: true },
        { label: "일일 PnL", value: "+$289.67", change: "73% WR", up: true },
        { label: "누적 PnL", value: "+$1,289.67", change: "30일", up: true },
        { label: "활성 포지션", value: "3", change: "$847 노출", up: null },
      ].map(k => (
        <Card key={k.label}>
          <Label>{k.label}</Label>
          <div style={{ marginTop: 6 }}>
            <BigNum color={k.up === true ? T.profit : k.up === false ? T.loss : T.text}>{k.value}</BigNum>
          </div>
          <Mono style={{ fontSize: 9, color: k.up === true ? T.profit : k.up === false ? T.loss : T.subtle, marginTop: 4, display: "block" }}>
            {k.change}
          </Mono>
        </Card>
      ))}
    </div>

    {/* Row 2: Equity Curve + Risk */}
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 10 }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <Label>자본 곡선 (48H)</Label>
          <div style={{ display: "flex", gap: 4 }}>
            {["1H", "24H", "7D", "30D"].map(p => (
              <Mono key={p} style={{
                fontSize: 8, padding: "2px 6px", borderRadius: 4, cursor: "pointer",
                background: p === "24H" ? T.accentDim : "transparent",
                color: p === "24H" ? T.accent : T.muted,
              }}>{p}</Mono>
            ))}
          </div>
        </div>
        <EquityCurve />
      </Card>
      <Card>
        <Label>리스크 상태</Label>
        <div style={{ marginTop: 10 }}>
          <RiskPanel />
        </div>
      </Card>
    </div>

    {/* Row 3: Strategies + Heatmap */}
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
      <Card>
        <Label>전략 성과</Label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
          {mockStrategies.map(s => <StrategyRow key={s.id} s={s} />)}
        </div>
      </Card>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <Label>스프레드 히트맵 (bps)</Label>
          <Mono style={{ fontSize: 8, color: T.subtle }}>실시간</Mono>
        </div>
        <HeatmapMini />
      </Card>
    </div>

    {/* Row 4: Trades + Alerts */}
    <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 10 }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <Label>최근 체결</Label>
          <Mono style={{ fontSize: 8, color: T.accent, cursor: "pointer" }}>전체 보기</Mono>
        </div>
        <TradeFeed />
      </Card>
      <Card>
        <Label>알림</Label>
        <div style={{ marginTop: 8 }}>
          <AlertFeed />
        </div>
      </Card>
    </div>

    {/* Row 5: Exchange Status */}
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <Label>거래소 연결 상태</Label>
        <Mono style={{ fontSize: 9, color: T.profit }}>6/8 연결됨</Mono>
      </div>
      <ExchangeStrip />
    </Card>
  </div>
);

// ============ MAIN APP ============

export default function PhaseL_DashboardMockup() {
  const [page, setPage] = useState("overview");
  const [mode, setMode] = useState("PAPER");
  const [time, setTime] = useState("14:23:47");

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      setTime(now.toLocaleTimeString("ko-KR", { hour12: false }));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div style={{
      width: "100%", height: "100vh", background: T.bg, color: T.text,
      display: "flex", flexDirection: "column", overflow: "hidden",
      fontFamily: "'JetBrains Mono', 'SF Mono', monospace",
    }}>
      {/* Pulse animation */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>

      {/* Top Bar */}
      <TopBar mode={mode} setMode={setMode} />

      {/* Body */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Sidebar */}
        <Sidebar page={page} setPage={setPage} />

        {/* Main Content */}
        <OverviewPage />
      </div>

      {/* Bottom Status */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "4px 20px", background: T.surface, borderTop: `1px solid ${T.border}`,
      }}>
        <div style={{ display: "flex", gap: 16 }}>
          <Mono style={{ fontSize: 8, color: T.muted }}>KST {time}</Mono>
          <Mono style={{ fontSize: 8, color: T.muted }}>PAPER MODE</Mono>
          <Mono style={{ fontSize: 8, color: T.muted }}>LATENCY 12ms</Mono>
        </div>
        <div style={{ display: "flex", gap: 16 }}>
          <Mono style={{ fontSize: 8, color: T.muted }}>CPU 23%</Mono>
          <Mono style={{ fontSize: 8, color: T.muted }}>MEM 1.4GB</Mono>
          <Mono style={{ fontSize: 8, color: T.profit }}>HEALTHY</Mono>
        </div>
      </div>
    </div>
  );
}
