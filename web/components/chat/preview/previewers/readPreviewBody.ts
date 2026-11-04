/**
 * Read a preview without trusting Content-Length (which may be absent, refer
 * to compressed bytes, or underreport the body). Never buffer past maxBytes.
 * Cancelling the reader also stops pending reads when the preview is closed.
 */
export async function readPreviewBody(
  response: Response,
  maxBytes: number,
  tooLargeMessage: string,
  signal: AbortSignal,
): Promise<ArrayBuffer> {
  const reader = response.body?.getReader();
  let cancellation: Promise<void> | undefined;
  const cancel = (reason?: unknown) => {
    cancellation ??= reader?.cancel(reason).catch(() => undefined) ?? Promise.resolve();
    return cancellation;
  };
  const onAbort = () => { void cancel(signal.reason); };
  signal.addEventListener("abort", onAbort, { once: true });

  try {
    signal.throwIfAborted();
    const declaredLength = Number(response.headers.get("content-length"));
    if (declaredLength > maxBytes) throw new Error(tooLargeMessage);
    if (!reader) return new ArrayBuffer(0);

    const chunks: Uint8Array[] = [];
    let byteLength = 0;
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      byteLength += value.byteLength;
      if (byteLength > maxBytes) throw new Error(tooLargeMessage);
      chunks.push(value);
    }

    const bytes = new Uint8Array(byteLength);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes.buffer;
  } catch (error) {
    await cancel(error);
    throw error;
  } finally {
    signal.removeEventListener("abort", onAbort);
    reader?.releaseLock();
  }
}
