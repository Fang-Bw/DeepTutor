/** Extract only public error messages, never validation inputs or raw bodies. */
export function httpErrorDetail(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item: unknown) => {
      if (!item || typeof item !== "object") return [];
      const msg = (item as { msg?: unknown }).msg;
      return typeof msg === "string" && msg.trim() ? [msg] : [];
    });
    return messages.length ? [...new Set(messages)].join("; ") : fallback;
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return fallback;
}
