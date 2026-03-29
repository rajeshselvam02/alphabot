import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const POSITIVE = "#00e5aa";
const NEGATIVE = "#ff4466";
const ACCENT = "#4488ff";

const f2 = (n) => (n != null ? Number(n).toFixed(2) : "—");
const f3 = (n) => (n != null ? Number(n).toFixed(3) : "—");
const usd = (n) =>
  n != null
    ? `$${Number(n).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`
    : "—";
const usdShort = (n) => {
  if (n == null) return "—";
  const value = Number(n);
  return Math.abs(value) >= 1000 ? `$${(value / 1000).toFixed(1)}k` : `$${value.toFixed(2)}`;
};
const pct = (n) => (n != null ? `${Number(n) >= 0 ? "+" : ""}${f2(n)}%` : "—");
const dd = (n) => `${f2(Number(n) || 0)}%`;
const pnlColor = (value) => (Number(value) >= 0 ? POSITIVE : NEGATIVE);
const riskColor = (value) => (Number(value) < 50 ? POSITIVE : Number(value) < 80 ? "#ffaa00" : NEGATIVE);

function marketForStrategy(strategy) {
  return strategy === "forex_mr" ? "forex" : "crypto";
}

function normalizeBooks(status) {
  const crypto = status?.books?.crypto || status?.portfolio || null;
  const forex = status?.books?.forex || null;
  return { crypto, forex };
}

async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) return null;
  return response.json();
}

function useBot() {
  const [connected, setConnected] = useState(false);
  const [books, setBooks] = useState({ crypto: null, forex: null });
  const [risk, setRisk] = useState(null);
  const [trades, setTrades] = useState({ crypto: [], forex: [], combined: [], totals: {} });
  const [signals, setSignals] = useState({});
  const [strategies, setStrategies] = useState([]);
  const [engine, setEngine] = useState(null);
  const [equitySeries, setEquitySeries] = useState([]);
  const [latestValidation, setLatestValidation] = useState(null);
  const [recentValidations, setRecentValidations] = useState([]);
  const socketRef = useRef(null);

  const applyStatus = useCallback((payload) => {
    const nextBooks = normalizeBooks(payload);
    if (nextBooks.crypto || nextBooks.forex) {
      setBooks(nextBooks);
      if (nextBooks.crypto?.equity != null) {
        setEquitySeries((prev) => [
          ...prev.slice(-60),
          {
            t: new Date().toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" }),
            v: nextBooks.crypto.equity,
          },
        ]);
      }
    }
    if (payload?.risk) setRisk(payload.risk);
    if (payload?.strategies) setStrategies(payload.strategies);
    if (payload?.engine) setEngine(payload.engine);
  }, []);

  const load = useCallback(async () => {
    try {
      const [statusRes, tradesRes, signalsRes, validationRes, validationsRes] = await Promise.all([
        fetchJson("/api/status"),
        fetchJson("/api/trades"),
        fetchJson("/api/signals"),
        fetchJson("/api/xaufx/validation/latest"),
        fetchJson("/api/learning/validations?limit=8"),
      ]);
      applyStatus(statusRes);
      setTrades({
        crypto: Array.isArray(tradesRes?.crypto) ? tradesRes.crypto : [],
        forex: Array.isArray(tradesRes?.forex) ? tradesRes.forex : [],
        combined: Array.isArray(tradesRes?.trades) ? tradesRes.trades : [],
        totals: tradesRes?.totals || {},
      });
      if (signalsRes?.signals) setSignals(signalsRes.signals);
      setLatestValidation(validationRes?.validation || null);
      setRecentValidations(Array.isArray(validationsRes?.validations) ? validationsRes.validations : []);
    } catch {}
  }, [applyStatus]);

  const connect = useCallback(() => {
    socketRef.current = new WebSocket(`ws://${window.location.hostname}:8000/ws`);
    socketRef.current.onopen = () => {
      setConnected(true);
      load();
    };
    socketRef.current.onclose = () => {
      setConnected(false);
      setTimeout(connect, 3000);
    };
    socketRef.current.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg._ch === "status") {
          applyStatus(msg);
          return;
        }
        if (msg._ch === "signals") {
          setSignals((prev) => ({ ...prev, [msg.symbol || msg.strategy]: msg }));
          return;
        }
        if (msg._ch === "trades") {
          await load();
        }
      } catch {}
    };
  }, [applyStatus, load]);

  useEffect(() => {
    load();
    connect();
    return () => socketRef.current?.close();
  }, [connect, load]);

  const api = useCallback((path) => fetch(`/api${path}`, { method: "POST" }), []);

  return {
    connected,
    books,
    risk,
    trades,
    signals,
    strategies,
    engine,
    equitySeries,
    latestValidation,
    recentValidations,
    api,
    reload: load,
  };
}

