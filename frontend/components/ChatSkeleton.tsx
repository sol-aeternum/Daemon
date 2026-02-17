export default function ChatSkeleton() {
  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar skeleton */}
      <div className="w-64 border-r border-gray-200 bg-white p-4 space-y-3">
        <div className="h-8 bg-gray-200 rounded animate-pulse" />
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-10 bg-gray-100 rounded animate-pulse" />
          ))}
        </div>
      </div>
      
      {/* Main area skeleton */}
      <div className="flex-1 flex flex-col">
        {/* Header skeleton */}
        <div className="h-14 border-b border-gray-200 bg-white px-4 flex items-center">
          <div className="h-6 w-32 bg-gray-200 rounded animate-pulse" />
        </div>
        
        {/* Messages skeleton */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {[...Array(4)].map((_, i) => (
            <div
              key={i}
              className={`flex ${i % 2 === 0 ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[70%] p-3 rounded-lg ${
                  i % 2 === 0
                    ? 'bg-blue-500 text-white'
                    : 'bg-gray-100 text-gray-800'
                }`}
              >
                <div className="h-4 bg-gray-300/50 rounded animate-pulse mb-2" />
                <div className="h-4 bg-gray-300/50 rounded animate-pulse w-3/4" />
              </div>
            </div>
          ))}
        </div>
        
        {/* Input skeleton */}
        <div className="p-4 border-t border-gray-200">
          <div className="h-12 bg-gray-100 rounded-lg animate-pulse" />
        </div>
      </div>
    </div>
  );
}
