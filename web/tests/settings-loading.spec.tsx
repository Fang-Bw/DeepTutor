import React, { useEffect } from "react";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SettingsProvider, defaultCatalog, useSettings } from "@/features/settings/store/SettingsStore";

const mocks = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiUrl: (url: string) => url, apiFetch: (...args: unknown[]) => mocks.fetch(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const t = (key: string) => key;
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/context/AppShellContext", () => ({ useAppShell: () => ({ codeBlockTheme: "github", codeBlockShowLineNumbers: false, codeBlockWrapLongLines: false }) }));
vi.mock("@/lib/llm-options", () => ({ invalidateLLMOptionsCache: vi.fn() }));

let settings: ReturnType<typeof useSettings>;
function Capture() {
  const value = useSettings();
  useEffect(() => { settings = value; }, [value]);
  return null;
}
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const reply = (value: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => value });
const livePayload = () => ({ catalog: defaultCatalog(), ui: { theme: "snow", language: "en" } });
beforeEach(() => { mocks.fetch.mockReset(); });

it("starts all three reads before settings resolves, then lets the stored draft override live settings", async () => {
  const live = deferred();
  const draft = deferred();
  const status = deferred();
  mocks.fetch.mockImplementation((url: string) => ({ "/api/settings": live.promise, "/api/settings/draft": draft.promise, "/api/system/status": status.promise })[url]);
  render(<SettingsProvider><Capture /></SettingsProvider>);
  expect(mocks.fetch.mock.calls.map(([url]) => url)).toEqual(["/api/settings", "/api/settings/draft", "/api/system/status"]);
  const stored = defaultCatalog();
  stored.connections = [{ id: "draft-only", name: "Saved provider", provider: "custom", api_key: "***", base_url: "", api_version: "" }];
  await act(async () => {
    draft.resolve(reply({ draft: { catalog: stored, extensions: {} } }));
    status.resolve(reply({ marker: "ready" }));
  });
  expect(settings.catalogEditable).toBeNull();
  await act(async () => { live.resolve(reply(livePayload())); });
  await waitFor(() => expect(settings.settingsLoading).toBe(false));
  expect(settings.draft.connections?.[0].id).toBe("draft-only");
  expect(settings.catalog.connections).toEqual([]);
  expect(settings.draftState).toBe("saved");
});

it("keeps live settings usable when draft and diagnostics fail early", async () => {
  const live = deferred();
  mocks.fetch.mockImplementation((url: string) => url === "/api/settings" ? live.promise : Promise.reject(new Error("offline")));
  render(<SettingsProvider><Capture /></SettingsProvider>);
  await act(async () => { live.resolve(reply(livePayload())); });
  await waitFor(() => expect(settings.catalogEditable).toBe(true));
  expect(settings.draft).toEqual(defaultCatalog());
  expect(settings.settingsError).toBe("System status unavailable: {{message}}");
});

it("waits for a delayed draft before opening the settings editor", async () => {
  const draft = deferred();
  mocks.fetch.mockImplementation(async (url: string) => url === "/api/settings/draft" ? draft.promise : reply(url === "/api/settings" ? livePayload() : {}));
  render(<SettingsProvider><Capture /></SettingsProvider>);
  await act(async () => { await Promise.resolve(); });
  expect(settings.settingsLoading).toBe(true);
  expect(settings.catalogEditable).toBeNull();
  await act(async () => { draft.resolve(reply({ draft: null })); });
  await waitFor(() => expect(settings.catalogEditable).toBe(true));
});

it("does not hydrate a stored draft after the main settings read fails", async () => {
  const stored = defaultCatalog();
  stored.connections = [{ id: "secret", name: "Draft", provider: "custom", api_key: "***", base_url: "", api_version: "" }];
  mocks.fetch.mockImplementation(async (url: string) => url === "/api/settings" ? reply({}, false) : url === "/api/settings/draft" ? reply({ draft: { catalog: stored } }) : reply({}));
  render(<SettingsProvider><Capture /></SettingsProvider>);
  await waitFor(() => expect(settings.settingsLoading).toBe(false));
  expect(settings.catalogEditable).toBe(false);
  expect(settings.draft.connections).toEqual([]);
  expect(settings.settingsError).toBe("Settings fetch failed: HTTP 503");
});
