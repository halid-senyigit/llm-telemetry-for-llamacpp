import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, Cpu, Gauge, Radio, Thermometer, Zap } from "lucide-react";
import "./styles.css";

type LlamaStatus = {
  ok: boolean;
  error: string | null;
  model: string;
  currentGenerationSpeed: number;
  averageGenerationSpeed: number;
  totalGenerationTokens: number;
  promptSpeed: number;
  contextUsed: number;
  contextTotal: number;
  requestsProcessing: number;
  requestsQueued: number;
};

type GpuStatus = {
  ok: boolean;
  error: string | null;
  name: string;
  utilization: number;
  vramUsed: number;
  vramTotal: number;
  temperature: number;
  powerDraw: number;
  powerLimit: number;
  coreClock: number;
  memoryClock: number;
  pstate: string;
};

type Telemetry = {
  updatedAt: number;
  status: "Generating" | "Idle" | "Offline";
  llama: LlamaStatus;
  gpu: GpuStatus;
};

type Sample = {
  time: number;
  utilization: number;
  temperature: number;
};

const fallbackTelemetry: Telemetry = {
  updatedAt: Date.now() / 1000,
  status: "Offline",
  llama: {
    ok: false,
    error: "Waiting for backend",
    model: "llama.cpp model",
    currentGenerationSpeed: 0,
    averageGenerationSpeed: 0,
    totalGenerationTokens: 0,
    promptSpeed: 0,
    contextUsed: 0,
    contextTotal: 0,
    requestsProcessing: 0,
    requestsQueued: 0,
  },
  gpu: {
    ok: false,
    error: "Waiting for backend",
    name: "NVIDIA GPU unavailable",
    utilization: 0,
    vramUsed: 0,
    vramTotal: 0,
    temperature: 0,
    powerDraw: 0,
    powerLimit: 0,
    coreClock: 0,
    memoryClock: 0,
    pstate: "N/A",
  },
};

function formatNumber(value: number, digits = 0) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number.isFinite(value) ? value : 0);
}

function percent(value: number, total: number) {
  if (!total) return 0;
  return Math.min(100, Math.max(0, (value / total) * 100));
}

function MetricCard({
  label,
  value,
  unit,
  tone = "normal",
}: {
  label: string;
  value: string;
  unit?: string;
  tone?: "normal" | "hot" | "quiet";
}) {
  return (
    <section className={`metric metric-${tone}`}>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      {unit ? <span className="metric-unit">{unit}</span> : null}
    </section>
  );
}

function LiveChart({ samples }: { samples: Sample[] }) {
  const width = 680;
  const height = 230;
  const topPad = 14;
  const rightPad = 42;
  const bottomPad = 28;
  const leftPad = 42;
  const chartWidth = width - leftPad - rightPad;
  const chartHeight = height - topPad - bottomPad;
  const yTicks = [0, 25, 50, 75, 100];
  const xTicks = [
    { label: "60s", x: leftPad },
    { label: "30s", x: leftPad + chartWidth / 2 },
    { label: "0s", x: leftPad + chartWidth },
  ];
  const points = (key: keyof Pick<Sample, "utilization" | "temperature">, max: number) =>
    samples
      .map((sample, index) => {
        const x = leftPad + (index / Math.max(samples.length - 1, 1)) * chartWidth;
        const y = topPad + chartHeight - (Math.min(max, Math.max(0, sample[key])) / max) * chartHeight;
        return `${x},${y}`;
      })
      .join(" ");

  return (
    <section className="panel chart-panel">
      <div className="panel-heading">
        <div>
          <span className="eyeline">Realtime GPU</span>
          <h2>Usage / Temperature</h2>
        </div>
      </div>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="GPU usage and temperature history">
        <defs>
          <linearGradient id="fillUsage" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ff9f1c" stopOpacity="0.24" />
            <stop offset="100%" stopColor="#ff9f1c" stopOpacity="0" />
          </linearGradient>
        </defs>
        {yTicks.map((line) => {
          const y = topPad + chartHeight - (line / 100) * chartHeight;
          return (
            <g key={line}>
              <line x1={leftPad} x2={width - rightPad} y1={y} y2={y} className="grid-line" />
              <text x={leftPad - 10} y={y + 4} className="axis-label axis-left">{line}%</text>
              <text x={width - rightPad + 10} y={y + 4} className="axis-label axis-right">{line}C</text>
            </g>
          );
        })}
        {xTicks.map((tick) => (
          <text key={tick.label} x={tick.x} y={height - 7} className="axis-label axis-bottom">{tick.label}</text>
        ))}
        <polyline points={points("utilization", 100)} className="series series-usage" />
        <polyline points={points("temperature", 100)} className="series series-temp" />
      </svg>
      <div className="legend chart-legend">
        <span><i className="dot usage" /> GPU %</span>
        <span><i className="dot temp" /> TEMP C</span>
      </div>
    </section>
  );
}

