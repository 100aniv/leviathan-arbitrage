import React from "react";
import clsx from "clsx";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  align?: "left" | "right" | "center";
  className?: string;
  render: (row: T, index: number) => React.ReactNode;
}

interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  emptyLabel?: string;
  loading?: boolean;
  getRowKey?: (row: T, index: number) => string | number;
  className?: string;
  dense?: boolean;
}

/**
 * Path-B v2 W3 DataTable — light-weight table primitive.
 * DESIGN.md §4 Table pattern: dense rows, right-align numbers, zebra stripes.
 */
export function DataTable<T>({
  columns,
  rows,
  emptyLabel = "데이터 없음",
  loading = false,
  getRowKey,
  className = "",
  dense = true,
}: DataTableProps<T>) {
  if (loading) {
    return (
      <div className={clsx("bg-bg-surface border border-border rounded-[12px] p-6 space-y-2", className)}>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="skeleton h-6 w-full" aria-label="불러오는 중" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div
        className={clsx(
          "bg-bg-surface border border-border rounded-[12px] p-8 text-center text-caption text-text-tertiary",
          className,
        )}
      >
        {emptyLabel}
      </div>
    );
  }

  return (
    <div className={clsx("bg-bg-surface border border-border rounded-[12px] overflow-hidden", className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-caption">
          <thead>
            <tr className="border-b border-border bg-bg-muted text-text-secondary">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={clsx(
                    "px-3 font-medium uppercase tracking-wider text-small",
                    dense ? "py-1.5" : "py-2",
                    col.align === "right" && "text-right",
                    col.align === "center" && "text-center",
                    !col.align && "text-left",
                    col.className,
                  )}
                  scope="col"
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={getRowKey ? getRowKey(row, i) : i}
                className={clsx(
                  "border-b border-border/50 transition-colors hover:bg-bg-muted",
                  i % 2 === 1 && "bg-bg-base/50",
                )}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={clsx(
                      "px-3 text-text-primary",
                      dense ? "py-1.5" : "py-2.5",
                      col.align === "right" && "text-right tabular-nums font-mono",
                      col.align === "center" && "text-center",
                      !col.align && "text-left",
                      col.className,
                    )}
                  >
                    {col.render(row, i)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
