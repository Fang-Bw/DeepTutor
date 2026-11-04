"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/ui/Button";
import { apiFetch, apiUrl } from "@/lib/api";
import { codexErrorMessageKey } from "@/lib/codex-oauth";
import { httpErrorDetail } from "@/lib/http-error-detail";

type ProbeResult = {
  ok: boolean;
  checks: Array<{
    target: "authorization" | "models";
    status: "reachable" | "blocked" | "error";
    http_status?: number;
    error_code?: string;
  }>;
};

export default function CodexNetworkProbe({ proxyUrl }: { proxyUrl: string }) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setResult(null);
    setError(null);
    setPending(false);
    return () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, [proxyUrl]);

  const probe = async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const response = await apiFetch(apiUrl("/api/settings/network/codex-test"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codex_proxy_url: proxyUrl }),
        signal: controller.signal,
      });
      const data: unknown = await response.json().catch(() => null);
      if (controller.signal.aborted) return;
      if (!response.ok) throw new Error(httpErrorDetail(data, t("codex.network.testFailed")));
      if (!data || typeof data !== "object" || !Array.isArray((data as ProbeResult).checks)) {
        throw new Error(t("codex.network.testFailed"));
      }
      setResult(data as ProbeResult);
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(error instanceof Error ? error.message : t("codex.network.testFailed"));
      }
    } finally {
      if (controllerRef.current === controller) setPending(false);
    }
  };

  return (
    <div className="space-y-3 pb-3">
      <Button type="button" onClick={() => void probe()} disabled={pending}>
        {pending ? t("codex.network.testing") : t("codex.network.test")}
      </Button>
      <p className="text-xs text-[var(--muted-foreground)]">{t("codex.network.testHint")}</p>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      {result && (
        <div role="status" className="space-y-1 text-sm">
          {result.checks.map((check) => (
            <p key={check.target}>
              {t(check.target === "authorization" ? "codex.network.authorization" : "codex.network.models")}
              {": "}
              {check.status === "reachable"
                ? t("codex.network.reachable")
                : t(codexErrorMessageKey(check.error_code || null))}
              {check.http_status != null && ` (HTTP ${check.http_status})`}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
