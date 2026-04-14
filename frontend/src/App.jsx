import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

const STORAGE_KEY = "kairos_chats_v2";

function loadChats() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    }

    // Migration from older history format, if present.
    const legacyRaw = localStorage.getItem("kairos_history_v1");
    if (!legacyRaw) return [];
    const legacy = JSON.parse(legacyRaw);
    if (!Array.isArray(legacy)) return [];

    return legacy.map((h) => {
      const chatId = h.id || crypto.randomUUID();
      const createdAt = h.createdAt || Date.now();
      const issue = h.issue || h.title || "";
      return {
        id: chatId,
        title: issue ? issue.slice(0, 48) : "New chat",
        createdAt,
        updatedAt: createdAt,
        messages: [
          { id: crypto.randomUUID(), role: "user", content: issue, createdAt },
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: h.markdown || "",
            createdAt: (h.createdAt || Date.now()) + 1,
            meta: {
              confidence: h.confidence,
              insufficient: h.insufficient,
              related: h.related || [],
              reportPath: h.reportPath,
              sourceIssue: issue,
            },
          },
        ],
      };
    });
  } catch {
    return [];
  }
}

function saveChats(items) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
}

const INITIAL_CHATS = loadChats();

function makeChatTitle(text) {
  const t = (text || "").trim().replace(/\s+/g, " ");
  if (!t) return "New chat";
  return t.length > 48 ? `${t.slice(0, 48)}…` : t;
}

