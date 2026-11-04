"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import { readPreviewBody } from "./readPreviewBody";

const MAX_TEXT_BYTES = 8 * 1024 * 1024; // 8 MB — preview, not a download

export type TextSourceState =
  | { kind: "loading" }
  | { kind: "ready"; text: string }
  | { kind: "error"; message: string };

/**
 * Fetch text at *url*, limiting actual streamed bytes as well as checking
 * Content-Length. Aborts on unmount or source change.
 *
 * If *fallbackText* is provided and the URL is empty/missing, we render
 * that text instead of fetching. This handles the office-doc case where
 * the backend already extracted plain text.
 */
export function useTextSource(
  url: string | null,
  fallbackText?: string,
): TextSourceState {
  const [state, setState] = useState<TextSourceState>(() =>
    !url && fallbackText !== undefined
      ? { kind: "ready", text: fallbackText }
      : { kind: "loading" },
  );
  const reqIdRef = useRef(0);

  useEffect(() => {
    const reqId = ++reqIdRef.current;
    // Inline fallback (no URL): nothing to fetch.
    if (!url) {
      if (fallbackText !== undefined) {
        setState({ kind: "ready", text: fallbackText });
      } else {
        setState({
          kind: "error",
          message: "Preview source is not available.",
        });
      }
      return;
    }

    const controller = new AbortController();
    setState({ kind: "loading" });

    (async () => {
      try {
        const res = await apiFetch(url, { signal: controller.signal });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const buffer = await readPreviewBody(
          res,
          MAX_TEXT_BYTES,
          "File is too large to preview as text. Use the Download button.",
          controller.signal,
        );
        if (controller.signal.aborted || reqIdRef.current !== reqId) return;
        const text = new TextDecoder().decode(buffer);
        setState({ kind: "ready", text });
      } catch (err) {
        if (controller.signal.aborted) return;
        if (reqIdRef.current !== reqId) return;
        const message =
          err instanceof Error ? err.message : "Failed to load preview";
        setState({ kind: "error", message });
      }
    })();

    return () => {
      controller.abort();
    };
  }, [url, fallbackText]);

  return state;
}
