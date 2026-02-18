'use client';

import { useState } from 'react';


interface SttSettings {
  language: string;
  enablePartials: boolean;
}

interface TtsSettings {
  voice: string;
  model: string;
  speed: number;
  format: string;
}

interface SettingsPanelProps {
  sttSettings?: SttSettings;
  setSttSettings?: (settings: SttSettings) => void;
}

export default function SettingsPanel({ sttSettings, setSttSettings }: SettingsPanelProps) {
  const [ttsSettings, setTtsSettings] = useLocalStorage<TtsSettings>('tts-settings', {
    voice: 'rachel',
    model: 'eleven_multilingual_v2',
    speed: 1.0,
    format: 'mp3_44100_128',
  });

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || '';

  return (
    <div className="border-t bg-white">
      <div className="px-4 py-3 border-b">
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
            Speech To Text
          </p>
        </div>
        
        {sttSettings && setSttSettings && (
          <div className="space-y-3 text-xs text-gray-700">
            <div>
              <label className="block mb-1 text-gray-500">Language</label>
              <select
                className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
                value={sttSettings.language}
                onChange={(e) =>
                  setSttSettings({
                    ...sttSettings,
                    language: e.target.value,
                  })
                }
              >
                <option value="en">English</option>
                <option value="es">Spanish</option>
                <option value="fr">French</option>
                <option value="de">German</option>
                <option value="it">Italian</option>
                <option value="pt">Portuguese</option>
                <option value="pl">Polish</option>
                <option value="hi">Hindi</option>
                <option value="ja">Japanese</option>
                <option value="zh">Chinese</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="stt-partials"
                className="h-4 w-4 accent-blue-600"
                checked={sttSettings.enablePartials}
                onChange={(e) =>
                  setSttSettings({
                    ...sttSettings,
                    enablePartials: e.target.checked,
                  })
                }
              />
              <label htmlFor="stt-partials" className="text-gray-600">
                Show partial results
              </label>
            </div>
          </div>
        )}
      </div>

      <div className="px-4 py-3 border-b">
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
            Text To Speech
          </p>
        </div>

        <div className="space-y-3 text-xs text-gray-700">
          <div>
            <label className="block mb-1 text-gray-500">Voice</label>
            <select
              className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
              value={ttsSettings.voice}
              onChange={(e) => setTtsSettings({ ...ttsSettings, voice: e.target.value })}
            >
              <option value="rachel">Rachel</option>
              <option value="sam">Sam</option>
              <option value="james">James</option>
              <option value="ari">Ari</option>
              <option value="adam">Adam</option>
              <option value="drew">Drew</option>
              <option value="clyde">Clyde</option>
              <option value="diana">Diana</option>
              <option value="ellen">Ellen</option>
              <option value="fiona">Fiona</option>
              <option value="george">George</option>
              <option value="grace">Grace</option>
              <option value="henry">Henry</option>
              <option value="io">Io</option>
              <option value="jenny">Jenny</option>
              <option value="kevin">Kevin</option>
              <option value="lily">Lily</option>
              <option value="marcus">Marcus</option>
              <option value="michelle">Michelle</option>
              <option value="patrick">Patrick</option>
              <option value="rachel">Rachel</option>
              <option value="sam">Sam</option>
              <option value="sarah">Sarah</option>
              <option value="steve">Steve</option>
              <option value="tiffany">Tiffany</option>
              <option value="tim">Tim</option>
              <option value="will">Will</option>
            </select>
          </div>

          <div>
            <label className="block mb-1 text-gray-500">Model</label>
            <select
              className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
              value={ttsSettings.model}
              onChange={(e) => setTtsSettings({ ...ttsSettings, model: e.target.value })}
            >
              <option value="eleven_multilingual_v2">Eleven v2 (Multilingual)</option>
              <option value="eleven_monolingual_v1">Eleven v1 (English only)</option>
              <option value="eleven_turbo_v2">Turbo v2 (Fast)</option>
            </select>
          </div>

          <div>
            <label className="block mb-1 text-gray-500">Speed: {ttsSettings.speed.toFixed(1)}x</label>
            <input
              type="range"
              min="0.5"
              max="2.0"
              step="0.1"
              className="w-full accent-blue-600"
              value={ttsSettings.speed}
              onChange={(e) => setTtsSettings({ ...ttsSettings, speed: parseFloat(e.target.value) })}
            />
          </div>

          <div>
            <label className="block mb-1 text-gray-500">Format</label>
            <select
              className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
              value={ttsSettings.format}
              onChange={(e) => setTtsSettings({ ...ttsSettings, format: e.target.value })}
            >
              <option value="mp3_44100_128">MP3 128kbps</option>
              <option value="mp3_44100_256">MP3 256kbps</option>
              <option value="mp3_44100_320">MP3 320kbps</option>
              <option value="pcm_16000">PCM 16kHz</option>
              <option value="pcm_22050">PCM 22kHz</option>
              <option value="pcm_24000">PCM 24kHz</option>
              <option value="pcm_44100">PCM 44kHz</option>
              <option value="ulaw_8000">μ-law 8kHz</option>
            </select>
          </div>
        </div>
      </div>

      <div className="px-4 py-3">
        <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Memory</p>
        
        {!showDeleteConfirm ? (
          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="w-full px-3 py-2 text-xs text-red-600 border border-red-200 rounded-md hover:bg-red-50 transition-colors"
          >
            Clear All Memory
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-red-600">Are you sure? This will delete all conversations and memories.</p>
            <div className="flex gap-2">
              <button
                onClick={async () => {
                  try {
                    await fetch(`${apiBaseUrl}/memory/all`, {
                      method: 'DELETE',
                      headers: { 'Authorization': `Bearer ${localStorage.getItem('daemon_api_key')}` }
                    });
                    window.location.reload();
                  } catch (error) {
                    console.error('Failed to clear memory:', error);
                  }
                }}
                className="flex-1 px-3 py-2 text-xs text-white bg-red-600 rounded-md hover:bg-red-700 transition-colors"
              >
                Yes, Clear All
              </button>
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="flex-1 px-3 py-2 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void] {
  const [storedValue, setStoredValue] = useState<T>(() => {
    if (typeof window === 'undefined') {
      return initialValue;
    }
    try {
      const item = window.localStorage.getItem(key);
      return item ? JSON.parse(item) : initialValue;
    } catch (error) {
      console.error(`Error loading ${key} from localStorage:`, error);
      return initialValue;
    }
  });

  const setValue = (value: T) => {
    try {
      setStoredValue(value);
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(key, JSON.stringify(value));
      }
    } catch (error) {
      console.error(`Error saving ${key} to localStorage:`, error);
    }
  };

  return [storedValue, setValue];
}