function useCompactLayout(maxWidth = 420) {
  const getMatches = () =>
    typeof window !== "undefined" ? window.matchMedia(`(max-width: ${maxWidth}px)`).matches : false;
  const [compact, setCompact] = useState(getMatches);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const media = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const onChange = (event) => setCompact(event.matches);
    setCompact(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [maxWidth]);

  return compact;
}

function Card({ label, value, sub, valueColor }) {
  return (
    <div style={styles.card}>
      <div style={styles.cardLabel}>{label}</div>
      <div style={{ ...styles.cardValue, color: valueColor || "#e2e8f0" }}>{value}</div>
      {sub ? <div style={styles.cardSub}>{sub}</div> : null}
    </div>
  );
}

function SectionTitle({ children, right }) {
  return (
    <div style={styles.sectionTitleRow}>
      <div style={styles.sectionTitle}>{children}</div>
      {right ? <div style={styles.sectionTitleMeta}>{right}</div> : null}
    </div>
  );
}

function SignalCard({ symbol, signal, compact }) {
  if (!signal) return null;
  const z = signal.zscore ?? 0;
  const width = Math.min((Math.abs(z) / 3) * 100, 100);
  const bandColor = z < -2 ? POSITIVE : z > 2 ? NEGATIVE : ACCENT;
  const side = signal.position;

  return (
    <div style={styles.panel}>
      <div style={compact ? styles.rowStack : styles.rowBetween}>
        <span style={styles.symbol}>{symbol}</span>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {signal.close != null ? <span style={styles.smallMono}>{usdShort(signal.close)}</span> : null}
          <span style={badge(side === "long" ? POSITIVE : side === "short" ? NEGATIVE : "#5a6a8a")}>
            {side ? side.toUpperCase() : "FLAT"}
          </span>
        </div>
      </div>
      <div style={{ ...styles.rowBetween, marginTop: 8 }}>
        <span style={styles.mutedTiny}>Z-Score</span>
        <span style={{ ...styles.monoStrong, color: bandColor }}>{z >= 0 ? "+" : ""}{f3(z)}</span>
      </div>
      <div style={styles.progressTrack}>
        <div style={{ ...styles.progressFill, width: `${width}%`, background: bandColor }} />
      </div>
      <div style={{ ...styles.rowBetween, marginTop: 6, fontSize: 9, color: "#3a4a6a" }}>
        <span>σ {f2(signal.std)}</span>
        <span>μ {f2(signal.mean)}</span>
      </div>
    </div>
  );
}

function ForexSignalCard({ symbol, signal, compact }) {
  if (!signal) return null;
  const reason = Array.isArray(signal.reason) ? signal.reason.join(", ") : signal.reason || "—";
  const action = (signal.action || signal.signal || "hold").toUpperCase();
  const color = action === "BUY" ? POSITIVE : action === "SELL" ? NEGATIVE : ACCENT;

  return (
    <div style={styles.panel}>
      <div style={compact ? styles.rowStack : styles.rowBetween}>
        <span style={styles.symbol}>{symbol}</span>
        <span style={badge(color)}>{action}</span>
      </div>
      <div style={compact ? styles.infoGrid1 : styles.infoGrid2}>
        <Info label="Z-Score" value={signal.zscore != null ? `${signal.zscore >= 0 ? "+" : ""}${f3(signal.zscore)}` : "—"} />
        <Info label="Quality" value={signal.quality != null ? f3(signal.quality) : "—"} />
        <Info label="Session" value={signal.session || "—"} />
        <Info label="Reason" value={reason} />
      </div>
    </div>
  );
}

function PositionRow({ position, compact }) {
  const pnl = position.unrealized_pnl || 0;
  const quantity = Number(position.quantity ?? position.lots ?? 0);
  const quantityLabel = position.book === "forex" ? `${quantity.toFixed(2)} lots` : quantity.toFixed(4);

  return (
    <div style={compact ? styles.listRowStack : styles.listRow}>
      <div style={styles.col}>
        <div style={styles.validationHeaderRow}>
          <span style={styles.symbol}>{position.symbol}</span>
          <span style={badge(position.side === "long" ? POSITIVE : NEGATIVE)}>{position.side?.toUpperCase()}</span>
          <span style={marketBadge(position.book)}>{position.book?.toUpperCase()}</span>
        </div>
        <span style={styles.smallMono}>
          {quantityLabel} @ {usd(position.entry_price)}
        </span>
        <span style={{ ...styles.smallMono, color: ACCENT }}>Now: {usd(position.current_price)}</span>
      </div>
      <div style={{ ...styles.col, alignItems: compact ? "flex-start" : "flex-end" }}>
        <span style={{ ...styles.monoStrong, color: pnlColor(pnl) }}>{usd(pnl)}</span>
        <span style={{ ...styles.smallMono, color: pnlColor((position.unrealized_pct || 0) * 100) }}>
          {pct((position.unrealized_pct || 0) * 100)}
        </span>
      </div>
    </div>
  );
}

