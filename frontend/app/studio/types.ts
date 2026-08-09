export type StudioGenerationStatus =
  | 'queued'
  | 'generating'
  | 'complete'
  | 'error';

export interface StudioReferenceImage {
  id: string;
  url: string;
}

export interface StudioModel {
  id: string;
  name: string;
  provider: string;
  modality_type: 'text_and_image' | 'image_only';
  supports_editing: boolean;
  supports_aspect_ratio: boolean;
  supported_aspect_ratios: string[];
  supports_resolution: boolean;
  supported_resolutions: string[];
  pricing_info: string;
  tier_minimum: 'free' | 'starter' | 'pro' | 'max' | 'byok';
  is_locked?: boolean;
  notes?: string;
  input_cost_per_million?: number;
  output_cost_per_million?: number;
  flat_image_price_usd?: number;
  first_megapixel_price_usd?: number;
  additional_megapixel_price_usd?: number;
  resolution_prices_usd?: Record<string, number>;
}

export interface StudioGeneration {
  id: string;
  modelId: string;
  prompt: string;
  aspectRatio: string;
  resolution: string;
  status: StudioGenerationStatus;
  createdAt: string;
  mediaType?: 'image' | 'video';
  imageId?: string;
  imageUrl?: string;
  videoUrl?: string;
  durationSeconds?: number;
  generationTimeMs?: number;
  costEstimate?: number;
  modelName?: string;
  error?: string;
  refunded?: boolean;
}