function formatSidebarTime(ts) {
  const d = new Date(ts || Date.now());
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function App() {
  const [chats, setChats] = useState(INITIAL_CHATS);
  const [activeChatId, setActiveChatId] = useState(INITIAL_CHATS[0]?.id ?? null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    saveChats(chats);
  }, [chats]);

  const activeChat = useMemo(() => chats.find((c) => c.id === activeChatId) || null, [chats, activeChatId]);

  const latestAssistant = useMemo(() => {
    if (!activeChat?.messages?.length) return null;
    for (let i = activeChat.messages.length - 1; i >= 0; i -= 1) {
      const m = activeChat.messages[i];
      if (m.role === "assistant") return m;
    }
    return null;
  }, [activeChat]);

  const canUseReportActions = Boolean(latestAssistant?.content);

  function ensureActiveChat() {
    if (activeChat) return activeChat;
    const id = crypto.randomUUID();
    const now = Date.now();
    const next = {
      id,
      title: "New chat",
      createdAt: now,
      updatedAt: now,
      messages: [],
    };
    setChats((prev) => [next, ...prev]);
    setActiveChatId(id);
    return next;
  }

  async function submitIssue() {
    const issue = input.trim();
    if (!issue || loading) return;
    setError("");
    setLoading(true);

    const chat = ensureActiveChat();
    const now = Date.now();
    const userMsg = { id: crypto.randomUUID(), role: "user", content: issue, createdAt: now };
    setChats((prev) =>
      prev.map((c) =>
        c.id === chat.id
          ? {
              ...c,
              title: c.messages.length === 0 ? makeChatTitle(issue) : c.title,
              updatedAt: now,
              messages: [...c.messages, userMsg],
            }
          : c,
      ),
    );
    setInput("");
    setSidebarOpen(false);

    try {
      const res = await fetch("/api/investigate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ issue }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || res.statusText);
      }
      const data = await res.json();

      const assistantMsg = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: data.markdown || "",
        createdAt: Date.now(),
        meta: {
          confidence: data.confidence_score,
          insufficient: data.insufficient_data,
          related: data.related_incidents || [],
          reportPath: data.report_path,
          sourceIssue: issue,
        },
      };

      setChats((prev) =>
        prev.map((c) => (c.id === chat.id ? { ...c, updatedAt: Date.now(), messages: [...c.messages, assistantMsg] } : c)),
      );
    } catch (e) {
      setError(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  function downloadMd() {
    if (!latestAssistant?.content) return;
    const blob = new Blob([latestAssistant.content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "kairos_incident_report.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function createPr() {
    if (!latestAssistant?.content) return;
    setError("");
    try {
      const res = await fetch("/api/create-pr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: `KAIROS: ${(latestAssistant.meta?.sourceIssue || "Incident").slice(0, 72)}`,
          markdown: latestAssistant.content,
          filename: `reports/kairos_chat_${activeChatId || "unknown"}.md`,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || JSON.stringify(data) || res.statusText);
      }
      if (data.pull_request_url) {
        window.open(data.pull_request_url, "_blank", "noopener,noreferrer");
      }
    } catch (e) {
      setError(e.message || "PR creation failed (configure GitHub env on backend)");
    }
  }

  const empty = !activeChat || activeChat.messages.length === 0;
  const activeSubtitle = activeChat ? activeChat.title : "New chat";

  function onSelectChat(id) {
    setActiveChatId(id);
    setSidebarOpen(false);
  }

  function newChat() {
    const id = crypto.randomUUID();
    const now = Date.now();
    const next = { id, title: "New chat", createdAt: now, updatedAt: now, messages: [] };
    setChats((prev) => [next, ...prev]);
    setActiveChatId(id);
    setSidebarOpen(false);
    setError("");
  }

  function clearAllChats() {
    setChats([]);
    setActiveChatId(null);
    setSidebarOpen(false);
  }

  return (
    <div className="cgShell">
      {sidebarOpen && <button type="button" className="drawerScrim" aria-label="Close chats" onClick={() => setSidebarOpen(false)} />}

      <aside className={`cgSidebar ${sidebarOpen ? "cgSidebarOpen" : ""}`} aria-label="Chats sidebar">
        <div className="cgSidebarTop">
          <div className="cgBrand">
            <div className="cgBrandDot" aria-hidden />
            <div className="cgBrandText">
              <div className="cgBrandName">Kairos</div>
              <div className="cgBrandSub">Incident assistant</div>
            </div>
          </div>

          <div className="cgSidebarButtons">
            <button type="button" className="cgBtn" onClick={newChat}>
              New chat
            </button>
            <button type="button" className="cgIconBtn" onClick={clearAllChats} disabled={chats.length === 0} aria-label="Clear all chats" title="Clear all chats">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M4 6h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <path d="M9 6V4.7c0-.9.7-1.7 1.7-1.7h2.6c1 0 1.7.8 1.7 1.7V6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <path d="M7 6l1 15c.1 1 1 2 2.2 2h3.6c1.2 0 2.1-1 2.2-2l1-15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button type="button" className="cgIconBtn cgMobileOnly" onClick={() => setSidebarOpen(false)} aria-label="Close sidebar">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M7 7l10 10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <path d="M17 7L7 17" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        </div>

        <div className="cgSidebarList" role="list">
          {chats.length === 0 ? (
            <div className="cgSidebarEmpty">No chats yet. Click “New chat” to begin.</div>
          ) : (
            chats.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`cgChatRow ${c.id === activeChatId ? "cgChatRowActive" : ""}`}
                onClick={() => onSelectChat(c.id)}
                role="listitem"
              >
                <div className="cgChatRowTitle">{c.title || "New chat"}</div>
                <div className="cgChatRowMeta">{formatSidebarTime(c.updatedAt || c.createdAt)}</div>
              </button>
            ))
          )}
        </div>
      </aside>

      <main className="cgMain">
        <header className="cgTopbar">
          <button type="button" className="cgIconBtn cgMobileOnly" onClick={() => setSidebarOpen(true)} aria-label="Open sidebar">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M4 7h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              <path d="M4 12h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              <path d="M4 17h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
          <div className="cgTopbarTitle" title={activeSubtitle}>
            {activeSubtitle}
          </div>
          <div className="cgTopbarActions">
            <button type="button" className="cgTopAction" onClick={downloadMd} disabled={!canUseReportActions} title="Download last report as Markdown">
              Download
            </button>
            <button type="button" className="cgTopAction cgTopActionPrimary" onClick={createPr} disabled={!canUseReportActions} title="Create a pull request from last report">
              Raise PR
            </button>
          </div>
        </header>

        <div className="cgThread">
          <div className="cgThreadInner">
            {empty ? (
              <div className="cgEmptyState">
                <div className="cgEmptyTitle">How can I help?</div>
                <div className="cgEmptySub">Describe an incident (symptom, environment, time window). I’ll generate a structured report with source references.</div>
              </div>
            ) : (
              activeChat.messages.map((m) => {
                const isUser = m.role === "user";
                const confidence =
                  m.role === "assistant" && typeof m.meta?.confidence === "number" ? m.meta.confidence.toFixed(2) : null;
                return (
                  <div key={m.id} className={`cgMsg ${isUser ? "cgMsgUser" : "cgMsgAssistant"}`}>
                    <div className="cgMsgAvatar" aria-hidden>
                      {isUser ? "U" : "K"}
                    </div>
                    <div className="cgMsgBody">
                      <div className="cgMsgHeader">
                        <div className="cgMsgRole">{isUser ? "You" : "Kairos"}</div>
                        <div className="cgMsgMeta">
                          {confidence ? `Confidence ${confidence}` : ""}
                          {m.role === "assistant" && m.meta?.insufficient ? " · Needs more evidence" : ""}
                        </div>
                      </div>
                      <div className={`cgMsgContent ${isUser ? "cgMsgText" : "cgMsgMarkdown"}`}>
                        {isUser ? m.content : <ReactMarkdown>{m.content || ""}</ReactMarkdown>}
                      </div>
                      {m.role === "assistant" && Array.isArray(m.meta?.related) && m.meta.related.length > 0 && (
                        <div className="cgCitations">
                          {m.meta.related.map((r, idx) => (
                            <span key={idx} className="cgCitation" title={r.link || r.title}>
                              {r.citation_label || `${r.source} · line ${r.line}`}
                            </span>
                          ))}
                        </div>
                      )}
                      {m.role === "assistant" && m.meta?.reportPath && <div className="cgReportPath">Saved: {m.meta.reportPath}</div>}
                    </div>
                  </div>
                );
              })
            )}

            {error && <div className="cgError">{error}</div>}
          </div>
        </div>

        <div className="cgComposer">
          <div className="cgComposerInner" role="group" aria-label="Message composer">
            <input
              className="cgInput"
              placeholder="Message Kairos…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submitIssue();
                }
              }}
              disabled={loading}
            />
            <button type="button" className="cgSend" onClick={submitIssue} disabled={loading || !input.trim()} aria-label="Send">
              {loading ? "…" : "Send"}
            </button>
          </div>
          <div className="cgComposerHint">
            Brand accents: <span className="cgSwatch cgSwatchSky" aria-hidden /> <span className="cgSwatch cgSwatchSun" aria-hidden />
          </div>
        </div>
      </main>
    </div>
  );
}
