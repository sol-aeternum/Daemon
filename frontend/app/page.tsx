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
import { SkeletonBlock } from "../components/ui/Skeleton";
import { ChatEvent, isChatEvent } from "../lib/events";
import { Search, Image, Code, MessageSquare, Sparkles } from "lucide-react";
import { Message } from "ai";

type ReasoningMessage = Message & {
  reasoning_text?: string;
  reasoning_duration_secs?: number;
  reasoning_model?: string;
};

const getPartContent = (part: unknown): string => {
  if (typeof part !== "object" || part === null) {
    return "";
  }

  const textValue = (part as { text?: unknown }).text;
  if (typeof textValue === "string") {
    return textValue;
  }

  const contentValue = (part as { content?: unknown }).content;
  if (typeof contentValue === "string") {
    return contentValue;
  }

  return "";
};

const getMessageContent = (message: Message): string => {
  const rawContent = message.content;
  if (typeof rawContent === "string" && rawContent.trim().length > 0) {
    return rawContent;
  }

  const messageWithParts = message as Message & { parts?: unknown };
  if (Array.isArray(messageWithParts.parts)) {
    const combinedParts = messageWithParts.parts.map(getPartContent).join("");
    if (combinedParts.trim().length > 0) {
      return combinedParts;
    }
  }

  return typeof rawContent === "string" ? rawContent : "";
};

