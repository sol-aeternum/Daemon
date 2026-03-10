/**
 * Shared formatting utilities for message content
 */

export function formatMessageContent(content: string): string {
  return content
    .replace(/!\[.*?\]\(\/generated-images\/.*?\)/g, "")
    .replace(/^\s*(?:https?:\/\/[^\s]+)?\/generated-images\/[^\s`]+\s*$/gim, "")
    .replace(/\*\*Image:\*\*\s*`\/generated-images\/[^`]+`/gi, "")
    .replace(/`\/generated-images\/[^`]+`/gi, "")
    .replace(/\*\*File:\*\*\s*`\/generated-audio\/[^`]+`/gi, "")
    .replace(/^\s*(?:https?:\/\/[^\s]+)?\/generated-audio\/[^\s`]+\s*$/gim, "")
    .replace(/`\/generated-audio\/[^`]+`/gi, "")
    .replace(/\[.*?\]\(\/generated-audio\/[^)]+\)/gi, "")
    .replace(/\*\*Audio Details:\*\*[\s\S]*?(?=\n\n|\n[A-Z]|$)/gi, "")
    .replace(/\*Generated using .*?\*/gi, "")
    .replace(/The image was generated using[\s\S]*?(\.|$)/gi, "")
    .replace(/Generated using[\s\S]*?(\.|$)/gi, "")
    .replace(/^[\s>*]*\*?the image was generated using.*$/gim, "")
    .replace(/^[\s>*]*\*?generated using.*$/gim, "")
    .trim();
}


export function formatRelativeTime(date: string | Date): string {
  const now = new Date();
  const then = new Date(date);
  const diffMs = now.getTime() - then.getTime();
  const diffSeconds = Math.floor(diffMs / 1000);
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);
  const diffMonths = Math.floor(diffDays / 30);
  const diffYears = Math.floor(diffDays / 365);

  if (diffSeconds < 10) return "Just now";
  if (diffSeconds < 60) return `${diffSeconds} seconds ago`;
  if (diffMinutes < 60) return `${diffMinutes} minutes ago`;
  if (diffHours < 24) return `${diffHours} hours ago`;
  if (diffDays < 30) return `${diffDays} days ago`;
  if (diffMonths < 12) return `${diffMonths} months ago`;
  return `${diffYears} years ago`;
}