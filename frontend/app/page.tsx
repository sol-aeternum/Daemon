'use client';

import { ChatInputBar } from '../components/ChatInputBar';
import { useChat } from '@ai-sdk/react';
import { useState, useRef, useEffect, Suspense, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { FilePreview } from '../src/components/FilePreview';
import { useStt } from '../hooks/useStt';
import { ErrorProvider, useError } from '../components/ErrorProvider';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { ConnectionStatus } from '../components/ConnectionStatus';
import {
  ConversationList,
  type SidebarSection,
} from '../components/ConversationList';
import { ToolCallLog } from '../components/ToolCallBlock';
import { MobileHeader } from '../components/MobileHeader';
import ChatSkeleton from '../components/ChatSkeleton';
import { useConversationHistory } from '../hooks/useConversationHistory';
import {
  ConversationHistoryProvider,
  useConversationHistoryContext,
} from '../components/ConversationHistoryProvider';
import { AudioPlaybackProvider } from '../components/AudioPlaybackProvider';
import { useEventArchive } from '../hooks/useEventArchive';
import { useStopGeneration } from '../hooks/useStopGeneration';
import { useStopShortcut } from '../hooks/useStopShortcut';
import { formatMessageContent } from '../lib/format';
import { useAgentStatus } from '../hooks/useAgentStatus';
import { AgentStatusList } from '../components/AgentStatusList';
import { OfflineIndicator } from '../components/OfflineIndicator';
import { RetryButton } from '../components/RetryButton';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useClientMounted } from '../hooks/useClientMounted';
import { MicButton } from '../components/MicButton';
import { TextToSpeechButton } from '../components/TextToSpeechButton';
import { StreamingTtsMessage } from '../components/StreamingTtsMessage';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { refreshIfNeeded, getAuthHeader } from '../lib/auth';
import { ThinkingIndicator } from '../components/ThinkingIndicator';
import MarkdownMessage from '../components/MarkdownMessage';
import { FileDownloadCard } from '../components/FileDownloadCard';
import { SkeletonBlock } from '../components/ui/Skeleton';
import {
  ChatEvent,
  isChatEvent,
  isCouncilEvent,
  isCouncilInterviewEvent,
  isCouncilProgressEvent,
  isCouncilOutputEvent,
  isCouncilDoneEvent,
} from '../lib/events';
import {
  Search,
  Image,
  Code,
  MessageSquare,
  Sparkles,
  X,
  Eye,
  EyeOff,
} from 'lucide-react';
import { CouncilInterviewCard } from '../components/council/CouncilInterviewCard';
import { CouncilProgress } from '../components/council/CouncilProgress';
import { CouncilOutputViewer } from '../components/council/CouncilOutputViewer';
import { Message } from 'ai';

type ReasoningMessage = Message & {
  reasoning_text?: string;
  reasoning_duration_secs?: number;
  reasoning_model?: string;
};

type PersistedToolCall = {
  name?: unknown;
  arguments?: unknown;
  id?: unknown;
  request_id?: unknown;
};

type PersistedToolResult = {
  name?: unknown;
  result?: unknown;
  id?: unknown;
  request_id?: unknown;
};

type PendingAttachment = {
  id: string;
  file: File;
};

type OutboundAttachmentKind = 'image' | 'text' | 'binary';

type OutboundAttachment = {
  id: string;
  name: string;
  mime_type: string;
  size: number;
  kind: OutboundAttachmentKind;
  data_url?: string;
  text_content?: string;
};

type DocumentDownloadInfo = {
  fileUrl: string;
  filename: string;
  fileType?: string;
  fileSize?: number;
};

const MAX_ATTACHMENT_TEXT_LENGTH = 8000;

const getOptionalString = (value: unknown): string | undefined => {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
};

const toRecord = (value: unknown): Record<string, unknown> | undefined => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
};

const getPersistedToolEvents = (message: Message): ChatEvent[] => {
  const messageWithTools = message as Message & {
    tool_calls?: unknown;
    tool_results?: unknown;
    metadata?: unknown;
  };

  const rawToolCalls = Array.isArray(messageWithTools.tool_calls)
    ? (messageWithTools.tool_calls as PersistedToolCall[])
    : [];
  const rawToolResults = Array.isArray(messageWithTools.tool_results)
    ? (messageWithTools.tool_results as PersistedToolResult[])
    : [];
  const metadata = toRecord(messageWithTools.metadata) || {};
  const metadataCouncilEvents = Array.isArray(metadata.council_events)
    ? metadata.council_events
    : [];

  const events: ChatEvent[] = [];
  const seenEventKeys = new Set<string>();

  const pushEvent = (event: ChatEvent) => {
    const key = event.id ? `id:${event.id}` : `json:${JSON.stringify(event)}`;
    if (seenEventKeys.has(key)) {
      return;
    }
    seenEventKeys.add(key);
    events.push(event);
  };

  for (const toolCall of rawToolCalls) {
    const event: Extract<ChatEvent, { type: 'tool_call' }> = {
      type: 'tool_call',
      name: getOptionalString(toolCall.name) || 'tool',
      arguments: toRecord(toolCall.arguments) || {},
    };

    const id = getOptionalString(toolCall.id);
    if (id) event.id = id;
    const requestId = getOptionalString(toolCall.request_id);
    if (requestId) event.request_id = requestId;

    pushEvent(event);
  }

  for (const toolResult of rawToolResults) {
    const event: Extract<ChatEvent, { type: 'tool_result' }> = {
      type: 'tool_result',
      name: getOptionalString(toolResult.name) || 'tool',
      result: toolResult.result,
    };

    const id = getOptionalString(toolResult.id);
    if (id) event.id = id;
    const requestId = getOptionalString(toolResult.request_id);
    if (requestId) event.request_id = requestId;

    pushEvent(event);
  }

  for (const candidate of metadataCouncilEvents) {
    if (isChatEvent(candidate)) {
      pushEvent(candidate);
    }
  }

  for (const toolResult of rawToolResults) {
    if (getOptionalString(toolResult.name) !== 'council_events') {
      continue;
    }
    const resultRecord = toRecord(toolResult.result);
    const resultEvents = Array.isArray(resultRecord?.events)
      ? resultRecord?.events
      : [];
    for (const candidate of resultEvents) {
      if (isChatEvent(candidate)) {
        pushEvent(candidate);
      }
    }
  }

  return events;
};

