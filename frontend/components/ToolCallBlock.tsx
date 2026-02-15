import { useState, useRef } from "react";
import { ChatEvent, isToolCallEvent, isToolResultEvent } from "../lib/events";
import { Download, Maximize2, X, Loader2, ChevronRight, Check, Volume2, VolumeX, Play, Pause } from "lucide-react";

export interface ToolExecution {
  call: ChatEvent;
  result?: ChatEvent;
}

interface ToolCallBlockProps {
  execution: ToolExecution;
}

export function ToolCallBlock({ execution }: ToolCallBlockProps) {
  const { call: rawCall, result: rawResult } = execution;
  const [isExpanded, setIsExpanded] = useState(false);
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);

  if (!isToolCallEvent(rawCall)) {
    return null;
  }
  const call = rawCall;

  const result = rawResult && isToolResultEvent(rawResult) ? rawResult : null;

  // 1. Loading State (Call exists, Result missing)
  if (!result) {
    if (call.name === "spawn_agent") {
      const agentType = call.arguments?.agent_type;
      const isAudio = agentType === "audio";
      return (
        <div className="flex items-center gap-2 text-gray-500 text-sm py-2 px-1 animate-pulse">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>{isAudio ? "Creating sound effect..." : "Creating image..."}</span>
        </div>
      );
    }
    // Generic tool loading
    return (
      <div className="flex items-center gap-2 text-gray-500 text-sm py-2 px-1 animate-pulse">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span>Running {call.name}...</span>
      </div>
    );
  }

  // 2. Result State
  let isError = false;
  let imagePath: string | null = null;
  let audioPath: string | null = null;
  let prompt: string | null = null;
  
  try {
    const raw = result.result;
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (parsed?.error || parsed?.success === false) {
      isError = true;
    }

    const imgPath = parsed?.data?.image_path ?? parsed?.image_path;
    const audPath = parsed?.data?.audio_path ?? parsed?.audio_path;
    prompt = parsed?.data?.prompt ?? parsed?.prompt;

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    if (typeof imgPath === "string" && imgPath.startsWith("/generated-images/")) {
      imagePath = `${apiUrl}${imgPath}`;
      isError = false;
    }

    if (typeof audPath === "string" && audPath.startsWith("/generated-audio/")) {
      audioPath = `${apiUrl}${audPath}`;
      isError = false;
    }
  } catch {
    const lowerResult = typeof result.result === "string"
      ? result.result.toLowerCase()
      : "";
    isError = lowerResult.includes("error") && !lowerResult.includes('"error": null');
  }

  // Image Result UI
  if (imagePath) {
    return (
      <div className="my-2">
        {prompt && (
           <div className="text-sm text-gray-500 mb-2 font-medium flex items-center gap-2">
             <Check className="w-4 h-4 text-green-500" />
             <span>Image created</span>
             <span className="text-gray-300">•</span>
             <span className="truncate max-w-md" title={prompt}>{prompt}</span>
           </div>
        )}
        
        <div className="relative group rounded-xl overflow-hidden border border-gray-200 bg-gray-50 shadow-sm max-w-md transition-all hover:shadow-md">
          <img
            src={imagePath}
            alt={prompt || "Generated image"}
            className="w-full h-auto max-h-96 object-cover cursor-pointer hover:opacity-95 transition-opacity"
            onClick={() => setIsLightboxOpen(true)}
          />
          
          <div className="absolute top-2 right-2 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
             <button
              onClick={(e) => {
                e.stopPropagation();
                setIsLightboxOpen(true);
              }}
              className="p-1.5 bg-black/50 hover:bg-black/70 text-white rounded-md backdrop-blur-sm transition-colors"
              title="Expand"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
            <a
              href={imagePath}
              download={`image-${Date.now()}.png`}
              className="p-1.5 bg-black/50 hover:bg-black/70 text-white rounded-md backdrop-blur-sm transition-colors"
              title="Download"
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              <Download className="w-4 h-4" />
            </a>
          </div>
        </div>

        {isLightboxOpen && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/95 backdrop-blur-sm p-4 animate-in fade-in duration-200" onClick={() => setIsLightboxOpen(false)}>
            <button
              onClick={() => setIsLightboxOpen(false)}
              className="absolute top-4 right-4 p-2 text-white/70 hover:text-white bg-white/10 hover:bg-white/20 rounded-full transition-colors"
            >
              <X className="w-6 h-6" />
            </button>
            
            <img
              src={imagePath}
              alt={prompt || "Full resolution image"}
              className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            />
            
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex gap-4" onClick={(e) => e.stopPropagation()}>
              <a
                href={imagePath}
                download={`image-${Date.now()}.png`}
                className="flex items-center gap-2 px-4 py-2 bg-white text-black rounded-full font-medium hover:bg-gray-100 transition-colors shadow-lg"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Download className="w-4 h-4" />
                Download
              </a>
            </div>
          </div>
        )}
      </div>
    );
  }

  // Audio Result UI
  if (audioPath) {
    return (
      <AudioPlayerBlock audioPath={audioPath} prompt={prompt} />
    );
  }

  // Standard Tool Result UI

  // Standard Tool Result UI
  return (
    <div className={`border rounded-lg my-2 overflow-hidden ${
      isError ? "bg-red-50 border-red-200" : "bg-gray-50 border-gray-200"
    }`}>
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className={`w-full px-4 py-2 flex items-center justify-between text-left transition-colors ${
          isError ? "hover:bg-red-100" : "hover:bg-gray-100"
        }`}
      >
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${isError ? "bg-red-500" : "bg-green-500"}`}></span>
          <div className="flex flex-col">
            <span className={`text-sm font-medium ${isError ? "text-red-800" : "text-gray-700"}`}>
              {call.name}
            </span>
          </div>
        </div>
        <ChevronRight
          className={`w-4 h-4 transition-transform ${isExpanded ? "rotate-90" : ""} ${
            isError ? "text-red-600" : "text-gray-500"
          }`}
        />
      </button>
      {isExpanded && (
        <div className="px-4 pb-3 space-y-2">
          <div className="text-xs text-gray-500 font-medium">Input:</div>
          <pre className="text-xs text-gray-600 bg-white border border-gray-200 rounded p-2 overflow-x-auto">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          <div className="text-xs text-gray-500 font-medium">Output:</div>
          <pre className={`text-xs rounded p-2 overflow-x-auto overflow-y-auto max-h-80 whitespace-pre-wrap break-words ${
            isError ? "text-red-700 bg-red-100" : "text-gray-700 bg-white border border-gray-200"
          }`}>
            {result.result}
          </pre>
        </div>
      )}
    </div>
  );
}

interface ToolCallLogProps {
  events: ChatEvent[];
}

export function ToolCallLog({ events }: ToolCallLogProps) {
  const executions: ToolExecution[] = [];

  events.forEach((event) => {
    if (isToolCallEvent(event)) {
      executions.push({ call: event });
    } else if (isToolResultEvent(event)) {
      const resultEvent = event as ChatEvent & { type: "tool_result"; name: string; result: string };
      let foundIndex = -1;
      for (let i = executions.length - 1; i >= 0; i--) {
        const execCall = executions[i].call as ChatEvent & { type: "tool_call"; name: string; arguments: Record<string, unknown> };
        if (execCall.name === resultEvent.name && !executions[i].result) {
          foundIndex = i;
          break;
        }
      }

      if (foundIndex !== -1) {
        executions[foundIndex].result = event;
      } else {
        console.warn("Orphan tool result:", event);
      }
    }
  });

  const getImagePath = (execution: ToolExecution) => {
    if (!execution.result || !isToolResultEvent(execution.result)) return null;
    try {
      const raw = execution.result.result;
      const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
      const path = parsed?.data?.image_path ?? parsed?.image_path;
      return typeof path === "string" && path.startsWith("/generated-images/") ? path : null;
    } catch {
      return null;
    }
  };

  if (executions.length > 1) {
    const spawnExecutions = executions.filter((execution) => 
      isToolCallEvent(execution.call) && execution.call.name === "spawn_agent"
    );
    if (spawnExecutions.length > 1) {
      const lastWithImage = [...spawnExecutions].reverse().find((execution) => getImagePath(execution));
      if (lastWithImage) {
        const keep = new Set([lastWithImage]);
        for (let i = executions.length - 1; i >= 0; i -= 1) {
          const execCall = executions[i].call as ChatEvent & { type: "tool_call"; name: string };
          if (execCall.name === "spawn_agent" && !keep.has(executions[i])) {
            executions.splice(i, 1);
          }
        }
      } else {
        const lastSpawn = spawnExecutions[spawnExecutions.length - 1];
        for (let i = executions.length - 1; i >= 0; i -= 1) {
          const execCall = executions[i].call as ChatEvent & { type: "tool_call"; name: string };
          if (execCall.name === "spawn_agent" && executions[i] !== lastSpawn) {
            executions.splice(i, 1);
          }
        }
      }
    }
  }

  if (executions.length === 0) return null;

  return (
    <div className="space-y-1">
      {executions.map((execution, idx) => (
        <ToolCallBlock key={idx} execution={execution} />
      ))}
    </div>
  );
}

// Audio Player Component
interface AudioPlayerBlockProps {
  audioPath: string;
  prompt: string | null;
}

function AudioPlayerBlock({ audioPath, prompt }: AudioPlayerBlockProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const handlePlayPause = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      const progress = (audioRef.current.currentTime / audioRef.current.duration) * 100;
      setProgress(progress);
    }
  };

  const handleEnded = () => {
    setIsPlaying(false);
    setProgress(0);
  };

  return (
    <div className="my-2">
      {prompt && (
        <div className="text-sm text-gray-500 mb-2 font-medium flex items-center gap-2">
          <Check className="w-4 h-4 text-green-500" />
          <span>Sound effect created</span>
          <span className="text-gray-300">•</span>
          <span className="truncate max-w-md" title={prompt}>{prompt}</span>
        </div>
      )}

      <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl border border-gray-200 max-w-md">
        <audio
          ref={audioRef}
          src={audioPath}
          onTimeUpdate={handleTimeUpdate}
          onEnded={handleEnded}
          preload="metadata"
        />

        <button
          onClick={handlePlayPause}
          className="flex-shrink-0 w-10 h-10 flex items-center justify-center bg-blue-500 hover:bg-blue-600 text-white rounded-full transition-colors"
          title={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? (
            <Pause className="w-5 h-5" />
          ) : (
            <Play className="w-5 h-5 ml-0.5" />
          )}
        </button>

        <div className="flex-1 flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Volume2 className="w-4 h-4 text-gray-400" />
            <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 transition-all duration-100"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        </div>

        <a
          href={audioPath}
          download={`sound-effect-${Date.now()}.mp3`}
          className="flex-shrink-0 p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-200 rounded-lg transition-colors"
          title="Download"
          target="_blank"
          rel="noopener noreferrer"
        >
          <Download className="w-4 h-4" />
        </a>
      </div>
    </div>
  );
}
