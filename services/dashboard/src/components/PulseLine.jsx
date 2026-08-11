import { useMemo } from "react";

/**
 * Renders recent readings as a heart-monitor-style trace. This is NOT
 * decorative noise - the line shape is derived directly from real values
 * (normalized latency), so a genuine spike in the data produces a genuine
 * spike in the line.
 */
export default function PulseLine({ values, status = "nominal", width = 320, height = 64 }) {
  const path = useMemo(() => {
    if (!values || values.length < 2) return "";

    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const padY = height * 0.18;

    const points = values.map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const norm = (v - min) / range; // 0..1
      const y = height - padY - norm * (height - padY * 2);
      return [x, y];
    });

    return points
      .map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`)
      .join(" ");
  }, [values, width, height]);

  const colorVar =
    status === "escalated"
      ? "var(--signal-red)"
      : status === "anomaly" || status === "pending"
      ? "var(--signal-amber)"
      : status === "resolved"
      ? "var(--signal-green)"
      : "var(--signal-cyan)";

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height={height}
      preserveAspectRatio="none"
      className={`pulse-line pulse-line--${status}`}
    >
      <defs>
        <linearGradient id={`fade-${status}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor={colorVar} stopOpacity="0.15" />
          <stop offset="75%" stopColor={colorVar} stopOpacity="1" />
          <stop offset="100%" stopColor={colorVar} stopOpacity="1" />
        </linearGradient>
      </defs>
      {path && (
        <path
          d={path}
          fill="none"
          stroke={`url(#fade-${status})`}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {/* leading dot to sell the "live" feel */}
      {path && values.length > 0 && (
        <circle
          cx={width}
          cy={height - height * 0.18 - ((values[values.length - 1] - Math.min(...values)) / ((Math.max(...values) - Math.min(...values)) || 1)) * (height - height * 0.36)}
          r="3.5"
          fill={colorVar}
        >
          <animate attributeName="r" values="3.5;5;3.5" dur="1.6s" repeatCount="indefinite" />
        </circle>
      )}
    </svg>
  );
}
