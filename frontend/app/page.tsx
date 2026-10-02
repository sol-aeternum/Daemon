'use client';

import { WelcomeScreen } from '../components/WelcomeScreen';
import { ChatHeaderActions } from '../components/ChatHeaderActions';
import { ChatActivityStatus } from '../components/ChatActivityStatus';
import { ChatInputBar } from '../components/ChatInputBar';
import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { useState, useRef, useEffect, Suspense, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Group, Panel, Separator } from 'react-resizable-panels';
import {
  ConversationDetails,
  useWideDetails,
  type DetailsTab,
} from '../components/ConversationDetails';
import {
  getConversationOutputs,
  type ConversationOutput,
  type DetailTurn,
} from '../lib/conversationDetails';
import { CopyResponseButton } from '../components/CopyResponseButton';
import { useChatDraft } from '../hooks/useChatDraft';
import { useAuthGeneration } from '../hooks/useAuthGeneration';
import { openChatDraft, resetChatDraft } from '../lib/chatDrafts';
import { useStt } from '../hooks/useStt';
import { ErrorProvider, useError } from '../components/ErrorProvider';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { ConnectionStatus } from '../components/ConnectionStatus';
import {
  ConversationList,
  type SidebarSection,
} from '../components/ConversationList';
import { ToolCallLog } from '../components/ToolCallBlock';
import {
  COLLAPSE_MIN_CHARS,
  CollapsibleMessage,
} from '../components/CollapsibleMessage';
import { useChatScroll } from '../hooks/useChatScroll';
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
import { useLocalStorage } from '../hooks/useLocalStorage';
import { refreshIfNeeded, getAuthHeader, getAuthGeneration } from '../lib/auth';
import { ThinkingIndicator } from '../components/ThinkingIndicator';
import { RoutingNotice } from '../components/RoutingNotice';
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
import { Eye, PanelRight } from 'lucide-react';
import { CouncilInterviewCard } from '../components/council/CouncilInterviewCard';
import { CouncilProgress } from '../components/council/CouncilProgress';
import { CouncilOutputViewer } from '../components/council/CouncilOutputViewer';
import {
  type DaemonMessage,
  getDaemonDataEvents,
  getDaemonMessageText,
} from '../lib/chatMessages';
import { buildMessageCitationSources } from '../lib/messageSources';