function TradeRow({ trade, compact }) {
  const quantity = Number(trade.quantity ?? trade.lots ?? 0);
  const quantityLabel = trade.book === "forex" ? `${quantity.toFixed(2)} lots` : quantity.toFixed(4);

  return (
    <div style={compact ? styles.listRowStack : styles.listRow}>
      <div style={styles.col}>
        <div style={styles.validationHeaderRow}>
          <span style={styles.symbol}>{trade.symbol}</span>
          <span style={badge(trade.side === "buy" ? POSITIVE : trade.side === "sell" ? NEGATIVE : ACCENT)}>
            {(trade.side || "trade").toUpperCase()}
          </span>
          <span style={marketBadge(trade.book)}>{trade.book?.toUpperCase()}</span>
        </div>
        <span style={styles.smallMono}>
          {quantityLabel} @ {usd(trade.fill_price)}
        </span>
      </div>
      <div style={{ ...styles.col, alignItems: compact ? "flex-start" : "flex-end" }}>
        <span style={{ ...styles.monoStrong, color: trade.pnl != null ? pnlColor(trade.pnl) : "#5a6a8a" }}>
          {trade.pnl != null ? usd(trade.pnl) : "open"}
        </span>
        <span style={styles.cardSub}>{trade.timestamp ? new Date(trade.timestamp).toLocaleTimeString() : ""}</span>
      </div>
    </div>
  );
}