function GpuStrip({ gpu }: { gpu: GpuStatus }) {
  const vramPercent = percent(gpu.vramUsed, gpu.vramTotal);
  return (
    <section className="gpu-strip">
      <div className="gpu-strip-head">
        <div className="gpu-title">
          <Cpu size={18} />
          <span>{gpu.name}</span>
        </div>
        <div className="gpu-facts">
          <span><Thermometer size={14} /> {formatNumber(gpu.temperature)} C</span>
          <span><Zap size={14} /> {formatNumber(gpu.powerDraw, 1)} / {formatNumber(gpu.powerLimit, 0)} W</span>
          <span>CORE {formatNumber(gpu.coreClock)} MHz</span>
          <span>MEM {formatNumber(gpu.memoryClock)} MHz</span>
        </div>
      </div>
      <div className="bar-group util-bar">
        <div className="bar-label">
          <span>GPU UTILIZATION</span>
          <strong>{formatNumber(gpu.utilization)}%</strong>
        </div>
        <div className="progress"><i style={{ width: `${Math.min(100, gpu.utilization)}%` }} /></div>
      </div>
      <div className="bar-group vram-bar">
        <div className="bar-label">
          <span>VRAM</span>
          <strong>{formatNumber(gpu.vramUsed)} / {formatNumber(gpu.vramTotal)} MiB · {formatNumber(vramPercent)}%</strong>
        </div>
        <div className="progress"><i style={{ width: `${vramPercent}%` }} /></div>
      </div>
    </section>
  );
}

function App() {
  const [telemetry, setTelemetry] = useState<Telemetry>(fallbackTelemetry);
  const [displayAverageGenerationSpeed, setDisplayAverageGenerationSpeed] = useState(
    fallbackTelemetry.llama.averageGenerationSpeed,
  );
  const [samples, setSamples] = useState<Sample[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let averageTimer: number | undefined;
    let pendingAverage = fallbackTelemetry.llama.averageGenerationSpeed;

    const applyTelemetry = (next: Telemetry) => {
      setTelemetry(next);
      pendingAverage = next.llama.averageGenerationSpeed;
      setSamples((existing) => [
        ...existing.slice(-59),
        {
          time: next.updatedAt,
          utilization: next.gpu.utilization,
          temperature: next.gpu.temperature,
        },
      ]);
    };

    averageTimer = window.setInterval(() => {
      setDisplayAverageGenerationSpeed(pendingAverage);
    }, 2000);

    const events = new EventSource("/api/events");
    events.onopen = () => setConnected(true);
    events.onerror = () => setConnected(false);
    events.onmessage = (event) => applyTelemetry(JSON.parse(event.data) as Telemetry);

    const fallback = window.setInterval(async () => {
      if (events.readyState !== EventSource.CLOSED) return;
      try {
        const response = await fetch("/api/status");
        applyTelemetry(await response.json());
      } catch {
        setConnected(false);
      }
    }, 2500);

    return () => {
      if (averageTimer) {
        window.clearInterval(averageTimer);
      }
      window.clearInterval(fallback);
      events.close();
    };
  }, []);

  const contextPercent = useMemo(
    () => percent(telemetry.llama.contextUsed, telemetry.llama.contextTotal),
    [telemetry.llama.contextTotal, telemetry.llama.contextUsed],
  );

  const statusClass = telemetry.status.toLowerCase();
  const updated = new Date(telemetry.updatedAt * 1000).toLocaleTimeString();

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>LLMBrief.local</h1>
          <p>{telemetry.llama.model}</p>
        </div>
        <div className="status-cluster">
          <span className={`badge ${statusClass}`}><Radio size={14} /> {telemetry.status}</span>
          <span className={`link-state ${connected ? "online" : ""}`}>{connected ? "SSE LIVE" : "API POLL"}</span>
          <span className="time">T+ {updated}</span>
        </div>
      </header>

      <section className="grid metrics-grid">
        <MetricCard label="Current generation speed" value={formatNumber(telemetry.llama.currentGenerationSpeed, 2)} unit="tok/s" tone="hot" />
        <MetricCard label="Average generation speed" value={formatNumber(displayAverageGenerationSpeed, 2)} unit="tok/s" />
        <MetricCard label="Prompt speed" value={formatNumber(telemetry.llama.promptSpeed, 2)} unit="tok/s" />
        <MetricCard label="Last context usage est." value={`${formatNumber(contextPercent)}%`} unit={`${formatNumber(telemetry.llama.contextUsed)} / ${formatNumber(telemetry.llama.contextTotal)}`} tone="quiet" />
        <MetricCard label="Requests / queue" value={`${telemetry.llama.requestsProcessing} / ${telemetry.llama.requestsQueued}`} />
      </section>

      <section className="main-grid">
        <LiveChart samples={samples.length ? samples : [{ time: Date.now(), utilization: 0, temperature: 0 }]} />
        <aside className="panel health-panel">
          <div className="panel-heading">
            <div>
              <span className="eyeline">Collector</span>
              <h2>Signal health</h2>
            </div>
            <Activity size={18} />
          </div>
          <div className="health-row">
            <span>llama.cpp</span>
            <strong className={telemetry.llama.ok ? "ok" : "bad"}>{telemetry.llama.ok ? "ONLINE" : "OFFLINE"}</strong>
          </div>
          <div className="health-row">
            <span>nvidia-smi</span>
            <strong className={telemetry.gpu.ok ? "ok" : "bad"}>{telemetry.gpu.ok ? "ONLINE" : "OFFLINE"}</strong>
          </div>
          <div className="mini-readout">
            <Gauge size={18} />
            <span>GPU {formatNumber(telemetry.gpu.utilization)}%</span>
            <span>{formatNumber(telemetry.gpu.temperature)} C</span>
          </div>
          <p className="fault">{telemetry.llama.error || telemetry.gpu.error || "All collectors reporting nominal telemetry."}</p>
        </aside>
      </section>

      <GpuStrip gpu={telemetry.gpu} />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
