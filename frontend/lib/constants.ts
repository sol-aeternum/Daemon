export type TtsSettings = {
  enabled: boolean;
  voice: string;
  model: string;
  speed: number;
  format: string;
};

export type SttSettings = {
  language: string;
  enablePartials: boolean;
};

export const DEFAULT_TTS_SETTINGS: TtsSettings = {
  enabled: true,
  voice: "Xb7hH8MSUJpSbSDYk0k2",
  model: "eleven_flash_v2_5",
  speed: 1.0,
  format: "mp3",
};

export const DEFAULT_STT_SETTINGS: SttSettings = {
  language: "en",
  enablePartials: true,
};