function Info({ label, value }) {
  return (
    <div>
      <div style={styles.mutedTiny}>{label}</div>
      <div style={{ ...styles.smallMono, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function BookPanel({ title, book, accent, compact }) {
  return (
    <div style={{ ...styles.panel, borderColor: `${accent}33` }}>
      <div style={compact ? styles.rowStack : styles.rowBetween}>
        <span style={{ ...styles.symbol, color: accent }}>{title}</span>
        <span style={marketBadge(title.toLowerCase())}>{(book?.mode || "paper").toUpperCase()}</span>
      </div>
      <div style={compact ? styles.heroValueCompact : styles.heroValue}>{usd(book?.equity)}</div>
      <div style={compact ? styles.inlineStatStack : styles.inlineStatRow}>
        <span style={{ ...styles.monoStrong, color: pnlColor(book?.return_pct || book?.total_return_pct || 0) }}>
          {pct(book?.return_pct ?? book?.total_return_pct)}
        </span>
        <span style={styles.cardSub}>Cash {usd(book?.cash)}</span>
      </div>
      <div style={compact ? styles.infoGrid1 : styles.infoGrid2}>
        <Info label="Unrealized" value={usdShort(book?.unrealized_pnl)} />
        <Info label="Open Pos" value={book?.open_positions ?? 0} />
        <Info label="Trades" value={book?.total_trades ?? 0} />
        <Info label="Win Rate" value={`${f2(book?.win_rate ?? book?.win_rate_pct)}%`} />
      </div>
    </div>
  );
}

function StrategyPanel({ strategy, compact }) {
  const active = strategy.is_active ?? strategy.active;
  return (
    <div style={styles.panel}>
      <div style={compact ? styles.rowStack : styles.rowBetween}>
        <span style={styles.symbol}>{strategy.strategy || strategy.name}</span>
        <span style={badge(active ? POSITIVE : NEGATIVE)}>{active ? "ACTIVE" : "PAUSED"}</span>
      </div>
      <div style={compact ? styles.infoGrid2 : styles.infoGrid4}>
        <Info label="Market" value={marketForStrategy(strategy.strategy || strategy.name).toUpperCase()} />
        <Info label="Signals" value={strategy.signals_fired || 0} />
        <Info label="Trades" value={strategy.trades_made || 0} />
        <Info label="Bars" value={strategy.total_bars || strategy.bar_count || 0} />
      </div>
    </div>
  );
}

function ValidationPanel({ validation, compact }) {
  const metrics = validation?.metrics || {};
  const config = validation?.config || {};
  const failureReasons = String(metrics.failure_reasons || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
  const verdict = metrics.verdict || validation?.status || "unavailable";
  const verdictColor =
    verdict === "research_winner"
      ? POSITIVE
      : verdict === "promotable_baseline"
        ? ACCENT
        : verdict === "candidate"
          ? "#ffaa00"
          : NEGATIVE;

  return (
    <div style={styles.panel}>
      <SectionTitle right={validation?.model_name || "xaufx_validation"}>Latest XAU/FX Validation</SectionTitle>
      <div style={compact ? styles.rowStack : styles.rowBetween}>
        <span style={styles.symbol}>{verdict.toUpperCase()}</span>
        <span style={badge(verdictColor)}>{verdict.toUpperCase()}</span>
      </div>
      <div style={compact ? styles.infoGrid1 : styles.infoGrid2}>
        <Info label="Config Hash" value={config.config_hash || "—"} />
        <Info label="Run ID" value={metrics.run_id || validation?.id || "—"} />
        <Info label="Artifact" value={validation?.artifact_path ? validation.artifact_path.split("/").slice(-1)[0] : "—"} />
        <Info label="Test Return" value={metrics.test_return_pct != null ? pct(metrics.test_return_pct) : "—"} />
      </div>
      <div style={{ marginTop: 12 }}>
        <div style={styles.mutedTiny}>Top Failure Reasons</div>
        {failureReasons.length > 0 ? (
          <div style={styles.validationReasonList}>
            {failureReasons.slice(0, 3).map((reason) => (
              <div key={reason} style={styles.validationReason}>
                {reason}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ ...styles.smallMono, marginTop: 8, color: POSITIVE }}>No active failure reasons</div>
        )}
      </div>
    </div>
  );
}

function ValidationRow({ validation, compact }) {
  const metrics = validation?.metrics || {};
  const config = validation?.config || {};
  const verdict = metrics.verdict || validation?.status || "unknown";
  const verdictColor =
    verdict === "research_winner"
      ? POSITIVE
      : verdict === "promotable_baseline"
        ? ACCENT
        : verdict === "candidate"
          ? "#ffaa00"
          : NEGATIVE;

  return (
    <div style={compact ? styles.listRowStack : styles.listRow}>
      <div style={styles.col}>
        <div style={compact ? styles.validationHeaderStack : styles.validationHeaderRow}>
          <span style={styles.symbol}>{metrics.run_id || validation?.id || "validation"}</span>
          <span style={badge(verdictColor)}>{verdict.toUpperCase()}</span>
        </div>
        <span style={styles.wrapMono}>cfg {config.config_hash || "—"}</span>
        <span style={styles.wrapSubtle}>
          {validation?.artifact_path ? validation.artifact_path.split("/").slice(-1)[0] : "—"}
        </span>
      </div>
      <div style={{ ...styles.col, alignItems: compact ? "flex-start" : "flex-end" }}>
        <span style={{ ...styles.monoStrong, color: pnlColor(metrics.test_return_pct || 0) }}>
          {metrics.test_return_pct != null ? pct(metrics.test_return_pct) : "—"}
        </span>
        <span style={styles.smallMono}>{metrics.test_trades != null ? `${metrics.test_trades} trades` : ""}</span>
      </div>
    </div>
  );
}

function Nav({ tab, setTab, compact }) {
  const items = [
    { id: "overview", icon: "◈", label: compact ? "Home" : "Home" },
    { id: "signals", icon: "⟐", label: compact ? "Sig" : "Signals" },
    { id: "positions", icon: "⊞", label: "Pos" },
    { id: "trades", icon: "↕", label: compact ? "Exec" : "Trades" },
    { id: "risk", icon: "⊛", label: "Risk" },
    { id: "validation", icon: "⌬", label: compact ? "Val" : "Valid" },
  ];

  return (
    <div style={styles.nav}>
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => setTab(item.id)}
          style={{ ...(compact ? styles.navButtonCompact : styles.navButton), color: tab === item.id ? ACCENT : "#3a4a6a" }}
        >
          {tab === item.id ? <div style={styles.navActiveLine} /> : null}
          <span style={tab === item.id ? styles.navIconActive : compact ? styles.navIconCompact : styles.navIcon}>{item.icon}</span>
          <span style={compact ? styles.navLabelCompact : styles.navLabel}>{item.label}</span>
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const { connected, books, risk, trades, signals, strategies, engine, equitySeries, latestValidation, recentValidations, api } = useBot();
  const [tab, setTab] = useState("overview");
  const [paused, setPaused] = useState(false);
  const compact = useCompactLayout();

  const cryptoBook = books.crypto;
  const forexBook = books.forex;
  const phase = engine?.phase || "booting";
  const phaseLabel = phase.replace(/_/g, " ").toUpperCase();
  const phaseColor = engine?.ready ? POSITIVE : phase === "error" ? NEGATIVE : "#ffaa00";
  const cryptoSignals = Object.entries(signals).filter(([, signal]) => signal.strategy !== "forex_mr" && signal.zscore != null);
  const forexSignals = Object.entries(signals).filter(([, signal]) => signal.strategy === "forex_mr");
  const cryptoPositions = Array.isArray(cryptoBook?.positions) ? cryptoBook.positions : [];
  const forexPositions = Array.isArray(forexBook?.positions) ? forexBook.positions : [];
  const cryptoStrategies = strategies.filter((item) => marketForStrategy(item.strategy || item.name) === "crypto");
  const forexStrategies = strategies.filter((item) => marketForStrategy(item.strategy || item.name) === "forex");

  const toggle = async () => {
    await api(paused ? "/control/resume" : "/control/pause");
    setPaused((prev) => !prev);
  };

  return (
    <div style={styles.app}>
      <style>{globalCss}</style>
      <div style={compact ? styles.headerCompact : styles.header}>
        <div style={styles.headerMeta}>
          <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: -1 }}>⬡ AlphaBot</span>
          <span style={badge(connected ? POSITIVE : NEGATIVE)}>{connected ? "LIVE" : "OFF"}</span>
          <span style={badge(ACCENT)}>PAPER</span>
          <span title={engine?.detail || phaseLabel} style={badge(phaseColor)}>{phaseLabel}</span>
        </div>
        <button onClick={toggle} style={{ ...styles.actionButton, width: compact ? "100%" : undefined, borderColor: paused ? `${POSITIVE}40` : `${NEGATIVE}40`, color: paused ? POSITIVE : NEGATIVE }}>
          {paused ? "▶ Resume" : "⏸ Pause"}
        </button>
      </div>

      <div style={styles.page}>
        {tab === "overview" ? (
          <div style={styles.stack}>
            <BookPanel title="Crypto" book={cryptoBook} accent={ACCENT} compact={compact} />
            <BookPanel title="Forex" book={forexBook} accent="#ffaa00" compact={compact} />

            <div style={compact ? styles.grid1 : styles.grid2}>
              <Card label="Crypto Unrealized" value={usdShort(cryptoBook?.unrealized_pnl)} valueColor={pnlColor(cryptoBook?.unrealized_pnl || 0)} sub={`${cryptoBook?.open_positions || 0} open`} />
              <Card label="Forex Unrealized" value={usdShort(forexBook?.unrealized_pnl)} valueColor={pnlColor(forexBook?.unrealized_pnl || 0)} sub={`${forexBook?.open_positions || 0} open`} />
              <Card label="Crypto Win Rate" value={`${f2(cryptoBook?.win_rate_pct)}%`} sub={`${cryptoBook?.total_trades || 0} trades`} />
              <Card label="Forex Win Rate" value={`${f2(forexBook?.win_rate)}%`} sub={`${forexBook?.total_trades || 0} trades`} />
              <Card label="Risk Drawdown" value={dd((risk?.drawdown || 0) * 100)} valueColor={riskColor((risk?.drawdown || 0) * 100)} sub="Global risk layer" />
              <Card label="Engine" value={phaseLabel} valueColor={phaseColor} sub={engine?.detail || "—"} />
            </div>

            <div style={styles.panel}>
              <SectionTitle>Crypto Equity Curve</SectionTitle>
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={equitySeries} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={ACCENT} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={ACCENT} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1a2030" />
                  <XAxis dataKey="t" tick={{ fill: "#3a4a6a", fontSize: 8 }} tickLine={false} />
                  <YAxis tick={{ fill: "#3a4a6a", fontSize: 8 }} tickLine={false} width={38} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
                  <Tooltip
                    contentStyle={{ background: "#0e1117", border: "1px solid #1e2535", borderRadius: 8, fontSize: 11, fontFamily: "monospace" }}
                    formatter={(v) => [usd(v), "Equity"]}
                  />
                  <ReferenceLine y={cryptoBook?.initial_capital || 10000} stroke="#1e2535" strokeDasharray="4 4" />
                  <Area type="monotone" dataKey="v" stroke={ACCENT} fill="url(#equityGradient)" strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            <div>
              <SectionTitle>Crypto Signals</SectionTitle>
              <div style={compact ? styles.grid1 : styles.grid2}>
                {cryptoSignals.slice(0, 4).map(([symbol, signal]) => (
                  <SignalCard key={symbol} symbol={symbol} signal={signal} compact={compact} />
                ))}
              </div>
            </div>

            <div>
              <SectionTitle>Forex Diagnostics</SectionTitle>
              <div style={styles.grid1}>
                {forexSignals.slice(0, 4).map(([symbol, signal]) => (
                  <ForexSignalCard key={symbol} symbol={symbol} signal={signal} compact={compact} />
                ))}
              </div>
            </div>
          </div>
        ) : null}

        {tab === "signals" ? (
          <div style={styles.stack}>
            <SectionTitle>Crypto Signals</SectionTitle>
            {cryptoSignals.length > 0 ? cryptoSignals.map(([symbol, signal]) => <SignalCard key={symbol} symbol={symbol} signal={signal} compact={compact} />) : <Empty label="No crypto signals yet" />}
            <SectionTitle>Forex Diagnostics</SectionTitle>
            {forexSignals.length > 0 ? forexSignals.map(([symbol, signal]) => <ForexSignalCard key={symbol} symbol={symbol} signal={signal} compact={compact} />) : <Empty label="No forex diagnostics yet" />}
          </div>
        ) : null}

        {tab === "positions" ? (
          <div style={styles.stack}>
            <SectionTitle right={`${cryptoPositions.length} open`}>Crypto Positions</SectionTitle>
            <div style={styles.listPanel}>
              {cryptoPositions.length > 0 ? cryptoPositions.map((position) => <PositionRow key={`${position.book}-${position.symbol}`} position={{ ...position, book: "crypto" }} compact={compact} />) : <Empty label="No crypto positions" />}
            </div>
            <SectionTitle right={`${forexPositions.length} open`}>Forex Positions</SectionTitle>
            <div style={styles.listPanel}>
              {forexPositions.length > 0 ? forexPositions.map((position) => <PositionRow key={`${position.book || "forex"}-${position.symbol}`} position={{ ...position, book: "forex" }} compact={compact} />) : <Empty label="No forex positions" />}
            </div>
          </div>
        ) : null}

        {tab === "trades" ? (
          <div style={styles.stack}>
            <SectionTitle right={`${trades.totals?.crypto || 0} total`}>Crypto Trades</SectionTitle>
            <div style={styles.listPanel}>
              {trades.crypto.length > 0 ? trades.crypto.map((trade, idx) => <TradeRow key={`crypto-${trade.id || idx}`} trade={trade} compact={compact} />) : <Empty label="No crypto trades" />}
            </div>
            <SectionTitle right={`${trades.totals?.forex || 0} total`}>Forex Trades</SectionTitle>
            <div style={styles.listPanel}>
              {trades.forex.length > 0 ? trades.forex.map((trade, idx) => <TradeRow key={`forex-${trade.id || idx}`} trade={trade} compact={compact} />) : <Empty label="No forex trades" />}
            </div>
          </div>
        ) : null}

        {tab === "risk" ? (
          <div style={styles.stack}>
            {risk?.is_halted ? (
              <div style={{ ...styles.panel, borderColor: `${NEGATIVE}55`, background: "#ff446610" }}>
                <div style={{ fontWeight: 700, marginBottom: 6, color: NEGATIVE }}>HALTED</div>
                <div style={{ fontSize: 12 }}>{risk.halt_reason}</div>
                <button onClick={() => api("/control/resume")} style={{ ...styles.actionButton, marginTop: 10, width: "100%", color: NEGATIVE, borderColor: NEGATIVE }}>
                  RESUME
                </button>
              </div>
            ) : null}

            <div style={compact ? styles.grid1 : styles.grid2}>
              <Card label="Drawdown" value={dd((risk?.drawdown || 0) * 100)} valueColor={riskColor((risk?.drawdown || 0) * 100)} sub="Limit: 10%" />
              <Card label="Daily Loss" value={dd((risk?.daily_loss || 0) * 100)} valueColor={riskColor((risk?.daily_loss || 0) * 100)} sub="Limit: 3%" />
              <Card label="Peak Equity" value={usdShort(risk?.peak_equity)} />
              <Card label="Trades" value={risk?.trade_count || 0} sub="Risk layer count" />
              <Card label="24h VaR 99%" value={usdShort(risk?.var_24h)} valueColor={riskColor(risk?.var_pct || 0)} sub={`${f2(risk?.var_pct || 0)}% of equity`} />
              <Card label="Jump VaR" value={usdShort(risk?.var_jump)} sub="Poisson" />
            </div>

            {risk?.budget && Object.keys(risk.budget).length > 0 ? (
              <div style={styles.panel}>
                <SectionTitle>Risk Budget</SectionTitle>
                <div style={compact ? styles.infoGrid1 : styles.infoGrid2}>
                  <Info label="Strategy" value={risk.budget.strategy || "—"} />
                  <Info label="Asset" value={risk.budget.asset_class || "—"} />
                  <Info label="Scale" value={`${f2((risk.budget.scale || 0) * 100)}%`} />
                  <Info label="Global Exp" value={usdShort(risk.budget.global_exposure)} />
                  <Info label="Strategy Exp" value={usdShort(risk.budget.strategy_exposure)} />
                  <Info label="Asset Exp" value={usdShort(risk.budget.asset_exposure)} />
                  <Info label="Symbol Exp" value={usdShort(risk.budget.symbol_exposure)} />
                  <Info label="Strategy Budget" value={`${f2((risk.budget.strategy_budget_pct || 0) * 100)}%`} />
                  <Info label="Asset Budget" value={`${f2((risk.budget.asset_budget_pct || 0) * 100)}%`} />
                </div>
              </div>
            ) : null}

            <SectionTitle>Crypto Strategies</SectionTitle>
            {cryptoStrategies.map((strategy) => <StrategyPanel key={strategy.strategy || strategy.name} strategy={strategy} compact={compact} />)}
            <SectionTitle>Forex Strategies</SectionTitle>
            {forexStrategies.map((strategy) => <StrategyPanel key={strategy.strategy || strategy.name} strategy={strategy} compact={compact} />)}
          </div>
        ) : null}

        {tab === "validation" ? (
          <div style={styles.stack}>
            <ValidationPanel validation={latestValidation} compact={compact} />
            <SectionTitle right={`${recentValidations.length} recent`}>Validation History</SectionTitle>
            <div style={styles.listPanel}>
              {recentValidations.length > 0 ? (
                recentValidations.map((validation, idx) => (
                  <ValidationRow key={validation.id || idx} validation={validation} compact={compact} />
                ))
              ) : (
                <Empty label="No validation history available" />
              )}
            </div>
          </div>
        ) : null}
      </div>

      <Nav tab={tab} setTab={setTab} compact={compact} />
    </div>
  );
}

function Empty({ label }) {
  return <div style={styles.empty}>{label}</div>;
}

function badge(color) {
  return {
    fontSize: 8,
    fontWeight: 700,
    padding: "2px 7px",
    borderRadius: 4,
    letterSpacing: 1,
    background: `${color}12`,
    color,
    border: `1px solid ${color}30`,
  };
}

function marketBadge(market) {
  return badge(market === "forex" ? "#ffaa00" : ACCENT);
}

const globalCss = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { overflow-x: hidden; -webkit-tap-highlight-color: transparent; }
  ::-webkit-scrollbar { width: 3px; }
  ::-webkit-scrollbar-thumb { background: #1e2535; }
`;

const styles = {
  app: {
    minHeight: "100vh",
    background: "#070b10",
    color: "#e2e8f0",
    fontFamily: "system-ui,sans-serif",
    paddingBottom: 72,
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "14px 16px 12px",
    background: "#0a0e16",
    borderBottom: "1px solid #1e2535",
    position: "sticky",
    top: 0,
    zIndex: 100,
  },
  headerCompact: {
    display: "flex",
    flexDirection: "column",
    alignItems: "stretch",
    gap: 10,
    padding: "14px 16px 12px",
    background: "#0a0e16",
    borderBottom: "1px solid #1e2535",
    position: "sticky",
    top: 0,
    zIndex: 100,
  },
  headerMeta: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    flexWrap: "wrap",
  },
  page: {
    padding: 16,
  },
  stack: {
    display: "flex",
    flexDirection: "column",
    gap: 14,
  },
  grid1: {
    display: "grid",
    gridTemplateColumns: "1fr",
    gap: 10,
  },
  grid2: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 10,
  },
  panel: {
    background: "#0e1117",
    border: "1px solid #1e2535",
    borderRadius: 14,
    padding: 16,
    minWidth: 0,
  },
  listPanel: {
    background: "#0e1117",
    border: "1px solid #1e2535",
    borderRadius: 14,
    overflow: "hidden",
  },
  card: {
    background: "#0e1117",
    border: "1px solid #1e2535",
    borderRadius: 12,
    padding: "14px 16px",
    minWidth: 0,
  },
  cardLabel: {
    color: "#5a6a8a",
    fontSize: 9,
    letterSpacing: 2,
    textTransform: "uppercase",
    marginBottom: 6,
  },
  cardValue: {
    fontSize: 20,
    fontWeight: 700,
    fontFamily: "monospace",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  cardSub: {
    color: "#5a6a8a",
    fontSize: 10,
    marginTop: 3,
  },
  wrapSubtle: {
    color: "#5a6a8a",
    fontSize: 10,
    marginTop: 3,
    wordBreak: "break-word",
    overflowWrap: "anywhere",
  },
  validationReasonList: {
    marginTop: 8,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  validationReason: {
    border: "1px solid #1e2535",
    borderRadius: 8,
    padding: "8px 10px",
    fontSize: 11,
    color: "#cbd5e1",
    background: "#0a0e16",
    fontFamily: "monospace",
    wordBreak: "break-word",
  },
  sectionTitle: {
    color: "#5a6a8a",
    fontSize: 9,
    letterSpacing: 2,
    textTransform: "uppercase",
  },
  sectionTitleRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 8,
    marginBottom: 10,
    flexWrap: "wrap",
  },
  sectionTitleMeta: {
    color: "#3a4a6a",
    fontSize: 10,
    textAlign: "right",
    wordBreak: "break-word",
  },
  symbol: {
    color: "#e2e8f0",
    fontWeight: 700,
    fontFamily: "monospace",
    fontSize: 12,
  },
  monoStrong: {
    fontFamily: "monospace",
    fontWeight: 700,
    fontSize: 13,
  },
  smallMono: {
    color: "#5a6a8a",
    fontSize: 10,
    fontFamily: "monospace",
  },
  wrapMono: {
    color: "#5a6a8a",
    fontSize: 10,
    fontFamily: "monospace",
    wordBreak: "break-word",
    overflowWrap: "anywhere",
  },
  mutedTiny: {
    color: "#3a4a6a",
    fontSize: 8,
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  rowBetween: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  rowStack: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 8,
  },
  progressTrack: {
    height: 4,
    background: "#1e2535",
    borderRadius: 2,
    overflow: "hidden",
    marginTop: 6,
  },
  progressFill: {
    height: "100%",
    boxShadow: "0 0 8px rgba(255,255,255,0.2)",
    transition: "width .5s",
  },
  infoGrid2: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 10,
    marginTop: 12,
  },
  infoGrid1: {
    display: "grid",
    gridTemplateColumns: "1fr",
    gap: 10,
    marginTop: 12,
  },
  infoGrid4: {
    display: "grid",
    gridTemplateColumns: "repeat(4,1fr)",
    gap: 10,
    marginTop: 12,
  },
  listRow: {
    padding: "12px 16px",
    borderBottom: "1px solid #0d1117",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  listRowStack: {
    padding: "12px 16px",
    borderBottom: "1px solid #0d1117",
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 10,
  },
  col: {
    display: "flex",
    flexDirection: "column",
    gap: 3,
  },
  validationHeaderRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  validationHeaderStack: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 6,
  },
  heroValue: {
    fontSize: 30,
    fontWeight: 800,
    fontFamily: "monospace",
    letterSpacing: -1,
    margin: "10px 0 6px",
  },
  heroValueCompact: {
    fontSize: 24,
    fontWeight: 800,
    fontFamily: "monospace",
    letterSpacing: -1,
    margin: "10px 0 6px",
    wordBreak: "break-word",
  },
  inlineStatRow: {
    display: "flex",
    gap: 12,
    alignItems: "center",
    flexWrap: "wrap",
  },
  inlineStatStack: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 4,
  },
  empty: {
    padding: 42,
    textAlign: "center",
    color: "#3a4a6a",
    fontSize: 13,
    background: "#0e1117",
    borderRadius: 12,
    border: "1px solid #1e2535",
  },
  nav: {
    position: "fixed",
    bottom: 0,
    left: 0,
    right: 0,
    background: "#0a0e16",
    borderTop: "1px solid #1e2535",
    display: "flex",
    zIndex: 200,
    paddingBottom: "env(safe-area-inset-bottom,0px)",
  },
  navButton: {
    flex: 1,
    padding: "10px 2px 8px",
    background: "none",
    border: "none",
    cursor: "pointer",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 3,
    position: "relative",
  },
  navButtonCompact: {
    flex: 1,
    minHeight: 62,
    padding: "8px 1px 10px",
    background: "none",
    border: "none",
    cursor: "pointer",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: 5,
    position: "relative",
  },
  navActiveLine: {
    position: "absolute",
    top: 0,
    width: 28,
    height: 2,
    background: ACCENT,
    borderRadius: "0 0 2px 2px",
  },
  navIcon: {
    fontSize: 18,
    lineHeight: 1,
  },
  navIconCompact: {
    fontSize: 20,
    lineHeight: 1,
  },
  navIconActive: {
    fontSize: 20,
    lineHeight: 1,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 28,
    height: 28,
    borderRadius: 9,
    background: "#4488ff14",
    border: "1px solid #4488ff2f",
  },
  navLabel: {
    fontSize: 8,
    fontWeight: 600,
    letterSpacing: 0.5,
    textTransform: "uppercase",
  },
  navLabelCompact: {
    fontSize: 7,
    fontWeight: 700,
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  actionButton: {
    padding: "7px 14px",
    borderRadius: 20,
    fontSize: 11,
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "inherit",
    background: "transparent",
    border: "1px solid #1e2535",
  },
};
