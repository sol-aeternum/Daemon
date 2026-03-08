"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Flame, Search, Zap } from "lucide-react";

type CatalogModel = {
  id: string;
  name: string;
  tagline: string;
  badges: string[];
};

type Catalog = {
  auto: { id: string; name: string; tagline: string; icon: string };
  featured: CatalogModel[];
};

type ModelSelectorProps = {
  selected: string;
  onSelect: (modelId: string) => void;
};

export function ModelSelector({ selected, onSelect }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [showMore, setShowMore] = useState(false);
  const [allModels, setAllModels] = useState<{ id: string; name?: string }[]>([]);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const apiBase = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

  useEffect(() => {
    fetch(`${apiBase}/v1/catalog`)
      .then((r) => r.json())
      .then((data) => setCatalog(data))
      .catch(() => setCatalog(null));
  }, [apiBase]);

  useEffect(() => {
    if (!showMore || allModels.length > 0) return;
    fetch(`${apiBase}/v1/models`)
      .then((r) => r.json())
      .then((data) => setAllModels(data?.data || []))
      .catch(() => setAllModels([]));
  }, [showMore, allModels.length, apiBase]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setShowMore(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const selectedName =
    selected === "auto"
      ? "Auto"
      : catalog?.featured.find((m) => m.id === selected)?.name ||
        allModels.find((m) => m.id === selected)?.name ||
        selected.split("/").pop() ||
        selected;

  const Badge = ({ type }: { type: string }) => {
    if (type === "hot") return <Flame className="w-3 h-3 text-[var(--color-status-warning)]" />;
    if (type === "new") {
      return (
        <span className="ml-1.5 rounded-full bg-[var(--color-status-success-bg)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-status-success)]">
          NEW
        </span>
      );
    }
    return null;
  };

  const filteredModels = allModels
    .filter((m) => {
      const q = search.toLowerCase();
      return (
        m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q)
      );
    })
    .slice(0, 20);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-[44px] items-center gap-1.5 rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2 text-sm transition-colors hover:bg-[var(--color-bg-tertiary)]"
      >
        {selected === "auto" && <Zap className="w-3.5 h-3.5 text-[var(--color-status-warning)]" />}
        <span className="text-[var(--color-text-secondary)]">{selectedName}</span>
        <ChevronDown className="w-3 h-3 text-[var(--color-text-muted)]" />
      </button>

      {open && catalog && (
        <div className="absolute bottom-full mb-2 left-0 w-72 bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] rounded-lg shadow-lg overflow-hidden z-50">
          <button
            type="button"
            onClick={() => {
              onSelect("auto");
              setOpen(false);
            }}
            className={`w-full min-h-[44px] px-3 py-2.5 flex items-center gap-2 text-left hover:bg-[var(--color-bg-tertiary)] ${
              selected === "auto" ? "bg-[var(--color-accent-subtle)]" : ""
            }`}
          >
            <Zap className="w-4 h-4 text-[var(--color-status-warning)]" />
            <div>
              <div className="text-sm font-medium">Auto</div>
              <div className="text-xs text-[var(--color-text-muted)]">Smart routing based on your message</div>
            </div>
          </button>

          <div className="border-t border-[var(--color-border-muted)]" />

          {catalog.featured.map((model) => (
            <button
              type="button"
              key={model.id}
              onClick={() => {
                onSelect(model.id);
                setOpen(false);
              }}
              className={`w-full min-h-[44px] px-3 py-2.5 flex items-start gap-2 text-left hover:bg-[var(--color-bg-tertiary)] ${
                selected === model.id ? "bg-[var(--color-accent-subtle)]" : ""
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium">{model.name}</span>
                  {model.badges.map((b) => (
                    <Badge key={b} type={b} />
                  ))}
                </div>
                <div className="text-xs text-[var(--color-text-muted)] truncate">{model.tagline}</div>
              </div>
            </button>
          ))}

          <div className="border-t border-[var(--color-border-muted)]" />

          {!showMore ? (
            <button
              type="button"
              onClick={() => setShowMore(true)}
              className="w-full min-h-[44px] px-3 py-2.5 text-left text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-bg-tertiary)]"
            >
              More models...
            </button>
          ) : (
            <div className="max-h-52 overflow-y-auto">
              <div className="px-3 py-2">
                <div className="flex items-center gap-1.5 px-2 py-1 bg-[var(--color-bg-tertiary)] rounded text-sm">
                  <Search className="w-3 h-3 text-[var(--color-text-muted)]" />
                  <input
                    type="text"
                    placeholder="Search models..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="bg-transparent outline-none flex-1 text-sm"
                    autoFocus
                  />
                </div>
              </div>
              {filteredModels.map((model) => (
                <button
                  type="button"
                  key={model.id}
                  onClick={() => {
                    onSelect(model.id);
                    setOpen(false);
                    setShowMore(false);
                  }}
                  className="w-full min-h-[44px] px-3 py-2.5 text-left text-sm hover:bg-[var(--color-bg-tertiary)] truncate"
                >
                  {model.name || model.id}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
