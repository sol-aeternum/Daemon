'use client';

import { WelcomeScreen } from '../components/WelcomeScreen';
import { SuggestionSourceContext } from '../components/SuggestionSourceContext';
import { chatTransportFetch } from '../lib/chatTransportFetch';
import {
  attachmentsForRetry,
  reconcileSubmission,
  shouldDetachStream,
  type LastTurn,
} from '../lib/durableRecovery';
import {
  finishedSubmissionKeys,
  promotePendingSubmission,
  pendingTasksIn,
  recordSubmissionTask,
  settlePendingSubmission,
  unresolvedSubmissions,
} from '../lib/pendingSubmission';
import {
  acceptSubmission,
  heldResendStatus,
  heldSubmission,
  heldSubmissionComplete,
  heldSubmissionsReady,
  latestHeldSubmission,
  promoteHeldSubmission,
  releaseSubmission,
  restoreHeldSubmission,
} from '../lib/chatDrafts';
import type { HomeSuggestion } from '../lib/homeSuggestions';
import { ChatHeaderActions } from '../components/ChatHeaderActions';
import { ChatActivityStatus } from '../components/ChatActivityStatus';
import { ChatInputBar } from '../components/ChatInputBar';
import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import {
  useState,
  useRef,
  useEffect,
  Suspense,
  useMemo,
  useCallback,
  useLayoutEffect,
} from 'react';
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
import { TextToSpeechButton } from '../components/TextToSpeechButton';
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
import { TtsPlaybackBar } from '../components/TtsPlaybackBar';
import { useEventArchive } from '../hooks/useEventArchive';
import { useStopGeneration } from '../hooks/useStopGeneration';
import { useActiveTaskFollower } from '../hooks/useActiveTaskFollower';
import { useDurableStop } from '../hooks/useDurableStop';
import { useStopShortcut } from '../hooks/useStopShortcut';
import type { StopOutcome } from '../hooks/useStopGeneration';
import { formatMessageContent } from '../lib/format';
import { useAgentStatus } from '../hooks/useAgentStatus';
import { AgentStatusList } from '../components/AgentStatusList';
import { OfflineIndicator } from '../components/OfflineIndicator';
import { RetryButton } from '../components/RetryButton';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { getAuthGeneration, subscribeAuthGeneration } from '../lib/auth';
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
  getDaemonTaskId,
  getDaemonTaskStatus,
  getRequestRejection,
  TERMINAL_TASK_STATUSES,
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
    refreshCurrentConversation,
    cancelTask,
    taskForKey,
    taskStatus,
    switchConversation,
    setConversationModel,
    searchQuery,
    setSearchQuery,
    conversationSearch,
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
  const suggestionSubmissionRef = useRef<{
    generation: number;
    messageId: string;
    pending: boolean;
  } | null>(null);
  const chatRequestGenerationRef = useRef<number | null>(null);
  // Idempotency key of the submission currently (or most recently) in flight.
  const activeSubmissionKeyRef = useRef<string | null>(null);
  // Files of the latest submitted turn and the conversation they were sent
  // to, re-sent only when that same conversation's turn is regenerated.
  const lastTurnRef = useRef<LastTurn>(null);
  // While a Stop is being confirmed its conversation stays busy, so a new
  // submission cannot race a task that is still stopping. Other
  // conversations are unaffected. ``null``: an unnamed new chat;
  // ``undefined``: no Stop pending.
  const [stoppingIn, setStoppingIn] = useState<string | null | undefined>(
    undefined,
  );
  const stopSeqRef = useRef(0);
  // The Stop still being confirmed, so a repeated press can wait for it.
  const pendingStopRef = useRef<{
    scope: string | null;
    outcome: Promise<StopOutcome>;
  } | null>(null);
  const stopInFlight =
    stoppingIn !== undefined && stoppingIn === (currentId ?? null);
  // The conversation the in-flight submission belongs to (``null``: an
  // unnamed new chat, promoted when named). Stop settles that submission's
  // key only when it stops a task in the same conversation.
  const activeSubmissionScopeRef = useRef<string | null>(null);
  // A new send starts with no key of its own until its transport assigns
  // one: an early Stop must never resolve an earlier submission's task.
  // A held submission that lost some of its files (too large to keep, or
  // expired) can't be resent under its key: that would be a different
  // request. With no task for its key, it is dropped, and the owner told.
  const dropIncompleteHeld = useCallback(
    (key: string): boolean => {
      if (!heldSubmission(key) || heldSubmissionComplete(key)) return false;
      settleSubmission(key);
      showError(
        'A message that may not have been sent could not be recovered with its files. Please attach them and send it again.',
      );
      return true;
    },
    [showError],
  );
  const beginSend = (key: string | null = null) => {
    // The key is known here for every keyed send; the transport confirms it.
    activeSubmissionKeyRef.current = key;
    activeSubmissionScopeRef.current =
      currentIdRef.current || latestConversationIdRef.current || null;
  };
  // Mirrors stopInFlight for submit handlers declared before it.
  const stopInFlightRef = useRef(false);
  // The open conversation id as of the latest render, for async callbacks.
  const currentIdRef = useRef(currentId);
  useLayoutEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);
  // Set once useChat has returned; used by callbacks created before it.
  const reconcileWithServerRef = useRef<
    ((key?: string | null) => Promise<void>) | null
  >(null);
  // A composer send is awaiting sendMessage: its draft is cleared only after
  // that, so restoring a refused submission must wait until then.
  const composerSendInFlightRef = useRef(false);
  const deferredRestoresRef = useRef<string[]>([]);
  // Keys this page resent (a held draft or a retry): a rejection of such a
  // key is ambiguous, since the key may already belong to an accepted task.
  const resentKeysRef = useRef(new Set<string>());
  // Refused submissions waiting for an empty composer to return to.
  const rejectedWaitingRef = useRef(new Set<string>());
  const returnRejectedRef = useRef<((key: string) => Promise<void>) | null>(
    null,
  );
  const [isSubmittingSuggestion, setIsSubmittingSuggestion] = useState(false);
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
        fetch: (requestInput, init) =>
          chatTransportFetch(requestInput, init, {
            model: activeModel,
            conversationId: currentId || null,
            onGeneration: (generation) => {
              chatRequestGenerationRef.current = generation;
            },
            onSubmissionKey: (key, conversationId) => {
              activeSubmissionKeyRef.current = key;
              // The conversation the request was queued in, not the route
              // now: the user may have navigated while the key was made.
              activeSubmissionScopeRef.current = conversationId;
            },
          }),
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
    onFinish: ({ message, isAbort, isDisconnect, isError }) => {
      // Durable bookkeeping first, even after another tab's token refresh
      // bumped the generation: keys belong to an account and are cleared on
      // a real account change, so settling or recording here is always safe.
      // The submission is settled once its outcome is known: a clean
      // finish, or a durable task that reported a terminal state (including
      // a failure, which arrives as a stream error). After an abort,
      // disconnect or other error the server may still hold an accepted
      // task, so the key is kept for a resend to reattach to it.
      const rejection = getRequestRejection(message);
      if (rejection && activeSubmissionKeyRef.current) {
        const key = activeSubmissionKeyRef.current;
        if (
          rejection.code === 'idempotency_conflict' ||
          resentKeysRef.current.has(key)
        ) {
          // The key may already belong to an accepted request (a reselected
          // file, or a resend refused by an outer limit before the replay
          // check): resolve it with the server rather than release it.
          void reconcileWithServerRef.current?.(key);
        } else if (composerSendInFlightRef.current) {
          // Refused before acceptance: nothing exists for this key. The text
          // goes back to the composer once the send has finished clearing it,
          // and a resend (perhaps with another model) is a new submission.
          deferredRestoresRef.current.push(key);
        } else {
          void returnRejectedRef.current?.(key);
        }
      }
      const taskStatusSeen = getDaemonTaskStatus(message);
      const outcomeKnown =
        !rejection &&
        ((!isAbort && !isDisconnect && !isError) ||
          (taskStatusSeen !== null &&
            TERMINAL_TASK_STATUSES.has(taskStatusSeen)));
      if (outcomeKnown && activeSubmissionKeyRef.current) {
        finishSubmission(activeSubmissionKeyRef.current);
      } else if (activeSubmissionKeyRef.current) {
        // Unresolved: remember its task, so the key is settled when any
        // later view of the conversation shows that task finished.
        const taskId = getDaemonTaskId(message);
        if (taskId) recordSubmission(activeSubmissionKeyRef.current, taskId);
      }
      if (
        chatRequestGenerationRef.current !== null &&
        chatRequestGenerationRef.current !== getAuthGeneration()
      )
        return;
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
      // The stream dropped, but server-owned work may still be running or
      // already finished: show the server's version of this turn. A turn the
      // server never accepted stays as typed, so it can be resent. This runs
      // even after another tab's token refresh (lookups are account-scoped).
      void reconcileWithServerRef.current?.();
      if (
        chatRequestGenerationRef.current !== null &&
        chatRequestGenerationRef.current !== getAuthGeneration()
      )
        return;
      showError(err.message || 'Chat error occurred');
      setConnectionStatus('disconnected');
    },
  });

  const isLoading = status === 'submitted' || status === 'streaming';
  // Leaving the conversation whose durable task this client is streaming
  // detaches from it (the task runs on server-side, and its own page follows
  // it); it never cancels it, and the newly opened conversation loads and
  // works independently. Request-bound streams are left as they were.
  useEffect(() => {
    if (
      shouldDetachStream(
        isLoading,
        messages[messages.length - 1],
        activeSubmissionScopeRef.current,
        currentId ?? null,
      )
    ) {
      stopChat();
    }
  }, [currentId, isLoading, messages, stopChat]);
  // As soon as the stream names its task, store it with the submission.
  const streamedTaskId = isLoading
    ? getDaemonTaskId(messages[messages.length - 1])
    : null;
  useEffect(() => {
    if (streamedTaskId && activeSubmissionKeyRef.current) {
      recordSubmission(activeSubmissionKeyRef.current, streamedTaskId);
    }
  }, [streamedTaskId]);
  // Pending keys are cleared by the auth transitions themselves (sign-in and
  // sign-out, in lib/auth.ts), also when this page is not mounted.
  // Follow server-owned work this client is not streaming itself (for example
  // after reopening the conversation on another device, or after a dropped
  // stream). Only a persisted placeholder qualifies, so a live stream's
  // messages are never replaced. While it runs, the input is busy: Stop is
  // offered and a conflicting submission is blocked.
  const lastMessage = messages[messages.length - 1];
  const followsPersistedTask =
    lastMessage?.role === 'assistant' && lastMessage.status === 'streaming';
  const followedConversation =
    followsPersistedTask && currentConversation?.id === currentId
      ? currentConversation
      : null;
  const serverTaskBusy =
    !isLoading && Boolean(followedConversation?.activeTask);
  // Until a conversation's server state has loaded, it may have a running
  // task: sending then would only meet conversation_busy.
  const conversationLoading =
    Boolean(currentId) && currentConversation?.id !== currentId;
  useActiveTaskFollower({
    conversation: followedConversation,
    isStreaming: isLoading,
    refresh: refreshCurrentConversation,
    onUpdate: (conversation) => setMessages(conversation.messages),
  });
  // Whenever a conversation's server state is seen (opened, refreshed or
  // followed), settle submissions whose task it shows has finished: retyping
  // such a request later is a new run, not a replay. Covers tasks that ended
  // while another conversation was open, and survives reloads.
  useEffect(() => {
    if (!currentConversation) return;
    for (const key of finishedSubmissionKeys(currentConversation, (status) =>
      TERMINAL_TASK_STATUSES.has(status),
    )) {
      finishSubmission(key);
    }
    // A task a newer one has superseded is no longer the conversation's
    // latest: ask the server about it directly (a few per view).
    const shown = new Set(
      [
        currentConversation.activeTask?.id,
        currentConversation.latestTask?.id,
      ].filter(Boolean),
    );
    for (const { key, taskId } of pendingTasksIn(currentConversation.id)
      .filter(({ taskId }) => !shown.has(taskId))
      .slice(0, 5)) {
      void taskStatus(taskId).then((status) => {
        if (status && TERMINAL_TASK_STATUSES.has(status)) {
          finishSubmission(key);
        }
      });
    }
  }, [currentConversation, taskStatus]);

  useEffect(() => {
    return subscribeAuthGeneration(() => {
      if (!suggestionSubmissionRef.current) return;
      suggestionSubmissionRef.current = null;
      stopChat();
      setMessages([]);
      setIsSubmittingSuggestion(false);
    });
  }, [stopChat, setMessages]);
  const data = useMemo(() => getDaemonDataEvents(messages), [messages]);
  const reload = () => {
    const conversationId = currentId || latestConversationIdRef.current || null;
    // Retrying an unresolved turn resends it under its own key (a replay if
    // the backend already accepted it); a settled turn regenerates anew.
    const held = latestHeldSubmission(conversationId);
    const retryAttachments = attachmentsForRetry(
      lastTurnRef.current,
      conversationId,
    );
    if (
      held &&
      (!heldSubmissionComplete(held.key) ||
        retryAttachments.length !== held.attachmentCount)
    ) {
      // Its files are gone (or this page no longer has them): retrying
      // under its key would send a different request. Resolve it instead;
      // if it was never accepted it goes back to the composer.
      showError(
        'This message cannot be retried with its files. Checking whether it was already sent…',
      );
      void reconcileWithServerRef.current?.(held.key);
      return;
    }
    if (held) resentKeysRef.current.add(held.key);
    beginSend(held?.key ?? null);
    void regenerate({
      body: {
        id: conversationId,
        model: activeModel,
        ...(held ? { idempotency_key: held.key } : {}),
        // Regenerating a turn re-sends its files; without them the backend
        // would see (and run) a different request. Another conversation's
        // files are never sent.
        attachments: retryAttachments,
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
    if (isSubmittingSuggestion || suggestionSubmissionRef.current?.pending)
      return;
    if (conversationLoading) {
      // Never drop a send silently: the conversation (and any task running
      // in it) is still being loaded, and failed loads are retried.
      showError('This conversation is still loading. Try again in a moment.');
      return;
    }
    if (
      (isLoading && messages.length > 0) ||
      serverTaskBusy ||
      stopInFlightRef.current ||
      conversationLoading
    )
      return;

    // A draft that is a held submission (``restoredKey``) resends only
    // exactly what was sent, with every file, under its key. Missing or
    // still-loading files must never go out under that key, nor rerun the
    // request under a new one: the key is resolved with the server first.
    const heldKey = command ? undefined : draft.restoredKey;
    const status = heldKey
      ? heldResendStatus(heldKey, input, pendingAttachments)
      : 'new';
    if (status === 'loading') {
      showError('Your attachments are still loading. Try again in a moment.');
      return;
    }
    if (status === 'incomplete' && heldKey) {
      showError(
        'Some files of this message are missing, so it cannot be sent again as it was. Checking whether it was already sent…',
      );
      void reconcileWithServerRef.current?.(heldKey);
      return;
    }
    const resend = status === 'resend' && !!heldKey;
    const key = resend && heldKey ? heldKey : crypto.randomUUID();
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
    // The held submission may have been resolved while its files were read
    // (the server had it, or refused it): this send is then not needed, and
    // must never go out again under a new key.
    if (resend && heldResendStatus(key, input, pendingAttachments) !== 'resend')
      return;
    const content =
      trimmedInput ||
      (attachments.length > 0
        ? `Attached ${attachments.length} file${attachments.length === 1 ? '' : 's'}.`
        : '');

    if (!content) return;

    suggestionSubmissionRef.current = null;
    const conversationScope =
      currentId || latestConversationIdRef.current || null;
    lastTurnRef.current = { conversationId: conversationScope, attachments };
    // The key belongs to the submitted draft: resending the held draft
    // unchanged (after a lost response or a reload) reuses its key, so the
    // backend replays what it already accepted. Anything else is new.
    // Only the draft that was put back from a held submission, unedited, is
    // its resend; the same text typed anew is a new request with a new key.
    if (resend) resentKeysRef.current.add(key);
    if (!command) draft.holdSubmission(key, input, pendingAttachments);

    composerSendInFlightRef.current = true;
    try {
      beginSend(key);
      await sendMessage(
        { text: content },
        {
          body: {
            id: conversationScope,
            model: activeModel,
            attachments,
            idempotency_key: key,
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
    } finally {
      composerSendInFlightRef.current = false;
      for (const refused of deferredRestoresRef.current.splice(0)) {
        void returnRejectedRef.current?.(refused);
      }
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

  const inputIsBusy =
    (isLoading && messages.length > 0) || serverTaskBusy || stopInFlight;
  // Durable tasks survive a disconnect, so Stop cancels the task explicitly;
  // closing the app only detaches. The outcome comes from the server and is
  // never assumed: a missing task id is not proof that nothing was accepted.
  const cancelActiveTask = useDurableStop({
    messages,
    activeTaskId: currentConversation?.activeTask?.id ?? null,
    isLoading,
    submissionKeyRef: activeSubmissionKeyRef,
    cancelTask,
    taskForKey,
    taskStatus,
  });
  // Show the server's copy of this submission's turn, identified by the
  // task its idempotency key created (never by matching text): the saved
  // result, a terminal notice, or the still-running task, which the
  // follower then tracks and which keeps the input busy until it ends.
  const reconcileWithServer = useCallback(
    (key: string | null = activeSubmissionKeyRef.current) =>
      reconcileSubmission(key, {
        currentId: () => currentIdRef.current,
        taskForKey,
        settle: finishSubmission,
        record: recordSubmission,
        // Never accepted: the held draft goes back in the composer, so
        // sending it again reuses its key (safe even if it lands late).
        notFound: (key) => {
          void heldSubmissionsReady().then(() => {
            if (dropIncompleteHeld(key)) return;
            if (restoreHeldSubmission(key)) {
              showError(
                'Your message may not have been sent. It is back in the composer; send it again to retry.',
              );
            }
          });
        },
        promote: (key, conversationId) => {
          promoteSubmission(key, conversationId);
          if (
            activeSubmissionKeyRef.current === key &&
            activeSubmissionScopeRef.current === null
          ) {
            activeSubmissionScopeRef.current = conversationId;
          }
        },
        // A new chat accepted before its conversation id reached this page:
        // the follower then shows the task's progress or result.
        open: switchConversation,
        showSaved: async (taskId) => {
          const fresh = await refreshCurrentConversation();
          if (
            fresh &&
            (fresh.activeTask?.id === taskId || fresh.latestTask?.id === taskId)
          ) {
            setMessages(fresh.messages);
          }
        },
      }),
    [
      dropIncompleteHeld,
      refreshCurrentConversation,
      setMessages,
      showError,
      switchConversation,
      taskForKey,
    ],
  );
  useEffect(() => {
    reconcileWithServerRef.current = reconcileWithServer;
    // A refused submission goes back to its composer and is never lost: if
    // the composer already holds a newer draft, it stays held until the
    // composer is empty again.
    returnRejectedRef.current = async (key: string) => {
      await heldSubmissionsReady();
      if (dropIncompleteHeld(key)) return;
      if (restoreHeldSubmission(key)) {
        settleSubmission(key);
      } else if (heldSubmission(key)) {
        rejectedWaitingRef.current.add(key);
        showError(
          'Your earlier message was not sent. It will return to the composer when the composer is empty.',
        );
      } else {
        settlePendingSubmission(key);
      }
    };
  }, [dropIncompleteHeld, reconcileWithServer, showError]);
  useEffect(() => {
    if (input || pendingAttachments.length > 0) return;
    for (const key of rejectedWaitingRef.current) {
      if (restoreHeldSubmission(key)) {
        rejectedWaitingRef.current.delete(key);
        settleSubmission(key);
        break;
      }
    }
  }, [input, pendingAttachments]);
  // After a reload (or on a later visit), resolve submissions whose outcome
  // this browser never saw, by key. Entries younger than a minute may still
  // be in flight in another tab and are left to it.
  const reconciledOnLoadRef = useRef(false);
  useEffect(() => {
    if (reconciledOnLoadRef.current) return;
    reconciledOnLoadRef.current = true;
    void (async () => {
      await heldSubmissionsReady();
      const settledBefore = Date.now() - UNRESOLVED_MIN_AGE_MS;
      for (const { key, entry } of unresolvedSubmissions().slice(0, 10)) {
        if (entry.createdAt > settledBefore) continue;
        const task = await taskForKey(key);
        if (task === undefined) continue; // unknown: try again next time
        if (task === null) {
          // Never accepted. A held draft goes back to its composer (sending
          // it reuses the key); without one there is nothing to resend.
          if (dropIncompleteHeld(key)) continue;
          if (!restoreHeldSubmission(key) && !heldSubmission(key)) {
            settlePendingSubmission(key);
          }
        } else if (TERMINAL_TASK_STATUSES.has(task.status)) {
          finishSubmission(key);
        } else {
          // Record (clearing the composer it was sent from) before promoting.
          recordSubmission(key, task.id);
          promoteSubmission(key, task.conversationId);
        }
      }
    })();
  }, [dropIncompleteHeld, taskForKey]);
  useEffect(() => {
    stopInFlightRef.current = stopInFlight;
  }, [stopInFlight]);
  const beforeStop = useCallback((): Promise<StopOutcome> | void => {
    // Settle and reconcile the submission this Stop was for, even if another
    // one starts before the server answers.
    const scope =
      currentIdRef.current || latestConversationIdRef.current || null;
    // Only this conversation's submission: a pending one from elsewhere
    // must keep its key whatever happens to the task stopped here.
    // A second press while this conversation's Stop is still being
    // confirmed waits for the same answer; it never counts as a stop of its
    // own (the stream is already aborted, so it would look request-bound).
    const pending = pendingStopRef.current;
    if (pending && pending.scope === scope) return pending.outcome;
    const key =
      activeSubmissionScopeRef.current === scope
        ? activeSubmissionKeyRef.current
        : null;
    const confirmation = cancelActiveTask();
    if (!confirmation) return;
    const seq = ++stopSeqRef.current;
    setStoppingIn(scope);
    const outcome = confirmation.then((result) => {
      if (result !== 'unconfirmed' && key) finishSubmission(key);
      if (seq === stopSeqRef.current) setStoppingIn(undefined);
      if (pendingStopRef.current?.outcome === outcome) {
        pendingStopRef.current = null;
      }
      void reconcileWithServer(key);
      return result;
    });
    pendingStopRef.current = { scope, outcome };
    return outcome;
  }, [cancelActiveTask, reconcileWithServer]);
  const handleStopResolved = useCallback(
    (outcome: StopOutcome) => {
      if (outcome === 'unconfirmed') {
        showError(
          'Stop could not be confirmed. The request may still be running; its result will appear here.',
        );
      }
    },
    [showError],
  );
  const {
    stoppedMessageIds,
    stoppingMessageIds,
    stopGeneration: handleStopGeneration,
    assignConversationId,
    clearAssignedConversationId,
  } = useStopGeneration({
    messages,
    stop: stopChat,
    archiveEvents: archiveCurrentEvents,
    conversationId: suggestionSubmissionRef.current
      ? latestConversationIdRef.current
      : (currentId ?? null),
    beforeStop,
    onStopResolved: handleStopResolved,
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
    if (isSubmittingSuggestion || isLoading) return;
    suggestionSubmissionRef.current = null;
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

  const handleSuggestionSelect = async (suggestion: HomeSuggestion) => {
    if (suggestionSubmissionRef.current || isLoading || messages.length > 0)
      return;
    const generation = getAuthGeneration();
    const messageId = crypto.randomUUID();
    // No composer receipt, file serialisation, draft reset or draft transfer.
    // The backend alone creates the destination after revalidating the source.
    suggestionSubmissionRef.current = { generation, messageId, pending: true };
    lastTurnRef.current = null;
    chatRequestGenerationRef.current = generation;
    latestConversationIdRef.current = null;
    setIsSubmittingSuggestion(true);
    clearAssignedConversationId();
    setMessages([]);
    try {
      beginSend();
      await sendMessage(
        { text: suggestion.prompt },
        {
          body: {
            id: null,
            suggestion_id: suggestion.id,
            __suggestionAuthGeneration: generation,
          },
        },
      );
    } catch {
      if (chatMountedRef.current && generation === getAuthGeneration()) {
        showError(
          'The suggestion could not be started. Your unfinished draft is preserved; check chat history before trying again.',
        );
      }
    } finally {
      if (chatMountedRef.current && generation === getAuthGeneration()) {
        if (suggestionSubmissionRef.current?.messageId === messageId)
          suggestionSubmissionRef.current.pending = false;
        setIsSubmittingSuggestion(false);
      }
    }
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
    if (
      chatRequestGenerationRef.current !== null &&
      chatRequestGenerationRef.current !== getAuthGeneration()
    )
      return;
    const conversationEvent = flattenedData.find(isConversationDataEvent);
    if (!conversationEvent) {
      if (currentId) {
        urlUpdatedRef.current = false;
      }
      return;
    }

    const conversationId = conversationEvent.conversation_id;
    const suggestionSubmission = suggestionSubmissionRef.current;
    if (
      suggestionSubmission &&
      suggestionSubmission.generation !== getAuthGeneration()
    )
      return;
    latestConversationIdRef.current = conversationId;
    renderedConversationIdRef.current = conversationId;
    if (!currentId || suggestionSubmission) {
      // A fast Stop can be recorded before the URL has the backend-assigned
      // conversation ID. Promote those provisional markers immediately so
      // the router transition does not make the marker disappear.
      assignConversationId(conversationId);
    }
    if (activeSubmissionKeyRef.current) {
      // A new chat's pending submission now belongs to this conversation, so
      // a resend from here reuses its key.
      promoteSubmission(activeSubmissionKeyRef.current, conversationId);
    }
    if (lastTurnRef.current?.conversationId === null) {
      lastTurnRef.current = { ...lastTurnRef.current, conversationId };
    }
    if (activeSubmissionScopeRef.current === null) {
      activeSubmissionScopeRef.current = conversationId;
    }
    // A Stop pressed before the new chat was named belongs to it.
    setStoppingIn((previous) =>
      previous === null ? conversationId : previous,
    );
    const hasCouncilEvent = flattenedData.some(isCouncilDataEvent);
    const hasCouncilDoneEvent = flattenedData.some(isCouncilDoneDataEvent);
    const shouldSyncConversationState = !hasCouncilEvent || hasCouncilDoneEvent;

    if ((!currentId || suggestionSubmission) && !urlUpdatedRef.current) {
      if (
        !suggestionSubmission &&
        !draftRef.current.transferToConversation(conversationId)
      ) {
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

    if (currentId && currentId === conversationId) {
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

  const chatComposer = (
    <form onSubmit={handleSubmit} className="mx-auto w-full max-w-3xl">
      <ChatInputBar
        selectedModel={activeModel}
        onSelectModel={(modelId) => {
          setActiveModel(modelId);
          if (currentId) setConversationModel(currentId, modelId);
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
        isLoading={inputIsBusy || isSubmittingSuggestion}
        onStop={handleStopGeneration}
        attachments={attachmentItems}
        onAttachFiles={handleAttachFiles}
        onRemoveAttachment={handleRemoveAttachment}
      />
    </form>
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
          search={conversationSearch}
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
          // Panel's inner wrapper defaults to overflow:auto. Only main may
          // scroll the chat; clip also prevents focus/programmatic outer scroll.
          style={{ overflow: 'clip' }}
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
              className="flex-1 min-h-0 overflow-y-auto overscroll-y-contain"
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
                    onSuggestionSelect={handleSuggestionSelect}
                    isSubmittingSuggestion={isSubmittingSuggestion}
                    composer={chatComposer}
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
                    // Disclosures (fallback, budget-fitted output) arrive as later
                    // routing updates, so the notice reads the latest one.
                    const latestRoutingEvent =
                      msgEvents.findLast(isRoutingEvent);
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
                                fallback={latestRoutingEvent?.fallback}
                                reasonCodes={latestRoutingEvent?.reason_codes}
                              />
                            </div>
                          )}

                        {councilEventsInMessage && (
                          <div className="w-full space-y-4">
                            {councilInterviewEvent && (
                              <CouncilInterviewCard
                                event={councilInterviewEvent}
                                onSendConfig={(config) => {
                                  // A retry of this turn re-sends no files.
                                  lastTurnRef.current = {
                                    conversationId:
                                      currentId ||
                                      latestConversationIdRef.current ||
                                      null,
                                    attachments: [],
                                  };
                                  // Council configuration runs request-
                                  // bound (DURABLE_REQUEST_DESIGN §7): no
                                  // task exists to replay, so it is not
                                  // held, and a retry is a new run.
                                  const text = `/council config: preset=${config.preset}, rounds=${config.rounds}, audit=${config.audit}`;
                                  beginSend();
                                  void sendMessage(
                                    { text },
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
                            {stoppingMessageIds.has(message.id) && (
                              <div
                                role="status"
                                className="text-xs text-[var(--color-text-muted)]"
                              >
                                (stopping…)
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
                                <>
                                  <CopyResponseButton
                                    content={messageContent}
                                  />
                                  <TextToSpeechButton
                                    text={messageContent}
                                    messageId={message.id}
                                    conversationId={currentId ?? null}
                                    available={
                                      currentMessagesMatch &&
                                      (!isLast || !isLoading)
                                    }
                                  />
                                </>
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
                            {stoppingMessageIds.has(message.id) && (
                              <div
                                role="status"
                                className="text-xs text-[var(--color-text-muted)]"
                              >
                                (stopping…)
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
                            <SuggestionSourceContext
                              context={
                                message.metadata?.home_suggestion ??
                                persistedMessagesById.get(message.id)?.metadata
                                  ?.home_suggestion
                              }
                            />
                          </div>
                        ) : null}
                      </CollapsibleMessage>
                    );
                  })}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </main>

            {messages.length > 0 && (
              <footer className="relative shrink-0 bg-[var(--color-bg-primary)] pb-safe-panel">
                {isScrolledUp && isLoading && (
                  <button
                    type="button"
                    onClick={() => {
                      jumpToLatest();
                      scrollContainerRef.current?.focus({
                        preventScroll: true,
                      });
                    }}
                    className="absolute bottom-full mb-4 right-4 min-h-touch px-4 rounded-full shadow-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] text-sm font-medium text-[var(--color-text-primary)]"
                  >
                    Jump to latest
                  </button>
                )}
                <TtsPlaybackBar />
                {chatComposer}
              </footer>
            )}
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

/**
 * Non-remounting playback scope.
 *
 * Conversation and auth generation are passed down as props instead of a key so
 * ChatContent (streaming state, drafts) stays mounted while a committed scope
 * change synchronously cancels read-aloud work inside the provider.
 */
function AudioPlaybackScopedProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { currentId } = useConversationHistoryContext();
  const authGeneration = useAuthGeneration();
  const scope = useMemo(
    () => ({ conversationId: currentId ?? null, authGeneration }),
    [authGeneration, currentId],
  );
  return (
    <AudioPlaybackProvider scope={scope}>{children}</AudioPlaybackProvider>
  );
}

/** Unresolved submissions younger than this are left to the tab sending them. */
const UNRESOLVED_MIN_AGE_MS = 60_000;

/**
 * A submission was refused or cannot be resent: forget its key and its held
 * draft. Whatever the composer shows (perhaps that draft, put back) stays.
 */
function settleSubmission(key: string): void {
  settlePendingSubmission(key);
  releaseSubmission(key);
}

/**
 * The server ran the submission (its task ended, or a request-bound turn
 * finished): forget its key, and the draft that still holds it.
 */
function finishSubmission(key: string): void {
  settlePendingSubmission(key);
  acceptSubmission(key);
}

/**
 * The server has the submission's task: its held draft (and the composer
 * text, if unchanged) is no longer needed for a resend, while the key stays
 * pending until the task ends.
 */
function recordSubmission(key: string, taskId: string): void {
  recordSubmissionTask(key, taskId);
  acceptSubmission(key);
}

/** A new chat's submission now belongs to the conversation the backend named. */
function promoteSubmission(key: string, conversationId: string): void {
  promotePendingSubmission(key, conversationId);
  promoteHeldSubmission(key, conversationId);
}

export default function ChatPage() {
  return (
    <Suspense fallback={<ChatSkeleton />}>
      <ConversationHistoryProvider>
        <AudioPlaybackScopedProvider>
          <ChatContentWrapper />
        </AudioPlaybackScopedProvider>
      </ConversationHistoryProvider>
    </Suspense>
  );
}
