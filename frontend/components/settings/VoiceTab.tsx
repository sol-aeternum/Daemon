'use client';

import { useEffect, useState } from 'react';
import { useLocalStorage } from '@/hooks/useLocalStorage';
import { SkeletonLine, SkeletonBlock, SkeletonCircle } from '@/components/ui/Skeleton';
import { DEFAULT_STT_SETTINGS, DEFAULT_TTS_SETTINGS, type SttSettings, type TtsSettings } from '@/lib/constants';
import {
  Volume2,
  Mic,
  Save,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Music,
  Languages,
  Eye,
  Gauge,
  FileAudio,
  BrainCircuit,
} from 'lucide-react';

// Voice options
const VOICES = [
  { id: 'allay', name: 'Allay', description: 'Gentle and soothing' },
  { id: 'amy', name: 'Amy', description: 'Warm and friendly' },
  { id: 'aria', name: 'Aria', description: 'Expressive and dynamic' },
  { id: 'ashley', name: 'Ashley', description: 'Clear and professional' },
  { id: 'char', name: 'Char', description: 'Deep and resonant' },
  { id: 'emma', name: 'Emma', description: 'Bright and energetic' },
  { id: 'josh', name: 'Josh', description: 'Natural and conversational' },
  { id: 'rachel', name: 'Rachel', description: 'Polished and articulate' },
  { id: 'sage', name: 'Sage', description: 'Wise and measured' },
];

// Audio format options
const AUDIO_FORMATS = [
  { id: 'mp3', name: 'MP3', description: 'Compressed, widely supported' },
  { id: 'opus', name: 'Opus', description: 'Efficient, low latency' },
  { id: 'wav', name: 'WAV', description: 'Uncompressed, high quality' },
];

// STT language options
const STT_LANGUAGES = [
  { id: 'en', name: 'English', description: 'English (US/UK)' },
  { id: 'zh', name: 'Chinese', description: '中文' },
  { id: 'ja', name: 'Japanese', description: '日本語' },
  { id: 'ko', name: 'Korean', description: '한국어' },
  { id: 'es', name: 'Spanish', description: 'Español' },
  { id: 'fr', name: 'French', description: 'Français' },
  { id: 'de', name: 'German', description: 'Deutsch' },
];

type SaveStatus = 'idle' | 'loading' | 'success' | 'error';

