'use client';

import { useState, useEffect, useCallback } from 'react';
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
const SOURCES = ['All', 'Extracted', 'Manual', 'Tool'] as const;
const STATUSES = ['All', 'Active', 'Superseded', 'Rejected'] as const;

export default function MemoryFilters({ onFilterChange }: MemoryFiltersProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('All');
  const [selectedSource, setSelectedSource] = useState<string>('All');
  const [selectedStatus, setSelectedStatus] = useState<string>('Active');

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      const filters: FilterState = {};

      if (searchQuery.trim()) {
        filters.search = searchQuery.trim();
      }
      if (selectedCategory !== 'All') {
        filters.category = selectedCategory.toLowerCase();
      }
      if (selectedSource !== 'All') {
        filters.source_type = selectedSource.toLowerCase();
      }
      if (selectedStatus !== 'All') {
        filters.status = selectedStatus.toLowerCase();
      }

      onFilterChange(filters);
    }, 300);

    return () => clearTimeout(timer);
  }, [searchQuery, selectedCategory, selectedSource, selectedStatus, onFilterChange]);

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
  }, []);

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
    const baseClasses = 'px-3 py-1 text-xs font-medium rounded-full cursor-pointer transition-colors whitespace-nowrap';
    if (isSelected) {
      return `${baseClasses} bg-accent-primary text-white`;
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
          value={searchQuery}
          onChange={handleSearchChange}
          className="w-full pl-10 pr-4 py-2 bg-bg-secondary border border-border-primary rounded-lg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent-primary/50 focus:border-accent-primary"
        />
      </div>

      {/* Filter Groups */}
      <div className="space-y-3">
        {/* Category Chips */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-xs text-text-muted font-medium shrink-0">Category:</span>
          <div className="flex gap-2 overflow-x-auto pb-1 sm:pb-0">
            {CATEGORIES.map((category) => (
              <button
                key={category}
                type="button"
                onClick={() => handleCategoryClick(category)}
                className={getChipClasses(selectedCategory === category)}
              >
                {category}
              </button>
            ))}
          </div>
        </div>

        {/* Source Chips */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-xs text-text-muted font-medium shrink-0">Source:</span>
          <div className="flex gap-2 overflow-x-auto pb-1 sm:pb-0">
            {SOURCES.map((source) => (
              <button
                key={source}
                type="button"
                onClick={() => handleSourceClick(source)}
                className={getChipClasses(selectedSource === source)}
              >
                {source}
              </button>
            ))}
          </div>
        </div>

        {/* Status Chips */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <span className="text-xs text-text-muted font-medium shrink-0">Status:</span>
          <div className="flex gap-2 overflow-x-auto pb-1 sm:pb-0">
            {STATUSES.map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => handleStatusClick(status)}
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
