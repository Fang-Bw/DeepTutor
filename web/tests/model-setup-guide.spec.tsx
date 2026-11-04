import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ModelSetupGuide } from "@/components/settings/ModelSetupGuide";
import ModelSelector from "@/components/chat/home/ModelSelector";
import type { Catalog, SettingsContextValue } from "@/features/settings/store/SettingsStore";

const mocks = vi.hoisted(() => ({ settings: {} as SettingsContextValue, auth: { loading: false, statusAvailable: true, enabled: false, isAdmin: false } }));
vi.mock("@/features/settings/store/SettingsStore", () => ({ useSettings: () => mocks.settings }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => mocks.auth }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

function catalog(): Catalog {
  return {
    version: 1,
    connections: [],
    services: Object.fromEntries(
      ["llm", "task", "embedding", "search", "tts", "stt", "imagegen", "videogen"].map(
        (service) => [service, { profiles: [], active_profile_id: null, active_model_id: null }],
      ),
    ),
  } as unknown as Catalog;
}
function addModel(draft: Catalog) {
  draft.services.llm.profiles.push({ id: "first", name: "Account", binding: "custom", base_url: "", api_key: "", api_version: "", models: [{ id: "model", name: "Model", model: "example-model" }] });
}
beforeEach(() => {
  mocks.auth = { loading: false, statusAvailable: true, enabled: false, isAdmin: false };
  mocks.settings = {
    catalog: catalog(), draft: catalog(), catalogEditable: true, draftState: "clean",
    providers: { llm: [{ value: "custom", label: "Custom" }] }, connectionTargets: [],
  } as unknown as SettingsContextValue;
});

it("guides a missing default through explicit selection and apply, then returns to chat", () => {
  const view = render(<ModelSetupGuide onAddModel={vi.fn()} />);
  expect(screen.getByRole("link", { name: "Go to providers" })).toHaveAttribute("href", "/settings/connections");
  addModel(mocks.settings.draft);
  view.rerender(<ModelSetupGuide onAddModel={vi.fn()} />);
  expect(screen.getByText("settings.modelSetup.defaultHint")).toBeVisible();
  expect(mocks.settings.draft.services.llm.active_model_id).toBeNull();
  mocks.settings.draft.services.llm.active_profile_id = "first";
  mocks.settings.draft.services.llm.active_model_id = "model";
  mocks.settings.draftState = "saved";
  view.rerender(<ModelSetupGuide onAddModel={vi.fn()} />);
  expect(screen.getByText("settings.modelSetup.applyHint")).toBeVisible();
  expect(screen.getByRole("button", { name: "settings.modelSetup.reviewApply" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "settings.modelSetup.tryChat" })).toBeNull();
  mocks.settings.catalog = structuredClone(mocks.settings.draft);
  mocks.settings.draftState = "clean";
  view.rerender(<ModelSetupGuide onAddModel={vi.fn()} />);
  expect(screen.getByRole("link", { name: "settings.modelSetup.tryChat" })).toHaveAttribute("href", "/chat");
});

it("offers a model action for an empty provider and does not accept a blank model as ready", () => {
  mocks.settings.draft.connections = [{ id: "conn", name: "Custom", provider: "custom", api_key: "", base_url: "", api_version: "" }];
  const onAdd = vi.fn();
  const view = render(<ModelSetupGuide onAddModel={onAdd} />);
  fireEvent.click(screen.getByRole("button", { name: "settings.modelSetup.chooseModel" }));
  expect(onAdd).toHaveBeenCalledOnce();
  addModel(mocks.settings.draft);
  mocks.settings.draft.services.llm.profiles[0].models[0].model = " ";
  mocks.settings.draft.services.llm.active_profile_id = "first";
  mocks.settings.draft.services.llm.active_model_id = "model";
  view.rerender(<ModelSetupGuide onAddModel={onAdd} />);
  expect(screen.getByText("settings.modelSetup.modelHint")).toBeVisible();
});

it("does not show first-run instructions for an existing active model", () => {
  addModel(mocks.settings.catalog);
  mocks.settings.catalog.services.llm.active_profile_id = "first";
  mocks.settings.catalog.services.llm.active_model_id = "model";
  mocks.settings.draft = structuredClone(mocks.settings.catalog);
  render(<ModelSetupGuide />);
  expect(screen.queryByRole("region")).toBeNull();
});

it("turns the empty chat model picker into a setup link without treating fetch errors as missing configuration", () => {
  const onRefresh = vi.fn();
  const props = { options: [], activeDefault: null, value: null, loading: false, error: false, onChange: vi.fn(), onRefresh };
  const view = render(<ModelSelector {...props} />);
  expect(screen.getByRole("link", { name: "settings.modelSetup.configureModels" })).toHaveAttribute("href", "/settings/connections");
  view.rerender(<ModelSelector {...props} error />);
  expect(screen.queryByRole("link")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh models" }));
  expect(onRefresh).toHaveBeenCalledOnce();
});

it("directs non-admin users to their administrator instead of a restricted settings page", () => {
  mocks.auth.enabled = true;
  render(<ModelSelector options={[]} activeDefault={null} value={null} loading={false} error={false} onChange={vi.fn()} />);
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText("settings.modelSetup.contactAdmin")).toBeVisible();
});
