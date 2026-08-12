import { useEffect, useRef, useState } from "react";
import { Bot, Loader2, Send, User } from "lucide-react";
import { askAgent } from "../api";

const SUGGESTIONS = [
  "What's our biggest reliability risk right now?",
  "Summarize incidents in the last hour.",
  "Which service is most at risk of breaching?",
];

export default function AICopilot() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      text: "I'm your AI reliability engineer. Ask me about incidents, forecasts, or what to fix first.",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, busy]);

  async function send(question) {
    const q = (question ?? input).trim();
    if (!q || busy) return;
    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    const result = await askAgent(q);
    setBusy(false);
    setMessages((m) => [
      ...m,
      {
        role: "assistant",
        text:
          result?.answer ||
          "AI agent unreachable — is the ai-reasoning-agent service running?",
        mode: result?.mode,
      },
    ]);
  }

  return (
    <div className="panel ai-copilot">
      <div className="panel__header">
        <h2 className="panel__title">AI Copilot</h2>
        <span className="panel__subtitle">grounded in live incidents + forecasts</span>
      </div>

      <div className="copilot-messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={`copilot-msg copilot-msg--${m.role}`}>
            <div className="copilot-msg__avatar">
              {m.role === "user" ? <User size={13} /> : <Bot size={13} />}
            </div>
            <div className="copilot-msg__bubble">
              <pre className="copilot-msg__text">{m.text}</pre>
              {m.mode === "offline" && (
                <div className="copilot-msg__mode">offline explainability mode</div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="copilot-msg copilot-msg--assistant">
            <div className="copilot-msg__avatar">
              <Bot size={13} />
            </div>
            <div className="copilot-msg__bubble copilot-msg__bubble--typing">
              <Loader2 size={13} className="spin" /> reasoning…
            </div>
          </div>
        )}
      </div>

      <div className="copilot-suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="copilot-suggestion" onClick={() => send(s)} disabled={busy}>
            {s}
          </button>
        ))}
      </div>

      <form
        className="copilot-input"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about the system…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          <Send size={14} />
        </button>
      </form>
    </div>
  );
}
