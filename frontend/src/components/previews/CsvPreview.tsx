'use client';

import { useState, useEffect, useMemo } from 'react';
import { Loader2, AlertCircle, FileSpreadsheet } from 'lucide-react';
import Papa from 'papaparse';

interface CsvPreviewProps {
  content: string;
  maxRows?: number;
}

interface CsvData {
  headers: string[];
  rows: string[][];
  totalRows: number;
}

export function CsvPreview({ content, maxRows = 100 }: CsvPreviewProps) {
  const [data, setData] = useState<CsvData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const parseCsv = async () => {
      setIsLoading(true);
      setError(null);

      try {
        const result = Papa.parse<string[]>(content, {
          skipEmptyLines: true,
          delimiter: ',',
        });

        if (!isMounted) return;

        if (
          result.errors.length > 0 &&
          result.errors[0].code !== 'TooFewFields'
        ) {
          setError(`Parse error: ${result.errors[0].message}`);
          return;
        }

        const allRows = result.data;
        if (allRows.length === 0) {
          setError('CSV file is empty');
          return;
        }

        const headers = allRows[0];
        const dataRows = allRows.slice(1);
        const totalRows = dataRows.length;
        const rows = dataRows.slice(0, maxRows);

        setData({ headers, rows, totalRows });
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : 'Failed to parse CSV');
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    parseCsv();

    return () => {
      isMounted = false;
    };
  }, [content, maxRows]);

  const rowInfo = useMemo(() => {
    if (!data) return null;
    const displayed = data.rows.length;
    const total = data.totalRows;
    if (total > maxRows) {
      return `Showing ${displayed} of ${total} rows`;
    }
    return `${total} rows`;
  }, [data, maxRows]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-3 p-8 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] max-h-[400px]">
        <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-primary)]" />
        <span className="text-sm text-[var(--color-text-muted)]">
          Parsing CSV...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl max-h-[400px]">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-red-500">
            Failed to parse CSV
          </p>
          <p className="text-xs text-red-400 mt-1">{error}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return null;
  }

  return (
    <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-[400px]">
      {/* Header with row info */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
        <div className="flex items-center gap-2">
          <FileSpreadsheet className="w-4 h-4 text-[var(--color-status-success)]" />
          <span className="text-sm font-medium text-[var(--color-text-secondary)]">
            CSV Preview
          </span>
        </div>
        <span className="text-xs text-[var(--color-text-muted)]">
          {rowInfo}
        </span>
      </div>

      {/* Table container with scroll */}
      <div className="overflow-auto max-h-[340px]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--color-bg-secondary)] sticky top-0 z-10">
            <tr>
              {data.headers.map((header, idx) => (
                <th
                  key={idx}
                  className="px-4 py-2 text-left font-medium text-[var(--color-text-secondary)] border-b border-[var(--color-border-primary)] whitespace-nowrap"
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, rowIdx) => (
              <tr
                key={rowIdx}
                className="border-b border-[var(--color-border-primary)] last:border-b-0 hover:bg-[var(--color-bg-secondary)]/50 transition-colors"
              >
                {row.map((cell, cellIdx) => (
                  <td
                    key={cellIdx}
                    className="px-4 py-2 text-[var(--color-text-primary)] whitespace-nowrap"
                  >
                    {cell}
                  </td>
                ))}
                {/* Pad cells if row is shorter than headers */}
                {row.length < data.headers.length &&
                  Array.from({ length: data.headers.length - row.length }).map(
                    (_, padIdx) => (
                      <td
                        key={`pad-${padIdx}`}
                        className="px-4 py-2 text-[var(--color-text-muted)]"
                      >
                        —
                      </td>
                    ),
                  )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
