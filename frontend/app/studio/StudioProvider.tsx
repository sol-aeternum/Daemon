'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type {
  StudioGeneration,
  StudioModel,
  StudioReferenceImage,
} from './types';
import { useClientMounted } from '../../hooks/useClientMounted';

const MAX_MODELS = 4;
const STORAGE_MODELS_KEY = 'studio:selectedModels';
const STORAGE_ASPECT_RATIO_KEY = 'studio:aspectRatio';

const readStoredModels = (): string[] => {
  if (typeof localStorage === 'undefined') {
    return [];
  }

  const storedModels = localStorage.getItem(STORAGE_MODELS_KEY);
  if (!storedModels) {
    return [];
  }

  try {
    const parsed = JSON.parse(storedModels);
    return Array.isArray(parsed)
      ? parsed
          .filter((value): value is string => typeof value === 'string')
          .slice(0, MAX_MODELS)
      : [];
  } catch {
    localStorage.removeItem(STORAGE_MODELS_KEY);
    return [];
  }
};

const readStoredAspectRatio = (): string => {
  if (typeof localStorage === 'undefined') {
    return '1:1';
  }

  const storedAspectRatio = localStorage.getItem(STORAGE_ASPECT_RATIO_KEY);
  return storedAspectRatio && typeof storedAspectRatio === 'string'
    ? storedAspectRatio
    : '1:1';
};

interface StudioContextValue {
  availableModels: StudioModel[];
  selectedModels: string[];
  prompt: string;
  referenceImage: StudioReferenceImage | null;
  aspectRatio: string;
  resolution: string;
  generations: StudioGeneration[];
  isGenerating: boolean;
  addModel: (modelId: string) => void;
  removeModel: (modelId: string) => void;
  setAvailableModels: (models: StudioModel[]) => void;
  setPrompt: (value: string) => void;
  setReferenceImage: (value: StudioReferenceImage | null) => void;
  clearReference: () => void;
  setAspectRatio: (value: string) => void;
  setResolution: (value: string) => void;
  addGeneration: (value: StudioGeneration) => void;
  upsertGeneration: (value: { id: string } & Partial<StudioGeneration>) => void;
  clearGallery: () => void;
  setIsGenerating: (value: boolean) => void;
}

const StudioContext = createContext<StudioContextValue | null>(null);

export function StudioProvider({ children }: { children: ReactNode }) {
  const [availableModels, setAvailableModels] = useState<StudioModel[]>([]);
  const [selectedModels, setSelectedModels] =
    useState<string[]>(readStoredModels);
  const [prompt, setPrompt] = useState<string>('');
  const [referenceImage, setReferenceImage] =
    useState<StudioReferenceImage | null>(null);
  const [aspectRatio, setAspectRatioState] = useState<string>(
    readStoredAspectRatio,
  );
  const [resolution, setResolution] = useState<string>('1K');
  const [generations, setGenerations] = useState<StudioGeneration[]>([]);
  const [isGenerating, setIsGenerating] = useState<boolean>(false);
  const isClientMounted = useClientMounted();

  useEffect(() => {
    if (!isClientMounted) {
      return;
    }

    localStorage.setItem(STORAGE_MODELS_KEY, JSON.stringify(selectedModels));
  }, [isClientMounted, selectedModels]);

  useEffect(() => {
    if (!isClientMounted) {
      return;
    }

    localStorage.setItem(STORAGE_ASPECT_RATIO_KEY, aspectRatio);
  }, [isClientMounted, aspectRatio]);

  const addModel = useCallback((modelId: string) => {
    setSelectedModels((prev) => {
      if (prev.includes(modelId)) {
        return prev;
      }
      if (prev.length >= MAX_MODELS) {
        return prev;
      }
      return [...prev, modelId];
    });
  }, []);

  const removeModel = useCallback((modelId: string) => {
    setSelectedModels((prev) => prev.filter((value) => value !== modelId));
  }, []);

  const clearReference = useCallback(() => {
    setReferenceImage(null);
  }, []);

  const setAspectRatio = useCallback((value: string) => {
    setAspectRatioState(value);
  }, []);

  const addGeneration = useCallback((value: StudioGeneration) => {
    setGenerations((prev) => [value, ...prev]);
  }, []);

  const upsertGeneration = useCallback(
    (value: { id: string } & Partial<StudioGeneration>) => {
      setGenerations((prev) => {
        const index = prev.findIndex((item) => item.id === value.id);
        if (index === -1) {
          return [value as StudioGeneration, ...prev];
        }

        const copy = [...prev];
        copy[index] = { ...copy[index], ...value } as StudioGeneration;
        return copy;
      });
    },
    [],
  );

  const clearGallery = useCallback(() => {
    setGenerations([]);
  }, []);

  const value = useMemo<StudioContextValue>(
    () => ({
      selectedModels: isClientMounted ? selectedModels : [],
      prompt,
      referenceImage,
      availableModels,
      aspectRatio: isClientMounted ? aspectRatio : '1:1',
      resolution,
      generations,
      isGenerating,
      addModel,
      removeModel,
      setAvailableModels,
      setPrompt,
      setReferenceImage,
      clearReference,
      setAspectRatio,
      setResolution,
      addGeneration,
      upsertGeneration,
      clearGallery,
      setIsGenerating,
    }),
    [
      availableModels,
      isClientMounted,
      selectedModels,
      prompt,
      referenceImage,
      aspectRatio,
      resolution,
      generations,
      isGenerating,
      addModel,
      removeModel,
      clearReference,
      setAspectRatio,
      addGeneration,
      upsertGeneration,
      clearGallery,
    ],
  );

  return (
    <StudioContext.Provider value={value}>{children}</StudioContext.Provider>
  );
}

export function useStudio() {
  const context = useContext(StudioContext);
  if (!context) {
    throw new Error('useStudio must be used within a StudioProvider');
  }
  return context;
}
