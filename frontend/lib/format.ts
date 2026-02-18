/**
 * Shared formatting utilities for message content
 */

export function formatMessageContent(content: string): string {
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
}
