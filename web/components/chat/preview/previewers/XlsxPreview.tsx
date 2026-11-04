"use client";

import { useEffect, useMemo, useState } from "react";
import type { Worksheet } from "exceljs";
import { AlertCircle, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useBinarySource } from "./useBinarySource";
import Tooltip from "@/shared/ui/Tooltip";

// Preview bounds — a spreadsheet can hold millions of cells; rendering them
// all would lock the tab. We cap and flag truncation; Download gets the rest.
const MAX_ROWS = 1000;
const MAX_COLS = 60;
const PAGE_ROWS = 50;

/**
 * Browser fallback when server-side PDF conversion is unavailable. Each
 * worksheet is rendered via exceljs as a lightweight table with a sheet tab
 * strip. It shows cached cell text but not the workbook's visual formatting.
 */
export default function XlsxPreview({ url }: { url: string }) {
  const { t } = useTranslation();
  const src = useBinarySource(url);
  const [workbook, setWorkbook] = useState<{
    buffer: ArrayBuffer;
    sheets: Worksheet[];
  } | null>(null);
  const [active, setActive] = useState(0);
  const [page, setPage] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (src.kind !== "ready") {
      setWorkbook(null);
      setFailed(false);
      return;
    }

    let cancelled = false;
    setFailed(false);
    setWorkbook(null);
    (async () => {
      try {
        const mod = await import("exceljs");
        const ExcelJS =
          (mod as unknown as { default?: typeof mod }).default ?? mod;
        if (cancelled) return;
        const wb = new ExcelJS.Workbook();
        await wb.xlsx.load(src.buffer);
        if (cancelled) return;

        // ExcelJS still loads the workbook, but cell text is materialized only
        // for the visible page below, never for every row or every worksheet.
        setWorkbook({ buffer: src.buffer, sheets: wb.worksheets });
        setActive(0);
        setPage(0);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [src]);

  const sheets = src.kind === "ready" && workbook?.buffer === src.buffer
    ? workbook.sheets
    : null;
  const sheet = sheets?.[Math.min(active, sheets.length - 1)];
  const rowCount = Math.min(sheet?.rowCount ?? 0, MAX_ROWS);
  const pageCount = Math.max(1, Math.ceil(rowCount / PAGE_ROWS));
  const pageIndex = Math.min(page, pageCount - 1);
  const startRow = pageIndex * PAGE_ROWS + 1;
  const endRow = Math.min(startRow + PAGE_ROWS - 1, rowCount);
  const rows = useMemo(() => {
    if (!sheet) return [];
    const colCount = Math.min(sheet.columnCount, MAX_COLS);
    const result: string[][] = [];
    // eachRow would visit all allocated rows even after hitting MAX_ROWS.
    for (let r = startRow; r <= endRow; r += 1) {
      const row = sheet.getRow(r);
      const cells: string[] = [];
      for (let c = 1; c <= colCount; c += 1) {
        const text = row.getCell(c).text;
        cells.push(typeof text === "string" ? text : String(text ?? ""));
      }
      result.push(cells);
    }
    return result;
  }, [sheet, startRow, endRow]);

  if (src.kind === "error" || failed) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-[12px] text-[var(--muted-foreground)]">
        <AlertCircle size={18} strokeWidth={1.7} className="opacity-70" />
        <p>
          {t("Couldn't render this spreadsheet — use Download to open it.")}
        </p>
      </div>
    );
  }

  if (!sheets) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-[12px] text-[var(--muted-foreground)]">
        <Loader2 size={14} className="animate-spin" />
        <span>{t("Loading preview…")}</span>
      </div>
    );
  }

  if (sheets.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center text-[12px] text-[var(--muted-foreground)]">
        {t("This workbook has no sheets to preview.")}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-[var(--card)]">
      <div key={`${active}-${pageIndex}`} className="min-h-0 flex-1 overflow-auto">
        <table className="border-collapse text-[12px] text-[var(--foreground)]">
          <tbody>
            {rows.map((cells, r) => (
              <tr key={startRow + r}>
                <td className="sticky left-0 z-10 border border-[var(--border)]/50 bg-[var(--muted)]/55 px-2 py-1 text-right text-[10px] tabular-nums text-[var(--muted-foreground)]/70">
                  {startRow + r}
                </td>
                {cells.map((cell, c) => (
                  <td
                    key={c}
                    className={`max-w-[280px] truncate border border-[var(--border)]/40 px-2 py-1 ${
                      startRow + r === 1
                        ? "bg-[var(--muted)]/35 font-medium"
                        : "bg-[var(--card)]"
                    }`}
                    title={cell}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {sheet && (sheet.rowCount > MAX_ROWS || sheet.columnCount > MAX_COLS) ? (
          <p className="px-3 py-2 text-[11px] text-[var(--muted-foreground)]/70">
            {t("Large sheet — preview truncated. Download for the full file.")}
          </p>
        ) : null}
      </div>

      {pageCount > 1 ? (
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-[var(--border)]/40 px-3 py-2 text-[11px] text-[var(--muted-foreground)]">
          <button
            type="button"
            disabled={pageIndex === 0}
            onClick={() => setPage(pageIndex - 1)}
            className="rounded px-2 py-1 hover:bg-[var(--muted)] disabled:opacity-40"
          >
            {t("Previous page")}
          </button>
          <span aria-live="polite">
            {t("Rows {{start}}–{{end}} of {{total}}", {
              start: startRow,
              end: endRow,
              total: rowCount,
            })}
          </span>
          <button
            type="button"
            disabled={pageIndex === pageCount - 1}
            onClick={() => setPage(pageIndex + 1)}
            className="rounded px-2 py-1 hover:bg-[var(--muted)] disabled:opacity-40"
          >
            {t("Next page")}
          </button>
        </div>
      ) : null}

      {/* Sheet tabs — only when the workbook has more than one. */}
      {sheets.length > 1 ? (
        <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-t border-[var(--border)]/40 bg-[var(--muted)]/25 px-2 py-1.5">
          {sheets.map((s, i) => (
            <Tooltip key={`${s.name}-${i}`} label={s.name} side="top">
              <button
                type="button"
                onClick={() => { setActive(i); setPage(0); }}
                aria-label={s.name}
                className={`shrink-0 rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors ${
                  i === active
                    ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--card)]/70 hover:text-[var(--foreground)]"
                }`}
              >
                <span className="block max-w-[140px] truncate">{s.name}</span>
              </button>
            </Tooltip>
          ))}
        </div>
      ) : null}
    </div>
  );
}
