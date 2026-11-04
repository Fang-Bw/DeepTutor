import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api";
import { useTextSource } from "@/components/chat/preview/previewers/useTextSource";
import { useBinarySource } from "@/components/chat/preview/previewers/useBinarySource";

vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

function oversizedResponse(limit: number) {
  let bytes = 0;
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (bytes > limit) { controller.close(); return; }
      const chunk = new Uint8Array(Math.min(1024 * 1024, limit + 1 - bytes));
      bytes += chunk.length;
      controller.enqueue(chunk);
    },
    cancel,
  }, { highWaterMark: 0 });
  return { response: new Response(body), body, cancel, bytes: () => bytes };
}

describe("preview source hooks", () => {
  it("applies the 8 MiB text limit without a size header", async () => {
    const stream = oversizedResponse(8 * 1024 * 1024);
    vi.mocked(apiFetch).mockResolvedValueOnce(stream.response);
    const { result } = renderHook(() => useTextSource("/text"));
    await waitFor(() => expect(result.current).toEqual({
      kind: "error",
      message: "File is too large to preview as text. Use the Download button.",
    }));
    expect(stream.bytes()).toBe(8 * 1024 * 1024 + 1);
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it("applies the 25 MiB binary limit without a size header", async () => {
    const stream = oversizedResponse(25 * 1024 * 1024);
    vi.mocked(apiFetch).mockResolvedValueOnce(stream.response);
    const { result } = renderHook(() => useBinarySource("/office"));
    await waitFor(() => expect(result.current).toEqual({
      kind: "error",
      message: "File is too large to preview. Use the Download button.",
    }));
    expect(stream.bytes()).toBe(25 * 1024 * 1024 + 1);
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it("decodes text and preserves the inline fallback", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response("你好\nhello"));
    const initialProps: { url: string | null; fallback?: string } = { url: "/text" };
    const { result, rerender } = renderHook(({ url, fallback }) =>
      useTextSource(url, fallback), { initialProps });
    await waitFor(() => expect(result.current).toEqual({ kind: "ready", text: "你好\nhello" }));
    rerender({ url: null, fallback: "extracted text" });
    expect(result.current).toEqual({ kind: "ready", text: "extracted text" });
    expect(apiFetch).toHaveBeenCalledOnce();
  });

  it("cancels an old text stream without overwriting the new fallback", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel });
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(body));
    const initialProps: { url: string | null } = { url: "/old" };
    const { result, rerender } = renderHook(({ url }) =>
      useTextSource(url, "fallback"), { initialProps });
    await waitFor(() => expect(body.locked).toBe(true));
    await act(async () => { rerender({ url: null }); });
    expect(result.current).toEqual({ kind: "ready", text: "fallback" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });

  it("cancels a pending binary body on unmount", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel });
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(body));
    const { unmount } = renderHook(() => useBinarySource("/binary"));
    await waitFor(() => expect(body.locked).toBe(true));
    await act(async () => { unmount(); });
    expect(cancel).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });

  it("preserves binary data and revokes the optional object URL on unmount", async () => {
    const createObjectURL = vi.fn(() => "blob:preview");
    const revokeObjectURL = vi.fn();
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    try {
      vi.mocked(apiFetch).mockResolvedValueOnce(new Response(new Uint8Array([0, 127, 255])));
      const { result, unmount } = renderHook(() => useBinarySource("/binary", "application/pdf"));
      await waitFor(() => expect(result.current.kind).toBe("ready"));
      if (result.current.kind !== "ready") throw new Error("Expected ready preview");
      expect(new Uint8Array(result.current.buffer)).toEqual(new Uint8Array([0, 127, 255]));
      expect(result.current.objectUrl).toBe("blob:preview");
      expect(createObjectURL).toHaveBeenCalledOnce();
      unmount();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:preview");
    } finally {
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    }
  });
});
