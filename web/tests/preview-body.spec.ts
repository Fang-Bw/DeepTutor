import { describe, expect, it, vi } from "vitest";
import { readPreviewBody } from "@/components/chat/preview/previewers/readPreviewBody";

function streamResponse(chunks: Uint8Array[], contentLength?: string) {
  let index = 0;
  const cancel = vi.fn();
  const pull = vi.fn((controller: ReadableStreamDefaultController<Uint8Array>) => {
    if (index < chunks.length) controller.enqueue(chunks[index++]);
    else controller.close();
  });
  const body = new ReadableStream({ pull, cancel }, { highWaterMark: 0 });
  const response = new Response(body, {
    headers: contentLength === undefined ? {} : { "content-length": contentLength },
  });
  return { response, body, cancel, pull };
}

describe("bounded preview body reading", () => {
  it.each([undefined, "1", "not-a-number"])(
    "stops an oversized stream even with Content-Length %s",
    async (length) => {
      const stream = streamResponse([
        new Uint8Array([1, 2, 3]),
        new Uint8Array([4, 5, 6]),
        new Uint8Array([7]),
      ], length);
      await expect(readPreviewBody(stream.response, 5, "Too large", new AbortController().signal))
        .rejects.toThrow("Too large");
      expect(stream.pull).toHaveBeenCalledTimes(2);
      expect(stream.cancel).toHaveBeenCalledTimes(1);
      expect(stream.body.locked).toBe(false);
    },
  );

  it("rejects a declared oversized body before reading, and cancels it", async () => {
    const stream = streamResponse([new Uint8Array([1])], "100");
    await expect(readPreviewBody(stream.response, 5, "Too large", new AbortController().signal))
      .rejects.toThrow("Too large");
    expect(stream.pull).not.toHaveBeenCalled();
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it("preserves bytes at the exact limit, including UTF-8 split across chunks", async () => {
    const bytes = new TextEncoder().encode("你好 🌍");
    const stream = streamResponse([bytes.slice(0, 1), bytes.slice(1, 5), bytes.slice(5)]);
    const buffer = await readPreviewBody(stream.response, bytes.length, "Too large", new AbortController().signal);
    expect(new Uint8Array(buffer)).toEqual(bytes);
    expect(new TextDecoder().decode(buffer)).toBe("你好 🌍");
    expect(stream.cancel).not.toHaveBeenCalled();
    expect(stream.body.locked).toBe(false);
  });

  it("cancels a pending read on abort and releases its lock", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel });
    const controller = new AbortController();
    const pending = readPreviewBody(new Response(body), 5, "Too large", controller.signal);
    const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await rejection;
    expect(cancel).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });

  it("preserves the size error if stream cancellation itself fails", async () => {
    const body = new ReadableStream<Uint8Array>({
      pull(controller) { controller.enqueue(new Uint8Array(10)); },
      cancel() { return Promise.reject(new Error("Cancel failed")); },
    }, { highWaterMark: 0 });
    await expect(readPreviewBody(new Response(body), 5, "Too large", new AbortController().signal))
      .rejects.toThrow("Too large");
    expect(body.locked).toBe(false);
  });
});
