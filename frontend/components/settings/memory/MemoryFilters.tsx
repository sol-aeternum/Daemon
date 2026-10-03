'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Search } from 'lucide-react';

interface FilterState {
  category?: string;
  source_type?: string;
  status?: string;
  search?: string;
}

interface MemoryFiltersProps {
  onFilterChange: (filters: FilterState) => void;
}

const CATEGORIES = ['All', 'Fact', 'Preference', 'Project', 'Summary'] as const;
// Labels map to stored provenance. Memories saved from Settings and those
// Daemon saved when asked in chat are stored identically ("user_created").
const SOURCES = ['All', 'Extracted', 'Added by you', 'Imported'] as const;
const SOURCE_VALUES: Record<string, string | undefined> = {
  All: undefined,
  Extracted: 'extracted',
  'Added by you': 'user_created',
  Imported: 'import',
};
// "All" is sent explicitly: every status except deleted.
const STATUSES = [
  'All',
  'Active',
  'Superseded',
  'Pending',
  'Rejected',
] as const;

export default function MemoryFilters({ onFilterChange }: MemoryFiltersProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('All');
  const [selectedSource, setSelectedSource] = useState<string>('All');
  const [selectedStatus, setSelectedStatus] = useState<string>('Active');
  // The list already loads with these defaults; re-emitting them on mount
  // would reset it to page one and discard an early "Load more".
  const initialRun = useRef(true);

  // Debounced search
  useEffect(() => {
    if (initialRun.current) {
      initialRun.current = false;
      return;
    }
    const timer = setTimeout(() => {
      const filters: FilterState = {};

      if (searchQuery.trim()) {
        filters.search = searchQuery.trim();
      }
      if (selectedCategory !== 'All') {
        filters.category = selectedCategory.toLowerCase();
      }
      const source = SOURCE_VALUES[selectedSource];
      if (source) {
        filters.source_type = source;
      }
      filters.status = selectedStatus.toLowerCase();

      onFilterChange(filters);
    }, 300);

    return () => clearTimeout(timer);
  }, [
    searchQuery,
    selectedCategory,
    selectedSource,
    selectedStatus,
    onFilterChange,
  ]);

  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setSearchQuery(e.target.value);
    },
    [],
  );

  const handleCategoryClick = useCallback((category: string) => {
    setSelectedCategory(category);
  }, []);

  const handleSourceClick = useCallback((source: string) => {
    setSelectedSource(source);
  }, []);

  const handleStatusClick = useCallback((status: string) => {
    setSelectedStatus(status);
  }, []);

  const getChipClasses = (isSelected: boolean) => {
    const baseClasses =
      'min-h-touch min-w-touch px-3 py-2 text-xs font-medium rounded-full cursor-pointer transition-colors whitespace-nowrap';
    if (isSelected) {
      return `${baseClasses} bg-accent-primary text-[var(--color-text-on-accent)]`;
    }
    return `${baseClasses} bg-bg-tertiary text-text-muted hover:text-text-secondary`;
  };

  return (
    <div className="space-y-4">
      {/* Search Input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
        <input
          type="text"
          placeholder="Search memories..."
          aria-label="Search memories"
          value={searchQuery}
          onChange={handleSearchChange}
          className="w-full pl-10 pr-4 py-2 bg-bg-secondary border border-border-primary rounded-lg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent-primary/50 focus:border-accent-primary"
        />
      </div>

      {/* Filter Groups */}
      <div className="space-y-3">
        {/* Category Chips */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-xs text-text-muted font-medium shrink-0">
            Category:
          </span>
          <div className="flex gap-2 overflow-x-auto pb-1 sm:pb-0">
            {CATEGORIES.map((category) => (
              <button
                key={category}
                type="button"
                onClick={() => handleCategoryClick(category)}
                aria-pressed={selectedCategory === category}
                className={getChipClasses(selectedCategory === category)}
              >
                {category}
              </button>
            ))}
          </div>
        </div>

        {/* Source Chips */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-xs text-text-muted font-medium shrink-0">
            Source:
          </span>
          <div className="flex gap-2 overflow-x-auto pb-1 sm:pb-0">
            {SOURCES.map((source) => (
              <button
                key={source}
                type="button"
                onClick={() => handleSourceClick(source)}
                aria-pressed={selectedSource === source}
                className={getChipClasses(selectedSource === source)}
              >
                {source}
              </button>
            ))}
          </div>
        </div>

        {/* Status Chips */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-xs text-text-muted font-medium shrink-0">
            Status:
          </span>
          <div className="flex gap-2 overflow-x-auto pb-1 sm:pb-0">
            {STATUSES.map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => handleStatusClick(status)}
                aria-pressed={selectedStatus === status}
                className={getChipClasses(selectedStatus === status)}
              >
                {status}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
