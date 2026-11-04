"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSettings } from "@/features/settings/store/SettingsStore";
import type { Catalog } from "@/lib/model-catalog-types";
import { flattenModels, providerRegistry, providerServiceSupport } from "@/lib/provider-registry";
import { registryButton } from "./RegistryControls";

function activeModel(catalog: Catalog) {
  const service = catalog.services.llm;
  return flattenModels(catalog, ["llm"]).find(
    ({ profile, model }) => profile.id === service.active_profile_id &&
      model?.id === service.active_model_id && Boolean(model?.model.trim()),
  );
}

/** Explain the existing draft workflow without making configuration choices. */
export function ModelSetupGuide({ onAddModel }: { onAddModel?: () => void }) {
  const { t } = useTranslation();
  const { catalog, draft, providers, connectionTargets, catalogEditable, draftState } = useSettings();
  const liveReady = Boolean(activeModel(catalog));
  // Workspaces mount this guide after the catalog loading gate is resolved.
  const [startedHere] = useState(() => !liveReady);
  if (!catalogEditable || (liveReady && !startedHere)) return null;

  const hasProvider = providerRegistry(draft).some(
    (source) => providerServiceSupport(source, "llm", connectionTargets, providers).enabled,
  );
  const models = flattenModels(draft, ["llm"]);
  const hasModel = models.some(({ model }) => Boolean(model?.model.trim()));
  const hasDefault = Boolean(activeModel(draft));
  const applied = liveReady && draftState === "clean";
  const step = applied ? 4 : !hasProvider ? 0 : !hasModel ? 1 : !hasDefault ? 2 : 3;
  const labels = [
    t("settings.modelSetup.provider"),
    t("settings.modelSetup.model"),
    t("settings.modelSetup.default"),
    t("settings.modelSetup.apply"),
  ];
  const hint = applied ? t("settings.modelSetup.ready")
    : step === 0 ? t("settings.modelSetup.providerHint")
    : step === 1 ? t("settings.modelSetup.modelHint")
    : step === 2 ? t("settings.modelSetup.defaultHint")
    : t("settings.modelSetup.applyHint");

  return (
    <section aria-label={t("settings.modelSetup.title")} className="space-y-3 rounded-xl border border-[var(--border)] bg-[var(--muted)]/30 p-4">
      <h2 className="text-sm font-semibold">{t("settings.modelSetup.title")}</h2>
      <ol className="flex flex-wrap gap-x-5 gap-y-2 text-xs">
        {labels.map((label, index) => (
          <li key={label} aria-current={index === step ? "step" : undefined} className={`inline-flex items-center gap-1.5 ${index === step ? "font-semibold text-[var(--primary)]" : "text-[var(--muted-foreground)]"}`}>
            {index < step ? <Check size={13} aria-hidden /> : <span aria-hidden>{index + 1}.</span>}
            {label}
          </li>
        ))}
      </ol>
      <p className="text-sm text-[var(--muted-foreground)]">{hint}</p>
      {applied ? (
        <Link href="/chat" className={registryButton}>{t("settings.modelSetup.tryChat")}<ArrowRight size={14} /></Link>
      ) : step === 0 && onAddModel ? (
        <Link href="/settings/connections" className={registryButton}>{t("Go to providers")}<ArrowRight size={14} /></Link>
      ) : step === 1 && onAddModel ? (
        <button type="button" onClick={onAddModel} className={registryButton}>{t("settings.modelSetup.chooseModel")}<ArrowRight size={14} /></button>
      ) : step < 3 && !onAddModel && hasProvider ? (
        <Link href="/settings/llm" className={registryButton}>{t("settings.modelSetup.configureModels")}<ArrowRight size={14} /></Link>
      ) : step === 3 ? (
        <button type="button" onClick={() => document.getElementById("settings-apply-changes")?.focus({ preventScroll: true })} className={registryButton}>
          {t("settings.modelSetup.reviewApply")}<ArrowRight size={14} />
        </button>
      ) : null}
    </section>
  );
}
