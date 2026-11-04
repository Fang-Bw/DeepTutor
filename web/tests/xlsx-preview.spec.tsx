import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import XlsxPreview from "@/components/chat/preview/previewers/XlsxPreview";
import type { BinarySourceState } from "@/components/chat/preview/previewers/useBinarySource";
import { initI18n } from "@/i18n/init";

const fixture = vi.hoisted(() => ({
  source: { kind: "ready", buffer: new ArrayBuffer(0) } as BinarySourceState,
  sheets: [] as ReturnType<typeof makeSheet>[],
  load: vi.fn(),
}));

vi.mock("@/components/chat/preview/previewers/useBinarySource", () => ({
  useBinarySource: () => fixture.source,
}));
vi.mock("exceljs", () => {
  class Workbook {
    worksheets = fixture.sheets;
    xlsx = { load: fixture.load };
  }
  return { Workbook, default: { Workbook } };
});

initI18n("en");

function makeSheet(name: string, rowCount: number, columnCount: number) {
  const getCell = vi.fn((row: number, column: number) => ({ text: `${name}:${row}:${column}` }));
  return {
    name,
    rowCount,
    columnCount,
    eachRow: vi.fn(),
    getCell,
    getRow: vi.fn((row: number) => ({ getCell: (column: number) => getCell(row, column) })),
  };
}

beforeEach(() => {
  fixture.source = { kind: "ready", buffer: new ArrayBuffer(0) };
  fixture.load.mockResolvedValue(undefined);
  fixture.sheets = [];
});

describe("spreadsheet preview paging", () => {
  it("reads only the visible page, limits columns, and leaves inactive sheets untouched", async () => {
    const large = makeSheet("Large", 1_000_000, 100);
    const other = makeSheet("Other", 1000, 10);
    fixture.sheets = [large, other];
    const { container } = render(<XlsxPreview url="/book.xlsx" />);

    await screen.findByText("Large:1:1");
    expect(container.querySelectorAll("tbody tr")).toHaveLength(50);
    expect(container.querySelectorAll("td")).toHaveLength(50 * 61);
    expect(large.getRow).toHaveBeenCalledTimes(50);
    expect(large.getCell).toHaveBeenCalledTimes(50 * 60);
    expect(large.eachRow).not.toHaveBeenCalled();
    expect(other.getRow).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByText("Large sheet — preview truncated. Download for the full file.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("Large:51:1")).toBeInTheDocument();
    expect(screen.queryByText("Large:1:1")).not.toBeInTheDocument();
    expect(large.getRow).toHaveBeenCalledTimes(100);
    expect(other.getRow).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    expect(screen.getByText("Other:1:1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(other.getRow).toHaveBeenCalledTimes(50);
    expect(large.getRow).toHaveBeenCalledTimes(100);
  });

  it("never navigates or reads beyond the first 1000 rows", async () => {
    const sheet = makeSheet("Bounded", 1_000_000, 1);
    fixture.sheets = [sheet];
    render(<XlsxPreview url="/book.xlsx" />);
    await screen.findByText("Bounded:1:1");
    const next = screen.getByRole("button", { name: "Next page" });
    for (let i = 1; i < 20; i += 1) fireEvent.click(next);
    expect(screen.getByText("Bounded:1000:1")).toBeInTheDocument();
    expect(next).toBeDisabled();
    expect(sheet.getRow).toHaveBeenCalledTimes(1000);
    expect(sheet.getRow.mock.calls.every(([row]) => row <= 1000)).toBe(true);
    expect(sheet.eachRow).not.toHaveBeenCalled();
  });

  it("clears an old workbook when a replacement starts loading and resets the page", async () => {
    fixture.sheets = [makeSheet("Old", 100, 1)];
    const { rerender } = render(<XlsxPreview url="/old.xlsx" />);
    await screen.findByText("Old:1:1");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    fixture.source = { kind: "loading" };
    rerender(<XlsxPreview url="/new.xlsx" />);
    expect(screen.queryByText("Old:51:1")).not.toBeInTheDocument();
    expect(screen.getByText("Loading preview…")).toBeInTheDocument();
    fixture.sheets = [makeSheet("New", 1, 1)];
    fixture.source = { kind: "ready", buffer: new ArrayBuffer(1) };
    rerender(<XlsxPreview url="/new.xlsx" />);
    await screen.findByText("New:1:1");
    expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
  });

  it("keeps empty workbook and parse failure fallbacks", async () => {
    const { rerender } = render(<XlsxPreview url="/empty.xlsx" />);
    await screen.findByText("This workbook has no sheets to preview.");
    fixture.load.mockRejectedValueOnce(new Error("invalid zip"));
    fixture.source = { kind: "ready", buffer: new ArrayBuffer(1) };
    rerender(<XlsxPreview url="/invalid.xlsx" />);
    await waitFor(() => expect(screen.getByText("Couldn't render this spreadsheet — use Download to open it.")).toBeInTheDocument());
  });
});
