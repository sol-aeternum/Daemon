export type TtsSettings = {
  enabled: boolean;
  autoPlay: boolean;
  voice: string;
  model: string;
  speed: number;
  format: string;
};

/** Matches the API's raw Unicode code-point text bound before Markdown cleanup. */
export const MAX_TTS_TEXT_CODE_POINTS = 3000;

export type SttSettings = {
  language: string;
  enablePartials: boolean;
};

export const DEFAULT_TTS_SETTINGS: TtsSettings = {
  enabled: true,
  autoPlay: false,
  voice: 'daemon-default',
  model: 'daemon-default',
  speed: 1.0,
  format: 'mp3',
};

export const DEFAULT_STT_SETTINGS: SttSettings = {
  language: 'en',
  enablePartials: true,
};
