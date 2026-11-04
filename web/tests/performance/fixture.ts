import type { Page, WebSocketRoute } from "@playwright/test";
import { emptyTaskBoard } from "../e2e/fixtures/task-board";
import { historyMessages, SESSION_ID } from "./scenarios";

export async function installFixture(page: Page, workbook?: Buffer) {
  const unknownPaths = new Set<string>();
  let frontendOrigin: string | null = null;
  let resolveSocket!: (socket: WebSocketRoute) => void;
  const submitted = new Promise<WebSocketRoute>((resolve) => { resolveSocket = resolve; });
  await page.addInitScript(() => {
    localStorage.setItem("deeptutor-language", "en");
    localStorage.setItem("deeptutor-theme", "light");
  });
  // This interception never connects to the backend. Even unexpected commands
  // remain local and cannot start a billable model call.
  await page.routeWebSocket(/.*/, (socket) => {
    // Next's dev bootstrap needs its HMR handshake before hydration. Keep
    // only that frontend-owned socket live; application sockets remain fake.
    const socketUrl = new URL(socket.url());
    if (socketUrl.pathname === "/_next/webpack-hmr"
        && socketUrl.host === new URL(frontendOrigin || page.url()).host) {
      socket.connectToServer();
      return;
    }
    socket.onMessage((raw) => {
      const command = JSON.parse(String(raw));
      if (command.type === "ping") socket.send(JSON.stringify({ type: "pong", protocol_version: "2.0" }));
      if (command.command_id) {
        socket.send(JSON.stringify({
          type: "command_ack", protocol_version: "2.0", accepted: true,
          command_id: command.command_id, command_type: command.type,
        }));
      }
      if (command.type === "start_turn") resolveSocket(socket);
    });
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!frontendOrigin && request.isNavigationRequest() && request.frame() === page.mainFrame()) {
      frontendOrigin = url.origin;
    }
    const json = (payload: unknown, status = 200) => route.fulfill({ status, json: payload });
    if (path === "/files/attachments/benchmark.xlsx" && workbook) {
      return route.fulfill({ body: workbook, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    }
    if (path.startsWith("/api/")) {
      if (path === "/api/auth/status") return json({ enabled: false, authenticated: true, role: "admin", is_admin: true });
      if (path === "/api/settings/ui") return json({ language: "en", response_language: "en" });
      if (path === "/api/settings/workspace/registrations") return json({ workspaces: [] });
      if (path === "/api/settings/workspace/resources") return json({ skills: [], mcp: [], knowledge_bases: [] });
      if (path === "/api/capabilities/registered") return json({ capabilities: ["chat"] });
      if (path === "/api/settings/llm-options") return json({
        active: { profile_id: "synthetic", model_id: "synthetic" },
        options: [{ profile_id: "synthetic", model_id: "synthetic", profile_name: "Fixture", model_name: "Fixture", model: "fixture", provider: "fixture", is_active_default: true }],
      });
      if (path === "/api/task-board") return json(emptyTaskBoard);
      // HTTP 204 explicitly stops EventSource reconnects. The synthetic task
      // board never changes; returning JSON here creates unrelated errors.
      if (path === "/api/task-board/events") return route.fulfill({ status: 204 });
      if (path === "/api/dashboard/suggestions") return json({ suggestions: [], stale: false });
      if (path === "/api/sessions") return json({ sessions: [] });
      if (path === `/api/sessions/${SESSION_ID}`) {
        const messages = historyMessages();
        const attachments = workbook ? [{
          id: "synthetic-workbook", filename: "benchmark.xlsx", title: "Benchmark workbook",
          url: "/files/attachments/benchmark.xlsx", type: "file", generated: true,
          mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size_bytes: workbook.length,
        }] : [];
        return json({
          id: SESSION_ID, session_id: SESSION_ID, title: "Synthetic benchmark",
          created_at: 1, updated_at: 2, status: "idle", active_turns: [],
          preferences: { capability: "chat" },
          messages: messages.map((message, i) => ({ ...message, attachments: i === messages.length - 1 ? attachments : [] })),
        });
      }
      if (path === "/api/file-preview/pdf") return json({ detail: "Synthetic fixture forces browser fallback" }, 503);
      if (path === "/api/partners" || path === "/api/partner-groups" || path === "/api/workspaces" || path === "/api/knowledge-bases" || path === "/api/agents" || path === "/api/personas" || path === "/api/skills") return json([]);
      unknownPaths.add(path);
      return json({});
    }
    // Only application assets from the selected local frontend are permitted.
    // Fail closed for third-party analytics, fonts, search, and all file URLs.
    if (url.origin !== frontendOrigin || path.startsWith("/files/")) {
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  return { submitted, unknownPaths };
}