type ReasoningMessage = DaemonMessage & {
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

const getPersistedToolEvents = (message: DaemonMessage): ChatEvent[] => {
  const messageWithTools = message as DaemonMessage & {
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
import { SttSettings, DEFAULT_STT_SETTINGS } from '../lib/constants';

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

function ChatContent() {
  const { currentId: draftConversationId } = useConversationHistoryContext();
  const draft = useChatDraft(draftConversationId ?? null);
  const { input, setInput, pendingAttachments, setPendingAttachments } = draft;
  const draftRef = useRef(draft);
  const chatMountedRef = useRef(false);
  useEffect(() => {
    chatMountedRef.current = true;
    return () => {
      chatMountedRef.current = false;
    };
  }, []);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  const authGeneration = useAuthGeneration();
  const handleInputChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(event.target.value);
  };

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
  const [details, setDetails] = useState<{
    conversationId: string | null;
    generation: number;
    tab: DetailsTab;
    fileUrl?: string;
  } | null>(null);
  const detailsOpener = useRef<HTMLElement | null>(null);
  const wideDetails = useWideDetails();
  const { value: hideToolCalls, setValue: setHideToolCalls } = useLocalStorage(
    'daemon:hideToolCalls',
    false,
  );
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
      setDetails(null);
    });
  }, [currentId]);

  // Ref to track current duration for onFinish access
  const thinkingDurationRef = useRef<number>(0);

  // Ref to track current events for onFinish access
  const eventsRef = useRef<ChatEvent[]>([]);

  const lastArchivedEventKeysRef = useRef<Set<string>>(new Set());
  const currentRequestIdRef = useRef<string | null>(null);
  const latestConversationIdRef = useRef<string | null>(currentId);
  const renderedConversationIdRef = useRef<string | null>(currentId);
  const [messageScope, setMessageScope] = useState(currentId);
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

  const chatTransport = useMemo(
    () =>
      new DefaultChatTransport<DaemonMessage>({
        api: '/api/chat',
        fetch: async (requestInput, init) => {
          const generation = getAuthGeneration();
          await refreshIfNeeded();
          if (getAuthGeneration() !== generation || init?.signal?.aborted) {
            throw new Error(
              'Authentication changed before sending. Please review your draft.',
            );
          }
          const body: Record<string, unknown> =
            typeof init?.body === 'string' ? JSON.parse(init.body) : {};
          body.model = activeModel;
          body.id = currentId || null;

          const headers = new Headers(init?.headers);
          const authHeader = getAuthHeader();
          if (authHeader) {
            headers.set('Authorization', authHeader);
          }

          return fetch(requestInput, {
            ...init,
            headers,
            body: JSON.stringify(body),
          });
        },
      }),
    [activeModel, currentId],
  );

  // Keep the SDK chat instance stable while the backend promotes a new chat
  // from a null ID to its persisted conversation ID mid-stream. Conversation
  // switches are synchronized explicitly below once their history is loaded.
  const {
    messages,
    setMessages,
    status,
    error,
    regenerate,
    sendMessage,
    stop: stopChat,
  } = useChat<DaemonMessage>({
    transport: chatTransport,
    messages:
      currentConversation?.id === currentId ? currentConversation.messages : [],
    onFinish: ({ message }) => {
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

  const isLoading = status === 'submitted' || status === 'streaming';
  const data = useMemo(() => getDaemonDataEvents(messages), [messages]);
  const reload = () => {
    void regenerate({
      body: {
        id: currentId || latestConversationIdRef.current || null,
        model: activeModel,
      },
    });
  };

  useEffect(() => {
    if (isLoading) {
      return;
    }

    if (!currentId) {
      if (renderedConversationIdRef.current !== null) {
        renderedConversationIdRef.current = null;
        setMessages([]);
        queueMicrotask(() => setMessageScope(null));
      }
      return;
    }

    if (currentConversation?.id !== currentId) {
      return;
    }

    const conversationChanged = renderedConversationIdRef.current !== currentId;
    if (
      conversationChanged ||
      (messages.length === 0 && currentConversation.messages.length > 0)
    ) {
      renderedConversationIdRef.current = currentId;
      setMessages(currentConversation.messages);
      queueMicrotask(() => setMessageScope(currentId));
    }
  }, [
    currentConversation?.id,
    currentConversation?.messages,
    currentId,
    isLoading,
    messages.length,
    setMessages,
  ]);

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

  const submitChat = async (command?: string) => {
    if (isLoading && messages.length > 0) return;

    const generation = getAuthGeneration();
    const binding = draft.setInput;
    const receipt = draft.captureSubmission(input, pendingAttachments);
    const trimmedInput = (command ?? input).trim();
    const attachments =
      !command && pendingAttachments.length > 0
        ? await serializeAttachments(pendingAttachments)
        : [];
    if (
      !chatMountedRef.current ||
      generation !== getAuthGeneration() ||
      draftRef.current.setInput !== binding
    )
      return;
    const content =
      trimmedInput ||
      (attachments.length > 0
        ? `Attached ${attachments.length} file${attachments.length === 1 ? '' : 's'}.`
        : '');

    if (!content) return;

    try {
      await sendMessage(
        { text: content },
        {
          body: {
            id: currentId || latestConversationIdRef.current || null,
            model: activeModel,
            attachments,
          },
        },
      );

      // The current scope validates account/conversation/epoch and only clears
      // fields still equal to the submitted snapshot, including after ID promotion.
      if (!command) draftRef.current.clearSubmission(receipt);
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
    assignConversationId,
    clearAssignedConversationId,
  } = useStopGeneration({
    messages,
    stop: stopChat,
    archiveEvents: archiveCurrentEvents,
    conversationId: currentId ?? null,
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
      if (eventsRef.current.length > 0) {
        lastArchivedEventKeysRef.current = new Set(
          eventsRef.current.map(eventKey),
        );
      }
    }
    prevLoadingRef.current = isLoading;
  }, [isLoading]);

  const {
    scrollContainerRef,
    messagesEndRef,
    isScrolledUp,
    onScroll,
    jumpToLatest,
  } = useChatScroll({
    conversationId: currentId,
    messages,
    isLoading,
    authGeneration,
  });

  const handleSelectConversation = async (id: string) => {
    // Preserve per-message stopped markers across conversation switches —
    // clearing here would silently drop `(stopped)` indicators for the
    // current conversation when the user navigates away and back, since
    // the set is keyed by message ID and IDs are stable across renders.
    await switchConversation(id);
  };

  const handleNewChat = async () => {
    clearAssignedConversationId();
    resetChatDraft(openChatDraft(null, getAuthGeneration()));
    const generation = getAuthGeneration();
    const created = await createConversation();
    if (generation !== getAuthGeneration()) return;
    if (created) resetChatDraft(openChatDraft(created, generation));
    setArchivedEvents({});
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
    renderedConversationIdRef.current = conversationId;
    if (!currentId) {
      // A fast Stop can be recorded before the URL has the backend-assigned
      // conversation ID. Promote those provisional markers immediately so
      // the router transition does not make the marker disappear.
      assignConversationId(conversationId);
    }
    const hasCouncilEvent = flattenedData.some(isCouncilDataEvent);
    const hasCouncilDoneEvent = flattenedData.some(isCouncilDoneDataEvent);
    const shouldSyncConversationState = !hasCouncilEvent || hasCouncilDoneEvent;

    if (!currentId && !urlUpdatedRef.current) {
      if (!draftRef.current.transferToConversation(conversationId)) {
        // Never overwrite another conversation's draft. The unassigned draft
        // remains available at Home, rather than being silently discarded.
        showError(
          'This conversation already has a draft. Your other unfinished draft is preserved on Home.',
        );
      }
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
  }, [
    assignConversationId,
    flattenedData,
    currentId,
    refreshConversations,
    router,
    showError,
  ]);

  const agents = useAgentStatus(events);
  // Data parts accumulate across turns; the header only describes the live reply.
  const latestMessage = messages.at(-1);
  const currentActivityEvents =
    latestMessage?.role === 'assistant'
      ? getDaemonDataEvents([latestMessage])
      : [];

  const currentMessagesMatch = messageScope === currentId;
  const detailTurns: DetailTurn[] = (
    currentMessagesMatch ? messages : []
  ).flatMap((message, index) => {
    if (message.role !== 'assistant') return [];
    const live = getEventsForMessage(message.id, index === messages.length - 1);
    return [
      {
        id: message.id,
        events: live.length
          ? live
          : persistedToolEventsByMessageId.get(message.id) || [],
        running: isLoading && index === messages.length - 1,
        stopped: stoppedMessageIds.has(message.id),
      },
    ];
  });
  const activeDetails =
    details?.conversationId === (currentId ?? null) &&
    details?.generation === authGeneration
      ? details
      : null;
  const selectedOutput = detailTurns
    .flatMap((turn) => getConversationOutputs(turn.events))
    .find((output) => output.fileUrl === activeDetails?.fileUrl);
  const openDetails = (tab: DetailsTab, output?: ConversationOutput) => {
    detailsOpener.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setDetails({
      conversationId: currentId ?? null,
      generation: authGeneration,
      tab,
      fileUrl: output?.fileUrl,
    });
  };
  const closeDetails = () => {
    setDetails(null);
    requestAnimationFrame(() => {
      if (detailsOpener.current?.isConnected)
        detailsOpener.current.focus({ preventScroll: true });
    });
  };
  const detailsView = activeDetails && (
    <ConversationDetails
      conversationId={currentId}
      turns={detailTurns}
      tab={activeDetails.tab}
      selected={selectedOutput}
      onTab={(tab) =>
        setDetails({
          conversationId: currentId ?? null,
          generation: authGeneration,
          tab,
        })
      }
      onSelect={(output) =>
        setDetails({
          conversationId: currentId ?? null,
          generation: authGeneration,
          tab: 'Outputs',
          fileUrl: output?.fileUrl,
        })
      }
      onClose={closeDetails}
      modal={!wideDetails}
    />
  );
  const detailsButton = (
    <button
      type="button"
      aria-label="Open conversation details"
      aria-expanded={Boolean(activeDetails)}
      onClick={() => openDetails('Sources')}
      className="min-h-touch min-w-touch inline-flex items-center justify-center gap-2 rounded-lg px-2 text-sm hover:bg-[var(--color-bg-hover)]"
    >
      <PanelRight size={18} />
      <span className="hidden lg:inline">Details</span>
    </button>
  );

  const toolLogToggle = (
    <button
      type="button"
      aria-label="Hide tool calls"
      aria-pressed={hideToolCalls}
      onClick={() => setHideToolCalls((previous) => !previous)}
      className="min-h-touch px-2 text-xs font-medium rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] aria-pressed:bg-[var(--color-accent-subtle)] aria-pressed:text-[var(--color-accent-primary)]"
    >
      <span className="hidden lg:inline">Hide tool calls</span>
      <span className="lg:hidden">Tools</span>
    </button>
  );

  return (
    <div className="flex h-dvh bg-[var(--color-bg-primary)] overflow-hidden">
      {!isOnline && <OfflineIndicator />}
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-[var(--color-bg-overlay)] z-40 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div
        className={`
        fixed inset-y-0 left-0 z-50 w-sidebar bg-[var(--color-bg-secondary)] transform transition-transform duration-300
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
          defaultSize={activeDetails && wideDetails ? 65 : 100}
          minSize={40}
          className="flex min-h-0 flex-col"
        >
          <div className="flex-1 flex min-h-0 flex-col w-full min-w-0 relative">
            {isRecording && (
              <div className="bg-[var(--color-status-error)] text-[var(--color-text-on-status)] px-4 py-2 text-center text-sm font-medium animate-pulse">
                Recording... Tap mic to stop
              </div>
            )}
            <MobileHeader
              title={currentConversation?.title || 'New conversation'}
              onOpenSidebar={() => setIsSidebarOpen(true)}
            >
              <div className="flex items-center gap-2">
                {detailsButton}
                {toolLogToggle}
                <ConnectionStatus
                  status={connectionStatus}
                  onReconnect={reload}
                />
              </div>
            </MobileHeader>

            <header className="hidden md:flex bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)] px-4 py-3 items-center justify-between">
              <h1 className="min-w-0 truncate text-lg font-semibold">
                {currentConversation?.title || 'New conversation'}
              </h1>
              <div className="flex min-w-0 items-center gap-3">
                <ChatActivityStatus
                  events={currentActivityEvents}
                  isLoading={isLoading}
                />
                {toolLogToggle}
                {detailsButton}
                <ConnectionStatus
                  status={connectionStatus}
                  onReconnect={reload}
                />
                <ChatHeaderActions conversationId={currentId} />
              </div>
            </header>

            <main
              ref={scrollContainerRef}
              onScroll={onScroll}
              tabIndex={-1}
              aria-label="Conversation messages"
              className="flex-1 min-h-0 overflow-y-auto"
            >
              {messages.length === 0 && isLoading ? (
                <div className="mx-auto w-full max-w-3xl flex flex-col space-y-4 px-4 py-6 animate-fade-in">
                  {/* Assistant message skeleton - left aligned */}
                  <div className="flex flex-col items-start mb-6">
                    <div className="max-w-message-mobile md:max-w-assistant-message space-y-3">
                      <SkeletonBlock
                        width="60%"
                        height="4rem"
                        className="bg-[var(--color-bg-secondary)]"
                      />
                    </div>
                  </div>
                  {/* User message skeleton - right aligned */}
                  <div className="flex flex-col items-end mb-6">
                    <div className="max-w-message-mobile md:max-w-assistant-message">
                      <SkeletonBlock
                        width="80%"
                        height="3rem"
                        className="bg-[var(--color-accent-primary)] opacity-60"
                      />
                    </div>
                  </div>
                  {/* Assistant message skeleton - left aligned */}
                  <div className="flex flex-col items-start mb-6">
                    <div className="max-w-message-mobile md:max-w-assistant-message space-y-3">
                      <SkeletonBlock
                        width="50%"
                        height="5rem"
                        className="bg-[var(--color-bg-secondary)]"
                      />
                    </div>
                  </div>
                  {/* User message skeleton - right aligned */}
                  <div className="flex flex-col items-end mb-6">
                    <div className="max-w-message-mobile md:max-w-assistant-message">
                      <SkeletonBlock
                        width="70%"
                        height="2.5rem"
                        className="bg-[var(--color-accent-primary)] opacity-60"
                      />
                    </div>
                  </div>
                  {/* Assistant message skeleton - left aligned */}
                  <div className="flex flex-col items-start mb-6">
                    <div className="max-w-message-mobile md:max-w-assistant-message space-y-3">
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
                  <WelcomeScreen
                    input={input}
                    setInput={setInput}
                    onDeliberate={() => void submitChat('/council')}
                  />
                </div>
              ) : (
                <div className="mx-auto w-full max-w-3xl px-4 py-6">
                  {messages.map((message, index) => {
                    if (message.role === 'system') return null;
                    const isLast = index === messages.length - 1;
                    const liveEvents = getEventsForMessage(message.id, isLast);
                    const persistedToolEvents =
                      persistedToolEventsByMessageId.get(message.id) || [];
                    const msgEvents =
                      liveEvents.length > 0 ? liveEvents : persistedToolEvents;
                    const documentsForMessage = currentMessagesMatch
                      ? getConversationOutputs(msgEvents)
                      : [];
                    const liveThoughtContent = getThinkingContent(liveEvents);
                    const messageContent = getDaemonMessageText(message);
                    const formattedMessageContent =
                      formatMessageContent(messageContent);

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
                    const citationSources =
                      message.role === 'assistant'
                        ? buildMessageCitationSources(msgEvents)
                        : [];

                    return (
                      <CollapsibleMessage
                        key={message.id}
                        messageId={message.id}
                        title={`${message.role === 'user' ? 'You' : 'Daemon'} · message ${index + 1}`}
                        preview={formattedMessageContent}
                        collapsible={
                          index < messages.length - 5 &&
                          formattedMessageContent.length > COLLAPSE_MIN_CHARS
                        }
                        childrenClassName={
                          message.role === 'user'
                            ? 'flex justify-end'
                            : 'space-y-3'
                        }
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
                              {!hideToolCalls && (
                                <ToolCallLog events={msgEvents} />
                              )}
                              <RoutingNotice
                                fallback={routingEvent?.fallback}
                              />
                            </div>
                          )}

                        {councilEventsInMessage && (
                          <div className="w-full space-y-4">
                            {councilInterviewEvent && (
                              <CouncilInterviewCard
                                event={councilInterviewEvent}
                                onSendConfig={(config) => {
                                  void sendMessage(
                                    {
                                      text: `/council config: preset=${config.preset}, rounds=${config.rounds}, audit=${config.audit}`,
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
                              <MarkdownMessage
                                content={messageContent}
                                sources={citationSources}
                              />
                              {messageContent.trim() && (
                                <CopyResponseButton content={messageContent} />
                              )}
                            </div>
                            {stoppedMessageIds.has(message.id) && (
                              <div
                                role="status"
                                className="text-xs text-[var(--color-text-muted)]"
                              >
                                (stopped)
                              </div>
                            )}
                            {documentsForMessage.map(
                              (documentDownloadForMessage) => (
                                <div
                                  key={documentDownloadForMessage.fileUrl}
                                  className="mt-4 w-full"
                                >
                                  <FileDownloadCard
                                    filename={
                                      documentDownloadForMessage.filename
                                    }
                                    fileUrl={documentDownloadForMessage.fileUrl}
                                    fileSize={
                                      documentDownloadForMessage.fileSize
                                    }
                                    fileType={
                                      documentDownloadForMessage.fileType
                                    }
                                    trailingAction={
                                      <button
                                        type="button"
                                        onClick={() =>
                                          openDetails(
                                            'Outputs',
                                            documentDownloadForMessage,
                                          )
                                        }
                                        className="inline-flex min-h-touch items-center justify-center gap-2 px-3 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-primary)] border border-[var(--color-border-primary)] text-[var(--color-text-secondary)] rounded-lg"
                                        aria-label={`Preview ${documentDownloadForMessage.filename}`}
                                      >
                                        <Eye className="w-4 h-4" />
                                        Preview
                                      </button>
                                    }
                                  />
                                </div>
                              ),
                            )}
                          </div>
                        ) : message.role === 'user' ? (
                          <div className="max-w-message-mobile md:max-w-user-message rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3 text-[var(--color-text-primary)]">
                            <div className="whitespace-pre-wrap leading-relaxed font-medium">
                              {formattedMessageContent}
                            </div>
                          </div>
                        ) : null}
                      </CollapsibleMessage>
                    );
                  })}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </main>

            <footer className="relative shrink-0 bg-[var(--color-bg-primary)] pb-safe-panel">
              {isScrolledUp && isLoading && (
                <button
                  type="button"
                  onClick={() => {
                    jumpToLatest();
                    scrollContainerRef.current?.focus({ preventScroll: true });
                  }}
                  className="absolute bottom-full mb-4 right-4 min-h-touch px-4 rounded-full shadow-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] text-sm font-medium text-[var(--color-text-primary)]"
                >
                  Jump to latest
                </button>
              )}
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

        {activeDetails && wideDetails && (
          <Separator className="flex w-1 bg-[var(--color-border-primary)] hover:bg-[var(--color-accent-primary)] cursor-col-resize items-center justify-center">
            <div className="w-0.5 h-8 bg-[var(--color-border-secondary)] rounded-full" />
          </Separator>
        )}

        {activeDetails && wideDetails && (
          <Panel
            defaultSize={35}
            minSize={25}
            className="flex flex-col bg-[var(--color-bg-primary)] border-l border-[var(--color-border-primary)]"
            style={{ minWidth: 320 }}
          >
            {detailsView}
          </Panel>
        )}
      </Group>
      {activeDetails && !wideDetails && detailsView}

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
