import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Send, Trash2, Bot } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Message { role: string; content: string; }

function FormattedMessage({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let i = 0;

  
  while (i < lines.length) {
    const line = lines[i].trim();

    if (!line) { i++; continue; }

    // Numbered list item
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s/, ""));
        i++;
      }
      elements.push(
        <ol key={i} className="list-decimal list-inside space-y-1 my-2">
          {items.map((item, j) => <li key={j} className="text-sm leading-relaxed">{formatInline(item)}</li>)}
        </ol>
      );
      continue;
    }

    // Bullet list item
    if (/^[-*•]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*•]\s/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*•]\s/, ""));
        i++;
      }
      elements.push(
        <ul key={i} className="list-disc list-inside space-y-1 my-2">
          {items.map((item, j) => <li key={j} className="text-sm leading-relaxed">{formatInline(item)}</li>)}
        </ul>
      );
      continue;
    }

    // Heading
    if (/^#{1,3}\s/.test(line)) {
      elements.push(<p key={i} className="font-semibold text-sm mt-3 mb-1">{line.replace(/^#{1,3}\s/, "")}</p>);
      i++; continue;
    }

    // Regular paragraph
    elements.push(<p key={i} className="text-sm leading-relaxed">{formatInline(line)}</p>);
    i++;
  }

  return <div className="space-y-1">{elements}</div>;
}

function formatInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*.*?\*\*)/g);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part
  );
}

export default function Agent() {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: history } = useQuery({ queryKey: ["agent-history"], queryFn: api.agent.history });

  useEffect(() => {
    if (history) setMessages(history.map(({ role, content }) => ({ role, content })));
  }, [history]);

  const chatMut = useMutation({
    mutationFn: (msg: string) => api.agent.chat(msg),
    onSuccess: (data) => {
      setMessages(prev => [...prev, { role: "assistant", content: data.reply }]);
    },
    onError: () => {
      setMessages(prev => [...prev, { role: "assistant", content: "Sorry, I couldn't get a response. Check the LLM provider settings." }]);
    },
  });

  const clearMut = useMutation({
    mutationFn: api.agent.clearHistory,
    onSuccess: () => {
      setMessages([]);
      qc.setQueryData(["agent-history"], []);
    },
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chatMut.isPending]);

  const send = () => {
    const msg = input.trim();
    if (!msg || chatMut.isPending) return;
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: msg }]);
    chatMut.mutate(msg);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Bot size={20} className="text-pharma-light" />
          <h1 className="text-2xl font-bold text-pharma-blue dark:text-[#e2e8f0]">Hermes AI</h1>
        </div>
        <button
          onClick={() => clearMut.mutate()}
          className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-red-500"
        >
          <Trash2 size={13} /> Clear history
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-1 mb-4">
        {!messages.length && (
          <div className="text-center py-12 text-gray-400 text-sm">
            Ask Hermes AI about KOL activity, recent insights, or competitive intelligence.
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={cn(
              "max-w-3xl rounded-xl px-4 py-3 text-sm",
              msg.role === "user"
                ? "ml-auto bg-pharma-blue text-white"
                : "glass-panel text-slate-800 dark:text-slate-200"
            )}
          >
            {msg.role === "user"
              ? <p className="text-sm leading-relaxed">{msg.content}</p>
              : <FormattedMessage content={msg.content} />
            }
          </div>
        ))}
        {chatMut.isPending && (
          <div className="max-w-3xl glass-panel rounded-xl px-4 py-3">
            <div className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <span key={i} className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />
              ))}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
          placeholder="Ask about KOLs, recent findings, competitive intelligence..."
          className="flex-1 px-4 py-2.5 border border-slate-200 dark:border-white/10 rounded-xl text-sm glass focus:outline-none focus:ring-2 focus:ring-pharma-light placeholder:text-slate-400"
        />
        <button
          onClick={send}
          disabled={!input.trim() || chatMut.isPending}
          className="px-4 py-2.5 bg-pharma-blue text-white rounded-xl hover:bg-pharma-light disabled:opacity-50 transition-colors"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}