const getPartContent = (part: unknown): string => {
  if (typeof part !== 'object' || part === null) {
    return '';
  }

  const textValue = (part as { text?: unknown }).text;
  if (typeof textValue === 'string') {
    return textValue;
  }

  const contentValue = (part as { content?: unknown }).content;
  if (typeof contentValue === 'string') {
    return contentValue;
  }

  return '';
};

const getMessageContent = (message: Message): string => {
  const rawContent = message.content;
  if (typeof rawContent === 'string' && rawContent.trim().length > 0) {
    return rawContent;
  }

  const messageWithParts = message as Message & { parts?: unknown };
  if (Array.isArray(messageWithParts.parts)) {
    const combinedParts = messageWithParts.parts.map(getPartContent).join('');
    if (combinedParts.trim().length > 0) {
      return combinedParts;
    }
  }

  return typeof rawContent === 'string' ? rawContent : '';
};

const parseToolResultRecord = (
  value: unknown,
): Record<string, unknown> | undefined => {
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return toRecord(parsed);
    } catch {
      return undefined;
    }
  }
  return toRecord(value);
};

const getDocumentDownloadFromEvents = (
  events: ChatEvent[],
): DocumentDownloadInfo | undefined => {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event.type !== 'tool_result') continue;

    const parsed = parseToolResultRecord(event.result);
    if (!parsed) continue;

    const data = toRecord(parsed.data);
    const fileUrl = getOptionalString(data?.file_url ?? parsed.file_url);
    if (!fileUrl || !fileUrl.startsWith('/generated-files/')) continue;

    const filename =
      getOptionalString(data?.filename ?? parsed.filename) ||
      fileUrl.split('/').pop() ||
      'download';
    const fileType = getOptionalString(data?.format ?? parsed.format);

    const fromDataSize = data?.file_size;
    const fromRootSize = parsed.file_size;
    const fileSize =
      typeof fromDataSize === 'number'
        ? fromDataSize
        : typeof fromRootSize === 'number'
          ? fromRootSize
          : undefined;

    return { fileUrl, filename, fileType, fileSize };
  }

  return undefined;
};

const getModelName = (modelId: string | undefined): string | undefined => {
  if (!modelId) return undefined;
  const parts = modelId.split('/');
  const shortName = parts[parts.length - 1];
  return shortName
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
};

const isTextLikeFile = (file: File) => {
  const type = file.type.toLowerCase();
  if (type.startsWith('text/')) return true;
  return (
    type.includes('json') ||
    type.includes('xml') ||
    type.includes('javascript') ||
    type.includes('typescript') ||
    type.includes('markdown') ||
    type.includes('yaml') ||
    type.includes('csv')
  );
};

const fileToDataUrl = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === 'string' && result.startsWith('data:')) {
        resolve(result);
        return;
      }
      reject(new Error('Failed to read file as data URL'));
    };
    reader.onerror = () => {
      reject(reader.error || new Error('Failed to read file'));
    };
    reader.readAsDataURL(file);
  });
};

const isRoutingEvent = (
  event: ChatEvent,
): event is Extract<ChatEvent, { type: 'routing' }> => event.type === 'routing';
import {
  TtsSettings,
  SttSettings,
  DEFAULT_TTS_SETTINGS,
  DEFAULT_STT_SETTINGS,
} from '../lib/constants';

const hasCouncilEvents = (events: ChatEvent[]): boolean => {
  return events.some(isCouncilEvent);
};

const getCouncilInterviewEvent = (
  events: ChatEvent[],
): ChatEvent | undefined => {
  return events.find(isCouncilInterviewEvent);
};

const getLatestCouncilProgressEvent = (
  events: ChatEvent[],
): ChatEvent | undefined => {
  const progressEvents = events.filter(isCouncilProgressEvent);
  return progressEvents[progressEvents.length - 1];
};

const getCouncilOutputEvents = (events: ChatEvent[]): ChatEvent[] => {
  return events.filter((e) => isCouncilOutputEvent(e) || isCouncilDoneEvent(e));
};

const shouldShowCouncilProgress = (events: ChatEvent[]): boolean => {
  const hasProgress = events.some(isCouncilProgressEvent);
  const hasError = events.some((e) => e.type === 'council_error');
  return hasProgress && !hasError;
};

// =============================================================================
// WELCOME SCREEN COMPONENT
// =============================================================================

interface WelcomeScreenProps {
  setInput: (input: string) => void;
  onSubmit: (e?: { preventDefault?: () => void }) => void;
}

const getTimeGreeting = () => {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) {
    return 'Good morning';
  }
  if (hour >= 12 && hour < 17) {
    return 'Good afternoon';
  }
  return 'Good evening';
};