const getModelName = (modelId: string | undefined): string | undefined => {
  if (!modelId) return undefined;
  const parts = modelId.split("/");
  const shortName = parts[parts.length - 1];
  return shortName.replace(/-/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
};

const isRoutingEvent = (event: ChatEvent): event is Extract<ChatEvent, { type: "routing" }> => event.type === "routing";
import { TtsSettings, SttSettings, DEFAULT_TTS_SETTINGS, DEFAULT_STT_SETTINGS } from "../lib/constants";

// =============================================================================
// WELCOME SCREEN COMPONENT
// =============================================================================

interface WelcomeScreenProps {
  setInput: (input: string) => void;
  onSubmit: (e?: React.FormEvent) => void;
}

function WelcomeScreen({ setInput, onSubmit }: WelcomeScreenProps) {
  const [greeting, setGreeting] = useState("Good evening");
  const [userName, setUserName] = useState<string | null>(null);

  useEffect(() => {
    // Time-based greeting
    const hour = new Date().getHours();
    let timeGreeting = "Good evening";
    if (hour >= 5 && hour < 12) {
      timeGreeting = "Good morning";
    } else if (hour >= 12 && hour < 17) {
      timeGreeting = "Good afternoon";
    } else if (hour >= 17 && hour < 22) {
      timeGreeting = "Good evening";
    }
    setGreeting(timeGreeting);

    // Try to get user name from localStorage (could be set in settings)
    const storedName = localStorage.getItem("user_name");
    if (storedName) {
      setUserName(storedName);
    }
  }, []);

  const quickActions = [
    {
      icon: Search,
      label: "Research",
      starter: "I need to research...",
      gradient: "from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10",
      iconColor: "text-[var(--color-accent-primary)]",
    },
    {
      icon: Image,
      label: "Create Image",
      starter: "Create an image of...",
      gradient: "from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10",
      iconColor: "text-[var(--color-accent-primary)]",
    },
    {
      icon: Code,
      label: "Write Code",
      starter: "Write a function that...",
      gradient: "from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10",
      iconColor: "text-[var(--color-accent-primary)]",
    },
    {
      icon: MessageSquare,
      label: "Just Chat",
      starter: "",
      gradient: "from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10",
      iconColor: "text-[var(--color-accent-primary)]",
    },
  ];

  const handleActionClick = (starter: string) => {
    setInput(starter);
    // Focus the input after a short delay to allow state update
    setTimeout(() => {
      const inputEl = document.querySelector('input[type="text"], textarea') as HTMLElement;
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
                <div className={`absolute inset-0 bg-gradient-to-br ${action.gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-300`} />
                
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
  const autoScrollEnabledRef = useRef(true);
  const lastMessageSignatureRef = useRef("");
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
    const normalizedNewlines = content.replace(/\r\n/g, "\n").replace(/\t/g, " ");
    const lines = normalizedNewlines
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    const shortLineRatio = lines.length
      ? lines.filter((line) => line.split(/\s+/).length <= 2).length / lines.length
      : 0;

    const looksTokenFragmented = lines.length >= 12 && shortLineRatio > 0.65;

    if (looksTokenFragmented) {
      return normalizedNewlines
        .replace(/\s*\n+\s*/g, " ")
        .replace(/\s{2,}/g, " ")
        .trim();
    }

    const normalizedParagraphs = normalizedNewlines
      .split(/\n{3,}/)
      .map((paragraph) => paragraph.replace(/[ \t]*\n[ \t]*/g, " ").replace(/\s{2,}/g, " ").trim())
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
    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (!messagesEndRef.current || messages.length === 0) return;
    if (!autoScrollEnabledRef.current) return;

    const lastMessage = messages[messages.length - 1];
    const signature = `${messages.length}:${lastMessage?.id ?? ""}`;
    const isNewMessage = signature !== lastMessageSignatureRef.current;
    lastMessageSignatureRef.current = signature;

    const behavior: ScrollBehavior = isLoading && !isNewMessage ? "auto" : "smooth";
    messagesEndRef.current.scrollIntoView({ behavior });
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
    <div className="flex h-screen bg-[var(--color-bg-tertiary)] overflow-hidden">
      {!isOnline && <OfflineIndicator />}
      {isSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-40 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div className={`
        fixed inset-y-0 left-0 z-50 w-[260px] bg-[var(--color-bg-secondary)] transform transition-transform duration-300
        md:relative md:inset-auto md:z-0 md:w-auto md:translate-x-0
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
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
        />
      </div>

      <div className="flex-1 flex flex-col w-full min-w-0 relative">
        {isRecording && (
          <div className="bg-[var(--color-status-error)] text-white px-4 py-2 text-center text-sm font-medium animate-pulse">
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

        <header className="hidden md:flex bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)] px-4 py-3 items-center justify-between">
          <h1 className="text-lg font-semibold">
            {currentConversation?.title || "New conversation"}
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
                  <SkeletonBlock width="60%" height="4rem" className="bg-[var(--color-bg-secondary)]" />
                </div>
              </div>
              {/* User message skeleton - right aligned */}
              <div className="flex flex-col items-end mb-6">
                <div className="max-w-[85%] md:max-w-[80%]">
                  <SkeletonBlock width="80%" height="3rem" className="bg-[var(--color-accent-primary)] opacity-60" />
                </div>
              </div>
              {/* Assistant message skeleton - left aligned */}
              <div className="flex flex-col items-start mb-6">
                <div className="max-w-[85%] md:max-w-[80%] space-y-3">
                  <SkeletonBlock width="50%" height="5rem" className="bg-[var(--color-bg-secondary)]" />
                </div>
              </div>
              {/* User message skeleton - right aligned */}
              <div className="flex flex-col items-end mb-6">
                <div className="max-w-[85%] md:max-w-[80%]">
                  <SkeletonBlock width="70%" height="2.5rem" className="bg-[var(--color-accent-primary)] opacity-60" />
                </div>
              </div>
              {/* Assistant message skeleton - left aligned */}
              <div className="flex flex-col items-start mb-6">
                <div className="max-w-[85%] md:max-w-[80%] space-y-3">
                  <SkeletonBlock width="75%" height="4rem" className="bg-[var(--color-bg-secondary)]" />
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
                const msgEvents = getEventsForMessage(message.id, isLast);
                const liveThoughtContent = getThinkingContent(msgEvents);
                const messageContent = getMessageContent(message);
                const formattedMessageContent = formatMessageContent(messageContent);
                const showTts = message.role === "assistant" && formattedMessageContent.trim().length > 0;
                
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
                    className={`mb-8 ${message.role === "user" ? "flex justify-end" : "space-y-3"}`}
                  >
                    {/* Render tools and thinking for assistant messages */}
                    {message.role === "assistant" && (
                      <div className="w-full space-y-2">
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

                    {message.role === "assistant" ? (
                      <div className="w-full space-y-2">
                        <div className="w-full">
                          <MarkdownMessage content={messageContent} />
                        </div>
                        {showTts && (
                          <div className="flex justify-start">
                            {isLast && isLoading ? (
                              <StreamingTtsMessage
                                messageId={message.id}
                                text={formattedMessageContent}
                                isStreaming={isLast && isLoading}
                                enabled={Boolean(ttsSettings?.enabled)}
                                voice={ttsSettings?.voice}
                                model={ttsSettings?.model}
                                speed={ttsSettings?.speed}
                              />
                            ) : (
                              <TextToSpeechButton text={formattedMessageContent} />
                            )}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="max-w-[85%] md:max-w-[75%] rounded-2xl border border-[var(--color-accent-active)]/50 bg-[var(--color-accent-primary)] px-4 py-3 text-white shadow-sm">
                        <div className="whitespace-pre-wrap leading-relaxed font-medium">{formattedMessageContent}</div>
                      </div>
                    )}
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
          )}
        </main>

        <footer className="bg-[var(--color-bg-secondary)] border-t border-[var(--color-border-primary)] p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <form onSubmit={handleSubmit} className="mx-auto w-full max-w-3xl">
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
              isLocal={false}
              onToggleLocal={() => {}}
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
