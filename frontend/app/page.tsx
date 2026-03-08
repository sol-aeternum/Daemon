"use client";

import { ChatInputBar } from "../components/ChatInputBar";
import { useChat } from "@ai-sdk/react";
import { useState, useRef, useEffect, Suspense, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useStt } from "../hooks/useStt";
import { ErrorProvider, useError } from "../components/ErrorProvider";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ConnectionStatus } from "../components/ConnectionStatus";
import { ConversationList } from "../components/ConversationList";
import { ToolCallLog } from "../components/ToolCallBlock";
import { MobileHeader } from "../components/MobileHeader";
import ChatSkeleton from "../components/ChatSkeleton";
import { useConversationHistory } from "../hooks/useConversationHistory";
import { ConversationHistoryProvider, useConversationHistoryContext } from "../components/ConversationHistoryProvider";
import { AudioPlaybackProvider } from "../components/AudioPlaybackProvider";
import { useEventArchive } from "../hooks/useEventArchive";
import { formatMessageContent } from "../lib/format";
import { useAgentStatus } from "../hooks/useAgentStatus";
import { AgentStatusList } from "../components/AgentStatusList";
import { OfflineIndicator } from "../components/OfflineIndicator";
import { RetryButton } from "../components/RetryButton";
import { useOnlineStatus } from "../hooks/useOnlineStatus";
import { MicButton } from "../components/MicButton";
import { TextToSpeechButton } from "../components/TextToSpeechButton";
import { StreamingTtsMessage } from "../components/StreamingTtsMessage";
import { useLocalStorage } from "../hooks/useLocalStorage";
import { ThinkingIndicator } from "../components/ThinkingIndicator";
import MarkdownMessage from "../components/MarkdownMessage";
import { ChatEvent, isChatEvent } from "../lib/events";
import { Message } from "ai";

type ReasoningMessage = Message & {
  reasoning_text?: string;
  reasoning_duration_secs?: number;
  reasoning_model?: string;
};