function WelcomeScreen({ setInput, onSubmit }: WelcomeScreenProps) {
  const isClientMounted = useClientMounted();
  const greeting = isClientMounted ? getTimeGreeting() : 'Good evening';
  const userName =
    isClientMounted && typeof localStorage !== 'undefined'
      ? localStorage.getItem('user_name')
      : null;

  const quickActions = [
    {
      icon: Search,
      label: 'Research',
      starter: 'I need to research...',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: Image,
      label: 'Create Image',
      starter: 'Create an image of...',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: Code,
      label: 'Write Code',
      starter: 'Write a function that...',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: MessageSquare,
      label: 'Just Chat',
      starter: '',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
  ];

  const handleActionClick = (starter: string) => {
    setInput(starter);
    // Focus the input after a short delay to allow state update
    setTimeout(() => {
      const inputEl = document.querySelector(
        'input[type="text"], textarea',
      ) as HTMLElement;
      inputEl?.focus();
      // If "Just Chat" was clicked (empty starter), don't submit
      if (starter) {
        // Let user continue typing, don't auto-submit
      }
    }, 50);
  };

  return (
    <div className="flex flex-col items-center justify-center h-full px-4 animate-fade-in">
      <div className="flex flex-col items-center max-w-2xl w-full space-y-8">
        {/* Logo / Wordmark */}
        <div className="flex items-center gap-3 mb-2">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full" />
            <div className="relative w-12 h-12 rounded-xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles className="w-6 h-6 text-white" />
            </div>
          </div>
          <span className="text-3xl font-bold tracking-tight text-[var(--color-text-primary)]">
            Daemon
          </span>
        </div>

        {/* Greeting */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl md:text-5xl font-bold text-[var(--color-text-primary)] tracking-tight">
            {userName ? `${greeting}, ${userName}` : greeting}
          </h1>
          <p className="text-lg text-[var(--color-text-muted)]">
            What would you like to do today?
          </p>
        </div>

        {/* Quick Action Chips */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 w-full mt-8">
          {quickActions.map((action, index) => {
            const Icon = action.icon;
            return (
              <button
                key={action.label}
                onClick={() => handleActionClick(action.starter)}
                className="group relative flex flex-col items-center gap-3 p-4 rounded-2xl bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] hover:border-[var(--color-accent-primary)] transition-all duration-300 hover:shadow-lg hover:-translate-y-1 overflow-hidden"
                style={{
                  animationDelay: `${index * 100}ms`,
                }}
              >
                {/* Gradient background on hover */}
                <div
                  className={`absolute inset-0 bg-gradient-to-br ${action.gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-300`}
                />

                {/* Icon */}
                <div className="relative z-10 w-10 h-10 rounded-xl bg-[var(--color-bg-tertiary)] group-hover:bg-[var(--color-bg-primary)] flex items-center justify-center transition-colors duration-300">
                  <Icon className={`w-5 h-5 ${action.iconColor}`} />
                </div>

                {/* Label */}
                <span className="relative z-10 text-sm font-medium text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)] transition-colors duration-300">
                  {action.label}
                </span>
              </button>
            );
          })}
        </div>

        {/* Hint text */}
        <p className="text-sm text-[var(--color-text-muted)] mt-8 text-center">
          Or type your message below to get started
        </p>
      </div>
    </div>
  );
}

// =============================================================================
// MAIN CHAT CONTENT
// =============================================================================

function ChatContent() {
  const { value: ttsSettings } = useLocalStorage<TtsSettings>(
    'tts_settings',
    DEFAULT_TTS_SETTINGS,
  );

  const { value: sttSettings, setValue: setSttSettings } =
    useLocalStorage<SttSettings>('stt_settings', DEFAULT_STT_SETTINGS);

  const effectiveSttSettings = sttSettings || DEFAULT_STT_SETTINGS;

  const {
    isRecording,
    isConnecting,
    start,
    stop,
    error: sttError,
  } = useStt({
    onTranscript: (text) => setInput(text),
    onPartialTranscript: (text) => setInput(text),
    language: effectiveSttSettings.language,
    enablePartials: effectiveSttSettings.enablePartials,
    debounceMs: 100,
  });

  const [connectionStatus, setConnectionStatus] = useState<
    'connected' | 'disconnected' | 'reconnecting'
  >('connected');
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [openedPreviewFileUrl, setOpenedPreviewFileUrl] = useState<
    string | null
  >(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const autoScrollEnabledRef = useRef(true);
  const lastMessageSignatureRef = useRef('');
  const { showError } = useError();
  const { isOnline } = useOnlineStatus();
  const router = useRouter();

  const {
    conversations,
    currentId,
    isLoaded,
    createConversation,
    updateConversation,
    deleteConversation,
    getCurrentConversation,
    switchConversation,
    setConversationModel,
    searchQuery,
    setSearchQuery,
    refreshConversations,
  } = useConversationHistoryContext();

  const [activeModel, setActiveModel] = useState<string>('auto');
  const [pendingAttachments, setPendingAttachments] = useState<
    PendingAttachment[]
  >([]);
  // State to store events for past messages
  const [archivedEvents, setArchivedEvents] = useState<
    Record<
      string,
      { events: ChatEvent[]; duration: number; requestId?: string | null }
    >
  >({});
  const [thoughtFallbackByMessageId, setThoughtFallbackByMessageId] = useState<
    Record<string, string>
  >({});

  const currentConversation = getCurrentConversation();

  useEffect(() => {
    if (currentConversation?.selectedModel) {
      const selectedModel = currentConversation.selectedModel;
      queueMicrotask(() => setActiveModel(selectedModel));
    }
  }, [currentConversation]);

  useEffect(() => {
    queueMicrotask(() => {
      setThoughtFallbackByMessageId({});
      setOpenedPreviewFileUrl(null);
    });
  }, [currentId]);

  // Ref to track current duration for onFinish access
  const thinkingDurationRef = useRef<number>(0);

  // Ref to track current events for onFinish access
  const eventsRef = useRef<ChatEvent[]>([]);

  const lastArchivedEventKeysRef = useRef<Set<string>>(new Set());
  const currentRequestIdRef = useRef<string | null>(null);
  const latestConversationIdRef = useRef<string | null>(currentId);
  const autoOpenedPreviewFileUrlsRef = useRef<Set<string>>(new Set());
  const titleRefreshTimeoutsRef = useRef<number[]>([]);
  const scheduledTitleRefreshConversationIdsRef = useRef<Set<string>>(
    new Set(),
  );

  useEffect(() => {
    latestConversationIdRef.current = currentId;
  }, [currentId]);

  const eventKey = (event: ChatEvent) => {
    if (event.id) return `id:${event.id}`;
    return `json:${JSON.stringify(event)}`;
  };

  const normalizeThinkingText = (content: string): string => {
    const normalizedNewlines = content
      .replace(/\r\n/g, '\n')
      .replace(/\t/g, ' ');
    const lines = normalizedNewlines
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);

    const shortLineRatio = lines.length
      ? lines.filter((line) => line.split(/\s+/).length <= 2).length /
        lines.length
      : 0;

    const looksTokenFragmented = lines.length >= 12 && shortLineRatio > 0.65;

    if (looksTokenFragmented) {
      return normalizedNewlines
        .replace(/\s*\n+\s*/g, ' ')
        .replace(/\s{2,}/g, ' ')
        .trim();
    }

    const normalizedParagraphs = normalizedNewlines
      .split(/\n{3,}/)
      .map((paragraph) =>
        paragraph
          .replace(/[ \t]*\n[ \t]*/g, ' ')
          .replace(/\s{2,}/g, ' ')
          .trim(),
      )
      .filter(Boolean);

    return normalizedParagraphs.join('\n\n');
  };

  const getThinkingContent = (msgEvents: ChatEvent[]) => {
    const rawContent = msgEvents
      .filter((e) => e.type === 'thinking')
      .map((e) => e.content)
      .join('');
    return normalizeThinkingText(rawContent);
  };

  const {
    messages,
    input,
    setInput,
    handleInputChange,
    append,
    setMessages,
    isLoading,
    error,
    reload,
    data,
    stop: stopChat,
  } = useChat({
    api: '/api/chat',
    body: { id: currentId || null },
    id: currentId || undefined,
    initialMessages: currentConversation?.messages || [],
    fetch: async (input, init) => {
      await refreshIfNeeded();
      const body = init?.body ? JSON.parse(init.body as string) : {};
      body.model = activeModel;
      if (body.id === undefined || body.id === null) {
        body.id = currentId || latestConversationIdRef.current || null;
      }
      const headers = new Headers(init?.headers);
      const authHeader = getAuthHeader();
      if (authHeader) {
        headers.set('Authorization', authHeader);
      }
      return fetch(input, {
        ...init,
        headers,
        body: JSON.stringify(body),
      });
    },
    onFinish: (message) => {
      setConnectionStatus('connected');
      const thoughtAtFinish = getThinkingContent(eventsRef.current);
      if (thoughtAtFinish.trim().length > 0) {
        setThoughtFallbackByMessageId((prev) => ({
          ...prev,
          [message.id]: thoughtAtFinish,
        }));
      }
      if (eventsRef.current.length > 0) {
        archiveCurrentEvents(message.id);
      }
      thinkingDurationRef.current = 0;
    },
    onError: (err) => {
      showError(err.message || 'Chat error occurred');
      setConnectionStatus('disconnected');
    },
  });

  useEffect(() => {
    if (isLoading) {
      return;
    }
    if (
      !currentConversation?.messages ||
      currentConversation.messages.length === 0
    ) {
      return;
    }
    if (messages.length !== 0) {
      return;
    }
    setMessages(currentConversation.messages);
  }, [currentConversation?.messages, isLoading, messages.length, setMessages]);

  const attachmentItems = useMemo(
    () =>
      pendingAttachments.map((attachment) => ({
        id: attachment.id,
        name: attachment.file.name,
        size: attachment.file.size,
      })),
    [pendingAttachments],
  );

  const handleAttachFiles = (files: FileList) => {
    const incomingFiles = Array.from(files);
    setPendingAttachments((prev) => {
      const next = [...prev];
      const seen = new Set(
        prev.map(
          (item) =>
            `${item.file.name}:${item.file.size}:${item.file.lastModified}`,
        ),
      );

      for (const file of incomingFiles) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (seen.has(key)) continue;
        if (next.length >= 6) break;
        next.push({
          id: `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
          file,
        });
        seen.add(key);
      }

      return next;
    });
  };

  const handleRemoveAttachment = (id: string) => {
    setPendingAttachments((prev) =>
      prev.filter((attachment) => attachment.id !== id),
    );
  };

  const serializeAttachments = async (
    attachments: PendingAttachment[],
  ): Promise<OutboundAttachment[]> => {
    const serialized = await Promise.all(
      attachments.map(async ({ id, file }) => {
        const mimeType = file.type || 'application/octet-stream';

        if (mimeType.startsWith('image/')) {
          try {
            const dataUrl = await fileToDataUrl(file);
            const imageAttachment: OutboundAttachment = {
              id,
              name: file.name,
              mime_type: mimeType,
              size: file.size,
              kind: 'image',
              data_url: dataUrl,
            };
            return imageAttachment;
          } catch {
            const failedImageAttachment: OutboundAttachment = {
              id,
              name: file.name,
              mime_type: mimeType,
              size: file.size,
              kind: 'binary',
            };
            return failedImageAttachment;
          }
        }

        if (isTextLikeFile(file)) {
          try {
            const raw = await file.text();
            const trimmed = raw.trim();
            const limited =
              trimmed.length > MAX_ATTACHMENT_TEXT_LENGTH
                ? `${trimmed.slice(0, MAX_ATTACHMENT_TEXT_LENGTH)}\n... (truncated)`
                : trimmed;
            const textAttachment: OutboundAttachment = {
              id,
              name: file.name,
              mime_type: mimeType,
              size: file.size,
              kind: 'text',
              text_content: limited || '(empty file)',
            };
            return textAttachment;
          } catch {
            const failedTextAttachment: OutboundAttachment = {
              id,
              name: file.name,
              mime_type: mimeType,
              size: file.size,
              kind: 'binary',
            };
            return failedTextAttachment;
          }
        }

        const binaryAttachment: OutboundAttachment = {
          id,
          name: file.name,
          mime_type: mimeType,
          size: file.size,
          kind: 'binary',
        };
        return binaryAttachment;
      }),
    );

    return serialized;
  };

  const submitChat = async () => {
    if (isLoading && messages.length > 0) return;

    const trimmedInput = input.trim();
    const attachments =
      pendingAttachments.length > 0
        ? await serializeAttachments(pendingAttachments)
        : [];
    const content =
      trimmedInput ||
      (attachments.length > 0
        ? `Attached ${attachments.length} file${attachments.length === 1 ? '' : 's'}.`
        : '');

    if (!content) return;

    // Snapshot the values we are about to submit so we can preserve any
    // newer edits the user typed or picked while the request was in flight.
    // Without this snapshot, clicking Stop (which resolves `append()`) would
    // unconditionally `setInput("")` and `setPendingAttachments([])`, erasing
    // drafts the user composed while waiting for the partial response.
    const submittedInput = input;
    const submittedAttachments = pendingAttachments;

    try {
      await append(
        {
          role: 'user',
          content,
        },
        {
          body: {
            id: currentId || latestConversationIdRef.current || null,
            model: activeModel,
            attachments,
          },
        },
      );

      // Only clear fields the user has not edited since the submit fired.
      // The `===` check is reference equality on the string and on the
      // pending-attachment array; any edit changes the reference.
      if (input === submittedInput) {
        setInput('');
      }
      if (pendingAttachments === submittedAttachments) {
        setPendingAttachments([]);
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to send message';
      showError(message);
      setConnectionStatus('disconnected');
    }
  };

  const handleSubmit = (e?: { preventDefault?: () => void }) => {
    if (e && typeof e.preventDefault === 'function') {
      e.preventDefault();
    }
    void submitChat();
  };

  const {
    getEventsForMessage,
    getDurationForMessage,
    archiveCurrentEvents,
    resetArchive,
  } = useEventArchive({
    data: data || [],
    isLoading,
  });

  const inputIsBusy = isLoading && messages.length > 0;
  const {
    stoppedMessageIds,
    stopGeneration: handleStopGeneration,
    resetStoppedMessages,
  } = useStopGeneration({
    messages,
    stop: stopChat,
    archiveEvents: archiveCurrentEvents,
  });

  useStopShortcut({
    active: inputIsBusy,
    onStop: handleStopGeneration,
  });

  const persistedMessagesById = useMemo(() => {
    const entries = (currentConversation?.messages || []).reduce<
      Array<[string, ReasoningMessage]>
    >((acc, message) => {
      if (message.id) {
        acc.push([message.id, message as ReasoningMessage]);
      }
      return acc;
    }, []);
    return new Map<string, ReasoningMessage>(entries);
  }, [currentConversation?.messages]);

  const persistedToolEventsByMessageId = useMemo(() => {
    const entries = (currentConversation?.messages || []).reduce<
      Array<[string, ChatEvent[]]>
    >((acc, message) => {
      if (message.id) {
        acc.push([message.id, getPersistedToolEvents(message)]);
      }
      return acc;
    }, []);
    return new Map<string, ChatEvent[]>(entries);
  }, [currentConversation?.messages]);

  const prevLoadingRef = useRef(isLoading);

  useEffect(() => {
    if (isLoading && !prevLoadingRef.current) {
      currentRequestIdRef.current = null;
      autoScrollEnabledRef.current = true;
      if (eventsRef.current.length > 0) {
        lastArchivedEventKeysRef.current = new Set(
          eventsRef.current.map(eventKey),
        );
      }
    }
    prevLoadingRef.current = isLoading;
  }, [isLoading]);

  // Auto-scroll: Track scroll position to respect user's reading position
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const { scrollTop, clientHeight, scrollHeight } = container;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      const isNearBottom = distanceFromBottom < 64;
      isNearBottomRef.current = isNearBottom;
      autoScrollEnabledRef.current = isNearBottom;
    };

    handleScroll();
    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    if (!messagesEndRef.current || messages.length === 0) return;
    if (!autoScrollEnabledRef.current) return;

    const lastMessage = messages[messages.length - 1];
    const signature = `${messages.length}:${lastMessage?.id ?? ''}`;
    const isNewMessage = signature !== lastMessageSignatureRef.current;
    lastMessageSignatureRef.current = signature;

    const behavior: ScrollBehavior =
      isLoading && !isNewMessage ? 'auto' : 'smooth';
    messagesEndRef.current.scrollIntoView({ behavior });
  }, [messages, isLoading]);

  const handleSelectConversation = async (id: string) => {
    // Preserve per-message stopped markers across conversation switches —
    // clearing here would silently drop `(stopped)` indicators for the
    // current conversation when the user navigates away and back, since
    // the set is keyed by message ID and IDs are stable across renders.
    await switchConversation(id);
  };

  const handleNewChat = async () => {
    await createConversation();
    setInput('');
    setPendingAttachments([]);
    setArchivedEvents({});
    resetStoppedMessages();
    thinkingDurationRef.current = 0;
    eventsRef.current = [];
    lastArchivedEventKeysRef.current = new Set();
    currentRequestIdRef.current = null;
  };

  const handleSidebarNavigate = (section: SidebarSection) => {
    if (section === 'chats') {
      router.push('/chats');
      return;
    }

    if (section === 'projects') {
      router.push('/projects');
      return;
    }

    if (section === 'artifacts') {
      router.push('/artifacts');
      return;
    }

    if (section === 'studio') {
      router.push('/studio');
      return;
    }
  };

  const handleGoHome = () => {
    router.push('/');
  };

  const flattenedData = useMemo(() => {
    if (!Array.isArray(data)) {
      return [] as unknown[];
    }

    return data.flatMap((entry) => (Array.isArray(entry) ? entry : [entry]));
  }, [data]);

  const events: ChatEvent[] = flattenedData.filter((x): x is ChatEvent =>
    isChatEvent(x),
  );

  // Update ref whenever events change
  useEffect(() => {
    eventsRef.current = events;
    let latestRequestId: string | null = null;
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const requestId = events[i].request_id;
      if (requestId) {
        latestRequestId = requestId;
        break;
      }
    }
    if (latestRequestId || events.length === 0) {
      currentRequestIdRef.current = latestRequestId;
    }
  }, [events]);

  const isConversationDataEvent = (
    value: unknown,
  ): value is { type: 'conversation'; conversation_id: string } => {
    if (!value || typeof value !== 'object') return false;
    const candidate = value as { type?: unknown; conversation_id?: unknown };
    return (
      candidate.type === 'conversation' &&
      typeof candidate.conversation_id === 'string'
    );
  };

  const isCouncilDataEvent = (value: unknown): boolean => {
    if (!value || typeof value !== 'object') return false;
    const candidate = value as { type?: unknown };
    return (
      candidate.type === 'council_interview' ||
      candidate.type === 'council_progress' ||
      candidate.type === 'council_output' ||
      candidate.type === 'council_done' ||
      candidate.type === 'council_error'
    );
  };

  const isCouncilDoneDataEvent = (value: unknown): boolean => {
    if (!value || typeof value !== 'object') return false;
    return (value as { type?: unknown }).type === 'council_done';
  };

  // Capture conversation_id from SSE and update URL (for edge cases)
  const urlUpdatedRef = useRef(false);
  useEffect(() => {
    return () => {
      for (const timeoutId of titleRefreshTimeoutsRef.current) {
        window.clearTimeout(timeoutId);
      }
      titleRefreshTimeoutsRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (flattenedData.length === 0) return;
    const conversationEvent = flattenedData.find(isConversationDataEvent);
    if (!conversationEvent) {
      if (currentId) {
        urlUpdatedRef.current = false;
      }
      return;
    }

    const conversationId = conversationEvent.conversation_id;
    latestConversationIdRef.current = conversationId;
    const hasCouncilEvent = flattenedData.some(isCouncilDataEvent);
    const hasCouncilDoneEvent = flattenedData.some(isCouncilDoneDataEvent);
    const shouldSyncConversationState = !hasCouncilEvent || hasCouncilDoneEvent;

    if (!currentId && !urlUpdatedRef.current) {
      urlUpdatedRef.current = true;
      router.replace(`/?id=${conversationId}`);
    }

    if (
      shouldSyncConversationState &&
      !scheduledTitleRefreshConversationIdsRef.current.has(conversationId)
    ) {
      scheduledTitleRefreshConversationIdsRef.current.add(conversationId);
      for (const delayMs of [2000, 5000]) {
        const timeoutId = window.setTimeout(() => {
          titleRefreshTimeoutsRef.current =
            titleRefreshTimeoutsRef.current.filter((id) => id !== timeoutId);
          void refreshConversations();
        }, delayMs);
        titleRefreshTimeoutsRef.current.push(timeoutId);
      }
    }

    if (currentId) {
      urlUpdatedRef.current = false;
    }
  }, [flattenedData, currentId, refreshConversations, router]);

  const agents = useAgentStatus(events);

  // Track the latest document download for preview panel
  const latestDocumentPreview = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const message = messages[i];
      const liveEvents = getEventsForMessage(
        message.id,
        i === messages.length - 1,
      );
      const persistedToolEvents =
        persistedToolEventsByMessageId.get(message.id) || [];
      const msgEvents =
        liveEvents.length > 0 ? liveEvents : persistedToolEvents;
      const doc = getDocumentDownloadFromEvents(msgEvents);
      if (doc) {
        return {
          doc,
          source:
            liveEvents.length > 0 ? ('live' as const) : ('persisted' as const),
        };
      }
    }
    return undefined;
  })();

  const documentDownload = latestDocumentPreview?.doc;

  useEffect(() => {
    let cancelled = false;

    if (!documentDownload) {
      queueMicrotask(() => {
        if (!cancelled) {
          setOpenedPreviewFileUrl(null);
        }
      });
      return () => {
        cancelled = true;
      };
    }

    if (latestDocumentPreview?.source === 'live') {
      const fileUrl = documentDownload.fileUrl;
      if (!autoOpenedPreviewFileUrlsRef.current.has(fileUrl)) {
        autoOpenedPreviewFileUrlsRef.current.add(fileUrl);
        queueMicrotask(() => {
          if (!cancelled) {
            setOpenedPreviewFileUrl(fileUrl);
          }
        });
      }
    }

    return () => {
      cancelled = true;
    };
  }, [documentDownload, latestDocumentPreview]);

  const showPreviewPanel =
    documentDownload !== undefined &&
    openedPreviewFileUrl === documentDownload.fileUrl;

  return (
    <div className="flex h-screen bg-[var(--color-bg-tertiary)] overflow-hidden">
      {!isOnline && <OfflineIndicator />}
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div
        className={`
        fixed inset-y-0 left-0 z-50 w-[260px] bg-[var(--color-bg-secondary)] transform transition-transform duration-300
        md:relative md:inset-auto md:z-0 md:w-auto md:translate-x-0
        ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}
      >
        <ConversationList
          conversations={conversations}
          currentId={currentId}
          onSelect={(id) => {
            handleSelectConversation(id);
            setIsSidebarOpen(false);
          }}
          onDelete={deleteConversation}
          onUpdate={updateConversation}
          onNewChat={() => {
            handleNewChat();
            setIsSidebarOpen(false);
          }}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          isLoading={!isLoaded}
          activeSection="home"
          onNavigate={handleSidebarNavigate}
          onGoHome={handleGoHome}
        />
      </div>

      <Group orientation="horizontal" className="flex-1 overflow-hidden">
        {/* Left Panel - Chat Content */}
        <Panel
          defaultSize={showPreviewPanel ? 60 : 100}
          minSize={40}
          className="flex flex-col"
        >
          <div className="flex-1 flex flex-col w-full min-w-0 relative">
            {isRecording && (
              <div className="bg-[var(--color-status-error)] text-white px-4 py-2 text-center text-sm font-medium animate-pulse">
                Recording... Tap mic to stop
              </div>
            )}
            <MobileHeader
              title={currentConversation?.title || 'New conversation'}
              onOpenSidebar={() => setIsSidebarOpen(true)}
            >
              <div className="flex items-center gap-2">
                <ConnectionStatus
                  status={connectionStatus}
                  onReconnect={reload}
                />
              </div>
            </MobileHeader>

            <header className="hidden md:flex bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)] px-4 py-3 items-center justify-between">
              <h1 className="text-lg font-semibold">
                {currentConversation?.title || 'New conversation'}
              </h1>
              <div className="flex items-center gap-4">
                <ConnectionStatus
                  status={connectionStatus}
                  onReconnect={reload}
                />
              </div>
            </header>

            <main ref={scrollContainerRef} className="flex-1 overflow-y-auto">
              {messages.length === 0 && isLoading ? (
                <div className="mx-auto w-full max-w-3xl flex flex-col space-y-4 px-4 py-6 animate-fade-in">
                  {/* Assistant message skeleton - left aligned */}
                  <div className="flex flex-col items-start mb-6">
                    <div className="max-w-[85%] md:max-w-[80%] space-y-3">
                      <SkeletonBlock
                        width="60%"
                        height="4rem"
                        className="bg-[var(--color-bg-secondary)]"
                      />
                    </div>
                  </div>
                  {/* User message skeleton - right aligned */}
                  <div className="flex flex-col items-end mb-6">
                    <div className="max-w-[85%] md:max-w-[80%]">
                      <SkeletonBlock
                        width="80%"
                        height="3rem"
                        className="bg-[var(--color-accent-primary)] opacity-60"
                      />
                    </div>
                  </div>
                  {/* Assistant message skeleton - left aligned */}
                  <div className="flex flex-col items-start mb-6">
                    <div className="max-w-[85%] md:max-w-[80%] space-y-3">
                      <SkeletonBlock
                        width="50%"
                        height="5rem"
                        className="bg-[var(--color-bg-secondary)]"
                      />
                    </div>
                  </div>
                  {/* User message skeleton - right aligned */}
                  <div className="flex flex-col items-end mb-6">
                    <div className="max-w-[85%] md:max-w-[80%]">
                      <SkeletonBlock
                        width="70%"
                        height="2.5rem"
                        className="bg-[var(--color-accent-primary)] opacity-60"
                      />
                    </div>
                  </div>
                  {/* Assistant message skeleton - left aligned */}
                  <div className="flex flex-col items-start mb-6">
                    <div className="max-w-[85%] md:max-w-[80%] space-y-3">
                      <SkeletonBlock
                        width="75%"
                        height="4rem"
                        className="bg-[var(--color-bg-secondary)]"
                      />
                    </div>
                  </div>
                </div>
              ) : messages.length === 0 ? (
                <div className="h-full px-4 py-6">
                  <WelcomeScreen setInput={setInput} onSubmit={handleSubmit} />
                </div>
              ) : (
                <div className="mx-auto w-full max-w-3xl px-4 py-6">
                  {messages.map((message, index) => {
                    const isLast = index === messages.length - 1;
                    const liveEvents = getEventsForMessage(message.id, isLast);
                    const persistedToolEvents =
                      persistedToolEventsByMessageId.get(message.id) || [];
                    const msgEvents =
                      liveEvents.length > 0 ? liveEvents : persistedToolEvents;
                    const documentDownloadForMessage =
                      getDocumentDownloadFromEvents(msgEvents);
                    const liveThoughtContent = getThinkingContent(liveEvents);
                    const messageContent = getMessageContent(message);
                    const formattedMessageContent =
                      formatMessageContent(messageContent);
                    const showTts =
                      message.role === 'assistant' &&
                      formattedMessageContent.trim().length > 0 &&
                      !hasCouncilEvents(msgEvents);

                    const councilEventsInMessage = hasCouncilEvents(msgEvents);
                    const councilInterviewEvent =
                      getCouncilInterviewEvent(msgEvents);
                    const councilProgressEvent =
                      getLatestCouncilProgressEvent(msgEvents);
                    const councilOutputEvents =
                      getCouncilOutputEvents(msgEvents);
                    const councilProgressVisible =
                      shouldShowCouncilProgress(msgEvents);

                    const persistedMessage = persistedMessagesById.get(
                      message.id,
                    ) as ReasoningMessage | undefined;
                    const reasoningMessage =
                      persistedMessage ?? (message as ReasoningMessage);
                    const persistedReasoning = reasoningMessage.reasoning_text;
                    const rawDuration =
                      typeof reasoningMessage.reasoning_duration_secs ===
                      'number'
                        ? reasoningMessage.reasoning_duration_secs
                        : undefined;
                    const persistedDuration =
                      rawDuration !== undefined
                        ? Math.max(1, rawDuration)
                        : undefined;
                    const fallbackDuration =
                      persistedReasoning && persistedDuration === undefined
                        ? 1
                        : persistedDuration;
                    const persistedModel = reasoningMessage.reasoning_model;
                    const routingEvent = msgEvents.find(isRoutingEvent);
                    const routingModel = routingEvent?.model;
                    const fallbackThought =
                      thoughtFallbackByMessageId[message.id];
                    const thoughtContent =
                      liveThoughtContent ||
                      persistedReasoning ||
                      fallbackThought ||
                      '';
                    const thoughtEvent: ChatEvent | undefined = thoughtContent
                      ? { type: 'thinking', content: thoughtContent }
                      : undefined;
                    const modelName = getModelName(
                      persistedModel || routingModel,
                    );
                    const isActivePreviewDocument = Boolean(
                      documentDownloadForMessage &&
                      documentDownload &&
                      documentDownloadForMessage.fileUrl ===
                        documentDownload.fileUrl,
                    );
                    const isPreviewVisibleForMessage =
                      isActivePreviewDocument && showPreviewPanel;

                    return (
                      <div
                        key={message.id}
                        className={`mb-8 ${message.role === 'user' ? 'flex justify-end' : 'space-y-3'}`}
                      >
                        {message.role === 'assistant' &&
                          !councilEventsInMessage && (
                            <div className="w-full space-y-2">
                              <ThinkingIndicator
                                event={thoughtEvent}
                                isThinking={isLast && isLoading}
                                isFinished={!isLast || !isLoading}
                                duration={
                                  isLast && isLoading
                                    ? undefined
                                    : getDurationForMessage(message.id) > 0
                                      ? getDurationForMessage(message.id)
                                      : fallbackDuration
                                }
                                modelName={modelName}
                                onDurationChange={(d) =>
                                  (thinkingDurationRef.current = d)
                                }
                              />
                              <ToolCallLog events={msgEvents} />
                            </div>
                          )}

                        {councilEventsInMessage && (
                          <div className="w-full space-y-4">
                            {councilInterviewEvent && (
                              <CouncilInterviewCard
                                event={councilInterviewEvent}
                                onSendConfig={(config) => {
                                  append(
                                    {
                                      role: 'user',
                                      content: `/council config: preset=${config.preset}, rounds=${config.rounds}, audit=${config.audit}`,
                                    },
                                    {
                                      body: {
                                        id:
                                          currentId ||
                                          latestConversationIdRef.current ||
                                          null,
                                        model: activeModel,
                                      },
                                    },
                                  );
                                }}
                              />
                            )}

                            {councilProgressEvent && councilProgressVisible && (
                              <CouncilProgress
                                event={
                                  councilProgressEvent as {
                                    type: 'council_progress';
                                    stage: string;
                                    current_round: number;
                                    total_rounds: number;
                                    models_complete: number;
                                    models_total: number;
                                  }
                                }
                              />
                            )}

                            {councilOutputEvents.length > 0 && (
                              <CouncilOutputViewer
                                events={councilOutputEvents}
                              />
                            )}

                            {stoppedMessageIds.has(message.id) && (
                              <div
                                role="status"
                                className="text-xs text-[var(--color-text-muted)]"
                              >
                                (stopped)
                              </div>
                            )}
                          </div>
                        )}

                        {message.role === 'assistant' &&
                        !councilEventsInMessage ? (
                          <div className="w-full space-y-2">
                            <div className="w-full">
                              <MarkdownMessage content={messageContent} />
                            </div>
                            {stoppedMessageIds.has(message.id) && (
                              <div
                                role="status"
                                className="text-xs text-[var(--color-text-muted)]"
                              >
                                (stopped)
                              </div>
                            )}
                            {documentDownloadForMessage && (
                              <div className="mt-4 w-full">
                                <FileDownloadCard
                                  filename={documentDownloadForMessage.filename}
                                  fileUrl={documentDownloadForMessage.fileUrl}
                                  fileSize={documentDownloadForMessage.fileSize}
                                  fileType={documentDownloadForMessage.fileType}
                                  trailingAction={
                                    isActivePreviewDocument ? (
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setOpenedPreviewFileUrl((previous) =>
                                            previous ===
                                            documentDownloadForMessage.fileUrl
                                              ? null
                                              : documentDownloadForMessage.fileUrl,
                                          );
                                        }}
                                        className="inline-flex items-center justify-center w-10 h-10 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-primary)] border border-[var(--color-border-primary)] hover:border-[var(--color-border-secondary)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] rounded-lg transition-all focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)]"
                                        title={
                                          isPreviewVisibleForMessage
                                            ? 'Hide preview pane'
                                            : 'Show preview pane'
                                        }
                                        aria-label={
                                          isPreviewVisibleForMessage
                                            ? 'Hide preview pane'
                                            : 'Show preview pane'
                                        }
                                        aria-expanded={
                                          isPreviewVisibleForMessage
                                        }
                                      >
                                        {isPreviewVisibleForMessage ? (
                                          <EyeOff className="w-5 h-5" />
                                        ) : (
                                          <Eye className="w-5 h-5" />
                                        )}
                                      </button>
                                    ) : undefined
                                  }
                                />
                              </div>
                            )}
                            {showTts && (
                              <div className="flex justify-start">
                                {isLast && isLoading ? (
                                  <StreamingTtsMessage
                                    messageId={message.id}
                                    text={formattedMessageContent}
                                    isStreaming={isLast && isLoading}
                                    enabled={Boolean(ttsSettings?.enabled)}
                                    autoStart={Boolean(
                                      ttsSettings?.enabled &&
                                      ttsSettings?.autoPlay,
                                    )}
                                    voice={ttsSettings?.voice}
                                    model={ttsSettings?.model}
                                    speed={ttsSettings?.speed}
                                  />
                                ) : (
                                  <TextToSpeechButton
                                    text={formattedMessageContent}
                                  />
                                )}
                              </div>
                            )}
                          </div>
                        ) : message.role === 'user' ? (
                          <div className="max-w-[85%] md:max-w-[75%] rounded-2xl border border-[var(--color-accent-active)]/50 bg-[var(--color-accent-primary)] px-4 py-3 text-white shadow-sm">
                            <div className="whitespace-pre-wrap leading-relaxed font-medium">
                              {formattedMessageContent}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </main>

            <footer className="bg-[var(--color-bg-secondary)] border-t border-[var(--color-border-primary)] p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
              <form
                onSubmit={handleSubmit}
                className="mx-auto w-full max-w-3xl"
              >
                <ChatInputBar
                  selectedModel={activeModel}
                  onSelectModel={(modelId) => {
                    setActiveModel(modelId);
                    if (currentId) {
                      setConversationModel(currentId, modelId);
                    }
                  }}
                  isRecording={isRecording}
                  isConnecting={isConnecting}
                  startRecording={start}
                  stopRecording={stop}
                  micDisabled={inputIsBusy || !currentId || !isOnline}
                  micError={sttError}
                  input={input}
                  onInputChange={handleInputChange}
                  onSubmit={handleSubmit}
                  isLoading={inputIsBusy}
                  onStop={handleStopGeneration}
                  attachments={attachmentItems}
                  onAttachFiles={handleAttachFiles}
                  onRemoveAttachment={handleRemoveAttachment}
                  isLocal={false}
                  onToggleLocal={() => {}}
                />
              </form>
            </footer>
          </div>
        </Panel>

        {/* Resize Handle - only shown when preview is visible */}
        {showPreviewPanel && (
          <Separator className="hidden md:flex w-1 bg-[var(--color-border-primary)] hover:bg-[var(--color-accent-primary)] transition-colors cursor-col-resize items-center justify-center">
            <div className="w-0.5 h-8 bg-[var(--color-border-secondary)] rounded-full" />
          </Separator>
        )}

        {/* Right Panel - File Preview */}
        {showPreviewPanel && (
          <Panel
            defaultSize={40}
            minSize={30}
            className="hidden md:flex flex-col bg-[var(--color-bg-secondary)] border-l border-[var(--color-border-primary)]"
            style={{ minWidth: 420 }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border-primary)]">
              <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
                Document Preview
              </h2>
              <button
                type="button"
                onClick={() => {
                  setOpenedPreviewFileUrl(null);
                }}
                className="inline-flex items-center justify-center rounded-md p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)] transition-colors"
                aria-label="Close preview pane"
                title="Close preview pane"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {documentDownload && (
                <FilePreview
                  fileUrl={documentDownload!.fileUrl}
                  filename={documentDownload!.filename}
                  format={documentDownload!.fileType || ''}
                  fileSize={documentDownload!.fileSize}
                />
              )}
            </div>
          </Panel>
        )}
      </Group>

      <AgentStatusList agents={agents} />
    </div>
  );
}

function ChatContentWrapper() {
  return (
    <ErrorProvider>
      <ErrorBoundary>
        <ChatContent />
      </ErrorBoundary>
    </ErrorProvider>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={<ChatSkeleton />}>
      <ConversationHistoryProvider>
        <AudioPlaybackProvider>
          <ChatContentWrapper />
        </AudioPlaybackProvider>
      </ConversationHistoryProvider>
    </Suspense>
  );
}
