import { describe, expect, it } from "vitest";
import { sanitizeHtml } from "@/lib/sanitizeHtml";

describe("sanitizeHtml", () => {
  it("strips <script> tags and content", () => {
    const result = sanitizeHtml("<script>alert(1)</script>hello");
    expect(result).not.toMatch(/<script/i);
    expect(result).not.toMatch(/alert/);
    expect(result).toMatch(/hello/);
  });

  it("strips <script src> tags", () => {
    const result = sanitizeHtml('<script src="evil.js"></script>safe');
    expect(result).not.toMatch(/<script/i);
    expect(result).toMatch(/safe/);
  });

  it("strips inline event handlers (onclick)", () => {
    const result = sanitizeHtml('<button onclick="alert(1)">click</button>');
    expect(result).not.toMatch(/onclick/i);
    expect(result).toMatch(/<button/i);
  });

  it("strips inline event handlers (onerror)", () => {
    const result = sanitizeHtml('<img src="x" onerror="alert(1)">');
    expect(result).not.toMatch(/onerror/i);
  });

  it("strips inline style attribute (CSS-based XSS vector)", () => {
    const result = sanitizeHtml('<div style="background:url(javascript:alert(1))">x</div>');
    expect(result).not.toMatch(/style=/i);
  });

  it("strips <style> tags", () => {
    const result = sanitizeHtml("<style>body{background:red}</style>ok");
    expect(result).not.toMatch(/<style/i);
    expect(result).toMatch(/ok/);
  });

  it("strips <form> and <input> tags (form hijacking)", () => {
    const result = sanitizeHtml(
      '<form action="https://evil.com"><input name="x" value="y"></form>safe'
    );
    expect(result).not.toMatch(/<form/i);
    expect(result).not.toMatch(/<input/i);
    expect(result).toMatch(/safe/);
  });

  it("strips javascript: URLs in href", () => {
    const result = sanitizeHtml('<a href="javascript:alert(1)">link</a>');
    expect(result).not.toMatch(/javascript:/i);
  });

  it("strips data: URLs in href (XSS vector)", () => {
    const result = sanitizeHtml(
      '<a href="data:text/html,<script>alert(1)</script>">x</a>'
    );
    expect(result).not.toMatch(/data:text\/html/i);
  });

  it("strips <iframe> tags", () => {
    const result = sanitizeHtml('<iframe src="https://evil.com"></iframe>safe');
    expect(result).not.toMatch(/<iframe/i);
    expect(result).toMatch(/safe/);
  });

  it("preserves safe formatting tags", () => {
    const result = sanitizeHtml("<p>hello <strong>world</strong></p>");
    expect(result).toMatch(/<p>/);
    expect(result).toMatch(/<strong>/);
  });

  it("preserves safe href values", () => {
    const result = sanitizeHtml('<a href="https://example.com">link</a>');
    expect(result).toMatch(/href="https:\/\/example\.com"/);
  });
});
