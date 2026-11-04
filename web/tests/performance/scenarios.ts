import type { StreamEvent } from "../../features/chat/model/protocol";

export const SESSION_ID = "runtime-benchmark-synthetic";
export const FINAL_MARKER = "BENCHMARK_RENDER_COMPLETE";
export const HISTORY_PAIRS = 20;
export const CHUNK_CHARS = 200;
export const CHUNK_INTERVAL_MS = 16;

export type ScenarioName = "long-markdown" | "math" | "mermaid" | "tool-events";

export function scenarioContent(name: ScenarioName): string {
  const blocks: Record<ScenarioName, string[]> = {
    "long-markdown": Array.from({ length: 80 }, (_, i) =>
      `### Topic ${i + 1}\n\nA deterministic explanation of retrieval and learning. ` +
      "The same synthetic paragraph is used in every sample to compare rendering. ".repeat(4) +
      "\n\n- Evidence from the source\n- A concrete example\n- A follow-up exercise\n\n",
    ),
    math: Array.from({ length: 60 }, (_, i) =>
      `### Equation ${i + 1}\n\nInline $a^2+b^2=c^2$ and $f(x)=x^2$.\n\n` +
      "$$\n\\int_0^1 x^2\\,dx=\\frac{1}{3}\n$$\n\n",
    ),
    mermaid: Array.from({ length: 8 }, (_, i) =>
      `### Diagram ${i + 1}\n\n\`\`\`mermaid\nflowchart LR\n` +
      `A${i}[Question] --> B${i}[Retrieve]\nB${i} --> C${i}[Explain]\n` +
      `C${i} --> D${i}[Practice]\nD${i} --> A${i}\n\`\`\`\n\n`,
    ),
    "tool-events": ["The synthetic tools have completed.\n\n"],
  };
  return blocks[name].join("") + FINAL_MARKER;
}

export function scenarioEvents(name: ScenarioName): StreamEvent[] {
  const events: StreamEvent[] = [];
  const emit = (type: StreamEvent["type"], content = "", metadata: Record<string, unknown> = {}) => {
    events.push({
      type, content, metadata, source: "chat", stage: "responding",
      session_id: SESSION_ID, turn_id: "benchmark-turn",
      seq: events.length + 1, timestamp: 1_700_000_000 + events.length / 100,
      protocol_version: "2.0",
    });
  };
  emit("session", "", { session_id: SESSION_ID, turn_id: "benchmark-turn" });
  if (name === "tool-events") {
    for (let i = 0; i < 80; i += 1) {
      const shared = {
        tool: "web_search", tool_name: "web_search", tool_call_id: `call-${i}`,
        // Rendering groups by call_id; tool_call_id also correlates the
        // dispatched tool result. Real trace events carry both identities.
        call_id: `call-${i}`,
        tool_source: "builtin", trace_role: "tool", trace_group: "tool_call",
        call_kind: "tool_planning", round: i + 1,
      };
      emit("tool_call", "web_search", {
        ...shared, trace_kind: "tool_call", args: { query: `Synthetic topic ${i}` },
      });
      emit("tool_result", `Synthetic evidence ${i}: no external search was performed.`, {
        ...shared, trace_kind: "tool_result", success: true,
      });
    }
  }
  const content = scenarioContent(name);
  for (let offset = 0; offset < content.length; offset += CHUNK_CHARS) {
    emit("content", content.slice(offset, offset + CHUNK_CHARS));
  }
  emit("done", "", {
    status: "completed", user_message_id: HISTORY_PAIRS * 2 + 1,
    assistant_message_id: HISTORY_PAIRS * 2 + 2,
  });
  return events;
}

export function historyMessages() {
  return Array.from({ length: HISTORY_PAIRS * 2 }, (_, i) => ({
    id: i + 1, session_id: SESSION_ID, parent_message_id: i === 0 ? null : i,
    role: i % 2 === 0 ? "user" : "assistant", capability: "chat",
    content: i % 2 === 0 ? `Synthetic question ${i / 2 + 1}` : `Synthetic answer ${(i + 1) / 2}`,
    events: [], attachments: [], metadata: {}, created_at: i + 1,
  }));
}
