import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import CodexNetworkProbe from "@/components/settings/CodexNetworkProbe";
import { httpErrorDetail } from "@/lib/http-error-detail";
import { codexErrorMessageKey } from "@/lib/codex-oauth";

const fetchMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiUrl: (path: string) => path, apiFetch: fetchMock }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

beforeEach(() => fetchMock.mockReset());

it("extracts FastAPI validation messages without echoing their input", () => {
  expect(httpErrorDetail({ detail: [{ msg: "Invalid proxy URL", input: "secret", loc: ["body", "codex_proxy_url"] }] }, "fallback"))
    .toBe("Invalid proxy URL");
  expect(httpErrorDetail({ detail: { message: "Safe message", token: "secret" } }, "fallback")).toBe("Safe message");
  expect(httpErrorDetail({ detail: { input: "secret" } }, "fallback")).toBe("fallback");
});

it("maps actionable OAuth errors instead of generic failure", () => {
  expect(codexErrorMessageKey("unsupported_region")).toBe("codex.oauth.unsupportedRegion");
  expect(codexErrorMessageKey("network_timeout")).toBe("codex.oauth.networkTimeout");
  expect(codexErrorMessageKey("authorization_code_rejected")).toBe("codex.oauth.signInAgain");
  expect(codexErrorMessageKey("token_exchange_failed")).toBe("codex.oauth.exchangeFailed");
});

it("tests the draft URL without saving it and displays reachability", async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true, checks: [
    { target: "authorization", status: "reachable", http_status: 200 },
    { target: "models", status: "reachable", http_status: 401 },
  ] }) });
  render(<CodexNetworkProbe proxyUrl="http://127.0.0.1:7890" />);
  fireEvent.click(screen.getByRole("button", { name: "codex.network.test" }));
  await screen.findByRole("status");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/settings/network/codex-test");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ codex_proxy_url: "http://127.0.0.1:7890" });
  expect(screen.getByRole("status")).toHaveTextContent("HTTP 401");
});

it("discards results for a proxy URL that has changed", async () => {
  let resolve!: (value: unknown) => void;
  fetchMock.mockReturnValue(new Promise((done) => { resolve = done; }));
  const view = render(<CodexNetworkProbe proxyUrl="http://127.0.0.1:7890" />);
  fireEvent.click(screen.getByRole("button", { name: "codex.network.test" }));
  view.rerender(<CodexNetworkProbe proxyUrl="http://127.0.0.1:7891" />);
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  await act(async () => resolve({ ok: true, json: async () => ({ ok: true, checks: [] }) }));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "codex.network.test" })).toBeEnabled());
});

it("shows proxy validation failures in plain text", async () => {
  fetchMock.mockResolvedValue({ ok: false, json: async () => ({ detail: [{ msg: "Use a valid proxy address", input: "secret" }] }) });
  render(<CodexNetworkProbe proxyUrl="invalid" />);
  fireEvent.click(screen.getByRole("button", { name: "codex.network.test" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Use a valid proxy address");
  expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
});