export default function VoiceTab() {
  const [mounted, setMounted] = useState(false);
  const { value: ttsSettings, setValue: setTtsSettings } = useLocalStorage<TtsSettings>('tts_settings', DEFAULT_TTS_SETTINGS);
  const { value: sttSettings, setValue: setSttSettings } = useLocalStorage<SttSettings>('stt_settings', DEFAULT_STT_SETTINGS);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [errorMessage, setErrorMessage] = useState('');

  // Handle form submission
  const handleSubmit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    setSaveStatus('loading');
    setErrorMessage('');

    try {
      // Settings are already persisted via useLocalStorage
      // Just simulate a brief loading state for UX
      await new Promise((resolve) => setTimeout(resolve, 500));

      setSaveStatus('success');

      // Reset success status after 3 seconds
      setTimeout(() => {
        setSaveStatus('idle');
      }, 3000);
    } catch (error) {
      console.error('Error saving settings:', error);
      setSaveStatus('error');
      setErrorMessage('Failed to save settings. Please try again.');
    }
  };

  // Update TTS settings
  const updateTtsSetting = <K extends keyof TtsSettings>(key: K, value: TtsSettings[K]) => {
    setTtsSettings((prev) => ({ ...prev, [key]: value }));
  };

  // Update STT settings
  const updateSttSetting = <K extends keyof SttSettings>(key: K, value: SttSettings[K]) => {
    setSttSettings((prev) => ({ ...prev, [key]: value }));
  };

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <div className="animate-fade-in space-y-6">
        <div className="flex items-center gap-3">
          <SkeletonCircle size={40} />
          <div className="space-y-2">
            <SkeletonLine width={160} height={20} />
            <SkeletonLine width={280} height={16} />
          </div>
        </div>
        <SkeletonBlock height={220} />
        <SkeletonBlock height={180} />
        <SkeletonBlock width={140} height={40} />
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6 pb-6 border-b border-[var(--color-border-primary)]">
        <div className="w-10 h-10 rounded-full bg-[var(--color-accent-subtle)] flex items-center justify-center">
          <Volume2 className="w-5 h-5 text-[var(--color-accent-primary)]" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Voice Settings</h2>
          <p className="text-sm text-[var(--color-text-muted)]">Configure text-to-speech and speech-to-text preferences</p>
        </div>
      </div>

      {/* Error Message */}
      {saveStatus === 'error' && (
        <div className="mb-6 p-4 rounded-lg bg-[var(--color-status-error-bg)] border border-[var(--color-status-error)]/20 flex items-start gap-3 animate-slide-up">
          <AlertCircle className="w-5 h-5 text-[var(--color-status-error)] flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-[var(--color-status-error)]">Save failed</p>
            <p className="text-sm text-[var(--color-text-secondary)]">{errorMessage}</p>
          </div>
        </div>
      )}

      {/* Success Message */}
      {saveStatus === 'success' && (
        <div className="mb-6 p-4 rounded-lg bg-[var(--color-status-success-bg)] border border-[var(--color-status-success)]/20 flex items-center gap-3 animate-slide-up">
          <CheckCircle2 className="w-5 h-5 text-[var(--color-status-success)] flex-shrink-0" />
          <p className="text-sm font-medium text-[var(--color-status-success)]">Settings saved successfully</p>
        </div>
      )}

      <fieldset disabled={saveStatus === 'loading'} className="space-y-8 disabled:opacity-70">
        {/* ========================================
            TEXT TO SPEECH SECTION
            ======================================== */}
        <section className="space-y-5">
          <div className="flex items-center gap-2">
            <Music className="w-5 h-5 text-[var(--color-accent-primary)]" />
            <h3 className="text-base font-semibold text-[var(--color-text-primary)]">Text to Speech</h3>
          </div>

          <div className="space-y-5 pl-4 border-l-2 border-[var(--color-border-primary)]">
            <div className="flex items-center justify-between p-4 bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border-primary)]">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-md bg-[var(--color-accent-subtle)] flex items-center justify-center">
                  <Volume2 className="w-4 h-4 text-[var(--color-accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--color-text-primary)]">
                    Auto-play AI responses
                  </label>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Read assistant replies aloud while they stream
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => updateTtsSetting('autoPlay', !ttsSettings.autoPlay)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)]/50 ${
                  ttsSettings.autoPlay ? 'bg-[var(--color-accent-primary)]' : 'bg-[var(--color-bg-tertiary)]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    ttsSettings.autoPlay ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            <div className="flex items-center justify-between p-4 bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border-primary)]">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-md bg-[var(--color-accent-subtle)] flex items-center justify-center">
                  <Volume2 className="w-4 h-4 text-[var(--color-accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--color-text-primary)]">
                    Enable Text-to-Speech controls
                  </label>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Show per-message play buttons and voice controls
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => updateTtsSetting('enabled', !ttsSettings.enabled)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)]/50 ${
                  ttsSettings.enabled ? 'bg-[var(--color-accent-primary)]' : 'bg-[var(--color-bg-tertiary)]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    ttsSettings.enabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* Voice Selector */}
            <div className="space-y-2">
              <label
                htmlFor="voice"
                className="block text-sm font-medium text-[var(--color-text-secondary)]"
              >
                Voice
              </label>
              <div className="relative">
                <select
                  id="voice"
                  value={ttsSettings.voice}
                  onChange={(e) => updateTtsSetting('voice', e.target.value)}
                  disabled={!ttsSettings.enabled}
                  className="w-full px-3 py-2.5 bg-[var(--color-bg-input)] border border-[var(--color-border-primary)] rounded-md text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-border-focus)] focus:ring-1 focus:ring-[var(--color-border-focus)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed appearance-none cursor-pointer"
                >
                  {VOICES.map((voice) => (
                    <option key={voice.id} value={voice.id}>
                      {voice.name} — {voice.description}
                    </option>
                  ))}
                </select>
                <div className="absolute inset-y-0 right-0 flex items-center px-3 pointer-events-none">
                  <svg
                    className="w-4 h-4 text-[var(--color-text-muted)]"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </div>
              </div>
            </div>

            {/* Model Input */}
            <div className="space-y-2">
              <label
                htmlFor="model"
                className="block text-sm font-medium text-[var(--color-text-secondary)]"
              >
                <span className="flex items-center gap-2">
                  <BrainCircuit className="w-4 h-4" />
                  Model
                </span>
              </label>
              <input
                type="text"
                id="model"
                value={ttsSettings.model}
                onChange={(e) => updateTtsSetting('model', e.target.value)}
                disabled={!ttsSettings.enabled}
                placeholder="eleven_multilingual_v2"
                className="w-full px-3 py-2.5 bg-[var(--color-bg-input)] border border-[var(--color-border-primary)] rounded-md text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-border-focus)] focus:ring-1 focus:ring-[var(--color-border-focus)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>

            {/* Speed Slider */}
            <div className="space-y-3">
              <label
                htmlFor="speed"
                className="block text-sm font-medium text-[var(--color-text-secondary)]"
              >
                <span className="flex items-center gap-2">
                  <Gauge className="w-4 h-4" />
                  Speech Speed
                </span>
              </label>
              <div className="flex items-center gap-4">
                <input
                  type="range"
                  id="speed"
                  min="0.5"
                  max="2.0"
                  step="0.1"
                  value={ttsSettings.speed}
                  onChange={(e) => updateTtsSetting('speed', parseFloat(e.target.value))}
                  disabled={!ttsSettings.enabled}
                  className="flex-1 h-2 bg-[var(--color-bg-tertiary)] rounded-lg appearance-none cursor-pointer accent-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: `linear-gradient(to right, var(--color-accent-primary) 0%, var(--color-accent-primary) ${
                      ((ttsSettings.speed - 0.5) / (2.0 - 0.5)) * 100
                    }%, var(--color-bg-tertiary) ${
                      ((ttsSettings.speed - 0.5) / (2.0 - 0.5)) * 100
                    }%, var(--color-bg-tertiary) 100%)`,
                  }}
                />
                <span className="text-sm font-medium text-[var(--color-text-primary)] min-w-[3rem] text-right">
                  {ttsSettings.speed.toFixed(1)}x
                </span>
              </div>
            </div>

            {/* Format Dropdown */}
            <div className="space-y-2">
              <label
                htmlFor="format"
                className="block text-sm font-medium text-[var(--color-text-secondary)]"
              >
                <span className="flex items-center gap-2">
                  <FileAudio className="w-4 h-4" />
                  Audio Format
                </span>
              </label>
              <div className="relative">
                <select
                  id="format"
                  value={ttsSettings.format}
                  onChange={(e) => updateTtsSetting('format', e.target.value)}
                  disabled={!ttsSettings.enabled}
                  className="w-full px-3 py-2.5 bg-[var(--color-bg-input)] border border-[var(--color-border-primary)] rounded-md text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-border-focus)] focus:ring-1 focus:ring-[var(--color-border-focus)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed appearance-none cursor-pointer"
                >
                  {AUDIO_FORMATS.map((format) => (
                    <option key={format.id} value={format.id}>
                      {format.name} — {format.description}
                    </option>
                  ))}
                </select>
                <div className="absolute inset-y-0 right-0 flex items-center px-3 pointer-events-none">
                  <svg
                    className="w-4 h-4 text-[var(--color-text-muted)]"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ========================================
            SPEECH TO TEXT SECTION
            ======================================== */}
        <section className="space-y-5">
          <div className="flex items-center gap-2">
            <Mic className="w-5 h-5 text-[var(--color-accent-primary)]" />
            <h3 className="text-base font-semibold text-[var(--color-text-primary)]">Speech to Text</h3>
          </div>

          <div className="space-y-5 pl-4 border-l-2 border-[var(--color-border-primary)]">
            {/* STT Language Selector */}
            <div className="space-y-2">
              <label
                htmlFor="sttLanguage"
                className="block text-sm font-medium text-[var(--color-text-secondary)]"
              >
                <span className="flex items-center gap-2">
                  <Languages className="w-4 h-4" />
                  Language
                </span>
              </label>
              <div className="relative">
                <select
                  id="sttLanguage"
                  value={sttSettings.language}
                  onChange={(e) => updateSttSetting('language', e.target.value)}
                  className="w-full px-3 py-2.5 bg-[var(--color-bg-input)] border border-[var(--color-border-primary)] rounded-md text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-border-focus)] focus:ring-1 focus:ring-[var(--color-border-focus)] transition-colors appearance-none cursor-pointer"
                >
                  {STT_LANGUAGES.map((lang) => (
                    <option key={lang.id} value={lang.id}>
                      {lang.name} — {lang.description}
                    </option>
                  ))}
                </select>
                <div className="absolute inset-y-0 right-0 flex items-center px-3 pointer-events-none">
                  <svg
                    className="w-4 h-4 text-[var(--color-text-muted)]"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </div>
              </div>
            </div>

            {/* Partial Results Toggle */}
            <div className="flex items-center justify-between p-4 bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border-primary)]">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-md bg-[var(--color-accent-subtle)] flex items-center justify-center">
                  <Eye className="w-4 h-4 text-[var(--color-accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--color-text-primary)]">
                    Partial Results
                  </label>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Display transcription while speaking
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => updateSttSetting('enablePartials', !sttSettings.enablePartials)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)]/50 ${
                  sttSettings.enablePartials ? 'bg-[var(--color-accent-primary)]' : 'bg-[var(--color-bg-tertiary)]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    sttSettings.enablePartials ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          </div>
        </section>
      </fieldset>

      {/* Submit Button */}
      <div className="mt-8 pt-6 border-t border-[var(--color-border-primary)]">
        <button
          type="submit"
          disabled={saveStatus === 'loading'}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-[var(--color-accent-primary)] hover:bg-[var(--color-accent-hover)] active:bg-[var(--color-accent-active)] text-white font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)]/50"
        >
          {saveStatus === 'loading' ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Saving...</span>
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              <span>Save Changes</span>
            </>
          )}
        </button>
      </div>
    </form>
  );
}
