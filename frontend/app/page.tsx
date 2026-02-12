"use client";

import { useChat } from "@ai-sdk/react";
import { useState, useRef, useEffect } from "react";
import { ErrorProvider, useError } from "../components/ErrorProvider";
import { ConnectionStatus } from "../components/ConnectionStatus";
import { ConversationList } from "../components/ConversationList";
import { ToolCallLog } from "../components/ToolCallBlock";
import { MobileHeader } from "../components/MobileHeader";
import { useConversationHistory } from "../hooks/useConversationHistory";
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
import { ChatEvent, isChatEvent } from "../lib/events";

function ChatContent() {
  type TtsSettings = {
    enabled: boolean;
    voice: string;
    model: string;
    speed: number;
    format: string;
  };

  const DEFAULT_TTS_SETTINGS: TtsSettings = {
    enabled: true,
    voice: "Xb7hH8MSUJpSbSDYk0k2",
    model: "eleven_flash_v2_5",
    speed: 1.0,
    format: "mp3",
  };

  const { value: ttsSettings } = useLocalStorage<TtsSettings>(
    "tts_settings",
    DEFAULT_TTS_SETTINGS
  );
  const [connectionStatus, setConnectionStatus] = useState<"connected" | "disconnected" | "reconnecting">("connected");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { showError } = useError();
  const { isOnline } = useOnlineStatus();

  const {
    conversations,
    currentId,
    isLoaded,
    createConversation,
    updateConversation,
    deleteConversation,
    getCurrentConversation,
    switchConversation,
  } = useConversationHistory();

  const currentConversation = getCurrentConversation();

  // State to store events for past messages
  const [archivedEvents, setArchivedEvents] = useState<Record<string, { events: ChatEvent[]; duration: number; requestId?: string | null }>>({});
  
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

  const { messages, input, setInput, handleInputChange, handleSubmit, isLoading, error, reload, data } = useChat({
    api: "/api/chat",
    id: currentId || undefined,
    initialMessages: currentConversation?.messages || [],
    onFinish: (message) => {
      setConnectionStatus("connected");
      if (eventsRef.current.length > 0) {
        const requestId = currentRequestIdRef.current;
        const archived = requestId
          ? eventsRef.current.filter((event) => event.request_id === requestId)
          : [...eventsRef.current];
        setArchivedEvents(prev => ({
          ...prev,
          [message.id]: {
            events: archived,
            duration: thinkingDurationRef.current,
            requestId,
          }
        }));
        lastArchivedEventKeysRef.current = new Set(archived.map(eventKey));
      }
      thinkingDurationRef.current = 0;
    },
    onError: (err) => {
      showError(err.message || "Chat error occurred");
      setConnectionStatus("disconnected");
    },
  });

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

  const handleSelectConversation = async (id: string) => {
    switchConversation(id);
  };

  const handleNewChat = async () => {
    createConversation();
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

  const getEventsForMessage = (messageId: string, isLastMessage: boolean) => {
    if (archivedEvents[messageId]) return archivedEvents[messageId].events;
    if (isLastMessage) {
      const requestId = currentRequestIdRef.current;
      if (requestId) {
        return events.filter((event) => event.request_id === requestId);
      }
      const lastArchivedKeys = lastArchivedEventKeysRef.current;
      return events.filter((event) => !lastArchivedKeys.has(eventKey(event)));
    }
    return [];
  };

  const getDurationForMessage = (messageId: string) => {
     return archivedEvents[messageId]?.duration || 0;
  };

  const formatMessageContent = (content: string) => {
    return content
      .replace(/!\[.*?\]\(\/generated-images\/.*?\)/g, "")
      .replace(/\*\*Image:\*\*\s*`\/generated-images\/[^`]+`/gi, "")
      .replace(/`\/generated-images\/[^`]+`/gi, "")
      .replace(/\*\*File:\*\*\s*`\/generated-audio\/[^`]+`/gi, "")
      .replace(/`\/generated-audio\/[^`]+`/gi, "")
      .replace(/\[.*?\]\(\/generated-audio\/[^)]+\)/gi, "")
      .replace(/\*\*Audio Details:\*\*[\s\S]*?(?=\n\n|\n[A-Z]|$)/gi, "")
      .replace(/\*Generated using .*?\*/gi, "")
      .replace(/The image was generated using[\s\S]*?(\.|$)/gi, "")
      .replace(/Generated using[\s\S]*?(\.|$)/gi, "")
      .replace(/^[\s>*]*\*?the image was generated using.*$/gim, "")
      .replace(/^[\s>*]*\*?generated using.*$/gim, "")
      .trim();
  };








  const getThinkingContent = (msgEvents: ChatEvent[]) => {
    return msgEvents
      .filter((e) => e.type === "thinking")
      .map((e) => e.content)
      .join("");
  };

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
          onNewChat={() => {
            handleNewChat();
            setIsSidebarOpen(false);
          }}
        />
      </div>

      <div className="flex-1 flex flex-col w-full min-w-0 relative">
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
              <button className="relative inline-flex h-6 w-11 items-center rounded-full bg-gray-200 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2">
                <span className="translate-x-1 inline-block h-4 w-4 transform rounded-full bg-white transition-transform" />
              </button>
              <span className="text-sm text-gray-500">Local</span>
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4 space-y-4 scroll-smooth">

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
                const thoughtContent = getThinkingContent(msgEvents);
                const thoughtEvent = thoughtContent ? { type: "thinking", content: thoughtContent } as ChatEvent : undefined;
                
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
                           duration={isLast && isLoading ? undefined : getDurationForMessage(message.id)}
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
                        <div className="flex-1 whitespace-pre-wrap">{formatMessageContent(message.content)}</div>
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

        <footer className="bg-white border-t p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <form onSubmit={handleSubmit} className="flex gap-2">
            {error && (
              <RetryButton onRetry={reload} isLoading={isLoading} />
            )}
            <MicButton
              onTranscript={(text) => setInput(text)}
              onPartialTranscript={(text) => setInput(text)}
              disabled={isLoading || !currentId || !isOnline}
            />
            <input
              value={input}
              onChange={handleInputChange}
              placeholder={isOnline ? "Ask anything..." : "You are offline"}
              className="flex-1 rounded-lg border border-gray-300 px-4 py-3 md:py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[44px]"
              disabled={isLoading || !currentId || !isOnline}
            />
            <button
              type="submit"
              disabled={isLoading || !input.trim() || !currentId || !isOnline}
              className="bg-blue-600 text-white px-4 py-2 rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] min-w-[44px] flex items-center justify-center"
            >
              {isLoading ? "..." : "Send"}
            </button>
          </form>
        </footer>
      </div>

      <AgentStatusList agents={agents} />
    </div>
  );
}

export default function ChatPage() {
  return (
    <ErrorProvider>
      <ChatContent />
    </ErrorProvider>
  );
}