const getModelName = (modelId: string | undefined): string | undefined => {
  if (!modelId) return undefined;
  const parts = modelId.split("/");
  const shortName = parts[parts.length - 1];
  return shortName.replace(/-/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
};

const isRoutingEvent = (event: ChatEvent): event is Extract<ChatEvent, { type: "routing" }> => event.type === "routing";
import { TtsSettings, SttSettings, DEFAULT_TTS_SETTINGS, DEFAULT_STT_SETTINGS } from "../lib/constants";

function ChatContent() {

  const { value: ttsSettings } = useLocalStorage<TtsSettings>(
    "tts_settings",
    DEFAULT_TTS_SETTINGS
  );

  const { value: sttSettings, setValue: setSttSettings } = useLocalStorage<SttSettings>(
    "stt_settings",
    DEFAULT_STT_SETTINGS
  );

  const effectiveSttSettings = sttSettings || DEFAULT_STT_SETTINGS;

  const { isRecording, isConnecting, start, stop, error: sttError } = useStt({
    onTranscript: (text) => setInput(text),
    onPartialTranscript: (text) => setInput(text),
    language: effectiveSttSettings.language,
    enablePartials: effectiveSttSettings.enablePartials,
    debounceMs: 100,
  });

  const [connectionStatus, setConnectionStatus] = useState<"connected" | "disconnected" | "reconnecting">("connected");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
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
    refreshConversations
  } = useConversationHistoryContext();

  const [activeModel, setActiveModel] = useState<string>("auto");

  const currentConversation = getCurrentConversation();

  useEffect(() => {
    if (currentConversation?.selectedModel) {
      setActiveModel(currentConversation.selectedModel);
    }
  }, [currentConversation]);

  useEffect(() => {
    setThoughtFallbackByMessageId({});
  }, [currentId]);

  // State to store events for past messages
  const [archivedEvents, setArchivedEvents] = useState<Record<string, { events: ChatEvent[]; duration: number; requestId?: string | null }>>({});
  const [thoughtFallbackByMessageId, setThoughtFallbackByMessageId] = useState<Record<string, string>>({});
  
  // Ref to track current duration for onFinish access
  const thinkingDurationRef = useRef<number>(0);
  
  // Ref to track current events for onFinish access
  const eventsRef = useRef<ChatEvent[]>([]);

  const lastArchivedEventKeysRef = useRef<Set<string>>(new Set());
  const currentRequestIdRef = useRef<string | null>(null);

  const eventKey = (event: ChatEvent) => {
    if (event.id) return `id:${event.id}`;
    return `json:${JSON.stringify(event)}`;
  };

  const normalizeThinkingText = (content: string): string => {
    const normalizedNewlines = content.replace(/\r\n/g, "\n");
    const normalizedParagraphs = normalizedNewlines
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.replace(/\s*\n\s*/g, " ").replace(/\s{2,}/g, " ").trim())
      .filter(Boolean);
    return normalizedParagraphs.join("\n\n");
  };

  const getThinkingContent = (msgEvents: ChatEvent[]) => {
    const rawContent = msgEvents
      .filter((e) => e.type === "thinking")
      .map((e) => e.content)
      .join("");
    return normalizeThinkingText(rawContent);
  };

  const { messages, input, setInput, handleInputChange, handleSubmit, isLoading, error, reload, data } = useChat({
    api: "/api/chat",
    body: { id: currentId || null },
    id: currentId || undefined,
    initialMessages: currentConversation?.messages || [],
    fetch: (input, init) => {
      const body = init?.body ? JSON.parse(init.body as string) : {};
      body.model = activeModel;
      // Preserve id from body option
      if (body.id === undefined && currentId) {
        body.id = currentId;
      }
      return fetch(input, {
        ...init,
        body: JSON.stringify(body),
      });
    },
    onFinish: (message) => {
      setConnectionStatus("connected");
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
      showError(err.message || "Chat error occurred");
      setConnectionStatus("disconnected");
    },
  });

  const { 
    getEventsForMessage, 
    getDurationForMessage, 
    archiveCurrentEvents,
    resetArchive 
  } = useEventArchive({
    data: data || [],
    isLoading,
  });

  const persistedMessagesById = useMemo(() => {
    const entries = (currentConversation?.messages || []).reduce<Array<[string, ReasoningMessage]>>(
      (acc, message) => {
        if (message.id) {
          acc.push([message.id, message as ReasoningMessage]);
        }
        return acc;
      },
      [],
    );
    return new Map<string, ReasoningMessage>(entries);
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

  // Auto-scroll: Track scroll position to respect user's reading position
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const { scrollTop, clientHeight, scrollHeight } = container;
      isNearBottomRef.current = scrollHeight - scrollTop - clientHeight < 150;
    };

    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  // Auto-scroll: Scroll to bottom on new messages
  useEffect(() => {
    if (isNearBottomRef.current && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  // Auto-scroll: Keep viewport at bottom during streaming
  useEffect(() => {
    if (isLoading && isNearBottomRef.current && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "auto" });
    }
  }, [messages, isLoading]);

  const handleSelectConversation = async (id: string) => {
    switchConversation(id);
  };

  const handleNewChat = async () => {
    await createConversation();
    setArchivedEvents({});
    thinkingDurationRef.current = 0;
    eventsRef.current = [];
    lastArchivedEventKeysRef.current = new Set();
    currentRequestIdRef.current = null;
  };

  const events: ChatEvent[] = Array.isArray(data)
    ? (data.filter((x): x is ChatEvent => isChatEvent(x)) as ChatEvent[])
    : [];
    
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
  ): value is { type: "conversation"; conversation_id: string } => {
    if (!value || typeof value !== "object") return false;
    const candidate = value as { type?: unknown; conversation_id?: unknown };
    return (
      candidate.type === "conversation"
      && typeof candidate.conversation_id === "string"
    );
  };

  // Capture conversation_id from SSE and update URL (for edge cases)
  const urlUpdatedRef = useRef(false);
  useEffect(() => {
    if (!data || data.length === 0) return;
    const conversationEvent = data.find(isConversationDataEvent);
    if (conversationEvent && !currentId && !urlUpdatedRef.current) {
      urlUpdatedRef.current = true;
      router.replace(`/?id=${conversationEvent.conversation_id}`);
      refreshConversations();
    }
    if (currentId) {
      urlUpdatedRef.current = false;
    }
  }, [data, currentId]);

  const agents = useAgentStatus(events);

  if (!isLoaded) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>;
  }

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      {!isOnline && <OfflineIndicator />}
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-40 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div className={`
        fixed inset-y-0 left-0 z-50 w-[280px] bg-white transform transition-transform duration-300 ease-in-out shadow-xl md:shadow-none
        md:relative md:translate-x-0 md:z-0 md:inset-auto md:w-auto
        ${isSidebarOpen ? "translate-x-0" : "-translate-x-full"}
      `}>
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
          sttSettings={effectiveSttSettings}
          setSttSettings={setSttSettings}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
        />
      </div>

      <div className="flex-1 flex flex-col w-full min-w-0 relative">
        {isRecording && (
          <div className="bg-red-500 text-white px-4 py-2 text-center text-sm font-medium animate-pulse">
            Recording... Tap mic to stop
          </div>
        )}
        <MobileHeader 
          title={currentConversation?.title || "New conversation"} 
          onOpenSidebar={() => setIsSidebarOpen(true)}
        >
           <div className="flex items-center gap-2">
             <ConnectionStatus status={connectionStatus} onReconnect={reload} />
           </div>
        </MobileHeader>

        <header className="hidden md:flex bg-white border-b px-4 py-3 items-center justify-between">
          <h1 className="text-lg font-semibold">
            {currentConversation?.title || "New conversation"}
          </h1>
          <div className="flex items-center gap-4">
            <ConnectionStatus
              status={connectionStatus}
              onReconnect={reload}
            />
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-500">Cloud</span>
              <button
                disabled
                className="relative inline-flex h-6 w-11 items-center rounded-full bg-gray-200 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 opacity-50 cursor-not-allowed"
                title="Local pipeline coming soon"
              >
                <span className="translate-x-1 inline-block h-4 w-4 transform rounded-full bg-white transition-transform" />
              </button>
              <span className="text-sm text-gray-500">Local</span>
            </div>
          </div>
        </header>

        <main ref={scrollContainerRef} className="flex-1 overflow-y-auto p-4 space-y-4 scroll-smooth">

          {messages.length === 0 ? (
            <div className="flex items-center justify-center h-full text-gray-400">
              <div className="text-center">
                <p className="text-lg mb-2">Welcome to Daemon</p>
                <p className="text-sm">Start a conversation or select a previous chat</p>
              </div>
            </div>
          ) : (
            <>
              {messages.map((message, index) => {
                const isLast = index === messages.length - 1;
                const msgEvents = getEventsForMessage(message.id, isLast);
                const liveThoughtContent = getThinkingContent(msgEvents);
                
                const persistedMessage = persistedMessagesById.get(message.id) as ReasoningMessage | undefined;
                const reasoningMessage = persistedMessage ?? (message as ReasoningMessage);
                const persistedReasoning = reasoningMessage.reasoning_text;
                const rawDuration = typeof reasoningMessage.reasoning_duration_secs === "number"
                  ? reasoningMessage.reasoning_duration_secs
                  : undefined;
                const persistedDuration = rawDuration !== undefined
                  ? Math.max(1, rawDuration)
                  : undefined;
                const fallbackDuration = persistedReasoning && persistedDuration === undefined
                  ? 1
                  : persistedDuration;
                const persistedModel = reasoningMessage.reasoning_model;
                const routingEvent = msgEvents.find(isRoutingEvent);
                const routingModel = routingEvent?.model;
                const fallbackThought = thoughtFallbackByMessageId[message.id];
                const thoughtContent = liveThoughtContent || persistedReasoning || fallbackThought || "";
                const thoughtEvent: ChatEvent | undefined = thoughtContent
                  ? { type: "thinking", content: thoughtContent }
                  : undefined;
                const modelName = getModelName(persistedModel || routingModel);
                
                return (
                  <div
                    key={message.id}
                    className={`flex flex-col mb-6 ${
                      message.role === "user" ? "items-end" : "items-start"
                    }`}
                  >
                    {/* Render tools and thinking for assistant messages */}
                    {message.role === "assistant" && (
                      <div className="max-w-[85%] md:max-w-[80%] w-full mb-2 space-y-2">
                          <ThinkingIndicator 
                            event={thoughtEvent} 
                            isThinking={isLast && isLoading} 
                            isFinished={!isLast || !isLoading}
                            duration={isLast && isLoading ? undefined : (getDurationForMessage(message.id) > 0 ? getDurationForMessage(message.id) : fallbackDuration)}
                            modelName={modelName}
                            onDurationChange={(d) => thinkingDurationRef.current = d} 
                          />
                         <ToolCallLog events={msgEvents} />
                      </div>
                    )}

                    <div
                      className={`max-w-[85%] md:max-w-[80%] rounded-lg px-4 py-2 ${
                        message.role === "user"
                          ? "bg-blue-600 text-white"
                          : "bg-white border border-gray-200 shadow-sm"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        {message.role === "assistant" ? (
                          <MarkdownMessage content={message.content} />
                        ) : (
                          <div className="flex-1 whitespace-pre-wrap">{formatMessageContent(message.content)}</div>
                        )}
                        {message.role === "assistant" && message.content && (
                          isLast && isLoading ? (
                            <StreamingTtsMessage
                              messageId={message.id}
                              text={formatMessageContent(message.content)}
                              isStreaming={isLast && isLoading}
                              enabled={Boolean(ttsSettings?.enabled)}
                              voice={ttsSettings?.voice}
                              model={ttsSettings?.model}
                              speed={ttsSettings?.speed}
                            />
                          ) : (
                            <TextToSpeechButton text={formatMessageContent(message.content)} />
                          )
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </>
          )}
        </main>

        <footer className="bg-white border-t border-gray-200 p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <form onSubmit={handleSubmit}>
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
              micDisabled={isLoading || !currentId || !isOnline}
              micError={sttError}
              input={input}
              onInputChange={handleInputChange}
              onSubmit={handleSubmit}
              isLoading={isLoading}
            />
          </form>
        </footer>
      </div>

      <AgentStatusList agents={agents} />
    </div>
  );
}

function ChatContentWrapper() {
  const {
    currentId,
    isLoaded,
  } = useConversationHistoryContext();

  if (!isLoaded) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>;
  }

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
