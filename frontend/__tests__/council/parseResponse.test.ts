import { describe, it, expect } from "vitest";
import { parseRound1Response, parseRound2Response } from "../../components/council/parseResponse";

describe("parseRound1Response", () => {
  it("should parse well-formed Round 1 response", () => {
    const input = `**Position**: We should invest in renewable energy
**Key Arguments**: 
1. Climate change is a real threat
2. Solar costs have dropped significantly
3. Government incentives are available
**Assumptions**: Technology will continue to improve
**Blind Spot**: We may be underestimating nuclear power
**Confidence**: 7/10
**Missing Information**: Long-term storage solutions`;

    const result = parseRound1Response(input);

    expect(result.parsed).toBe(true);
    expect(result.position).toBe("We should invest in renewable energy");
    expect(result.confidence).toBe(7);
    expect(result.assumptions).toBe("Technology will continue to improve");
    expect(result.blindSpot).toBe("We may be underestimating nuclear power");
  });

  it("should handle truncated response with partial sections", () => {
    const input = `**Position**: Testing is important
**Key Arguments**: 
1. Catches bugs early
2. Improves code quality`;

    const result = parseRound1Response(input);

    expect(result.parsed).toBe(true);
    expect(result.position).toBe("Testing is important");
    expect(result.keyArguments).toContain("Catches bugs early");
  });

  it("should return parsed: false for unformatted response", () => {
    const input = "This is just a plain text response without any section markers.";

    const result = parseRound1Response(input);

    expect(result.parsed).toBe(false);
    expect(result.raw).toBe(input);
    expect(result.position).toBe("");
  });

  it("should handle empty string", () => {
    const result = parseRound1Response("");

    expect(result.parsed).toBe(false);
    expect(result.raw).toBe("");
    expect(result.position).toBe("");
  });

  it("should handle confidence variations", () => {
    const cases = [
      { input: "**Confidence**: 7", expected: 7 },
      { input: "**Confidence**: 7/10", expected: 7 },
      { input: "**Confidence**: 7 out of 10", expected: 7 },
      { input: "**Confidence**: 7 — I'm confident", expected: 7 },
      { input: "**Confidence**: 10", expected: 10 },
      { input: "**Confidence**: 0", expected: 0 },
      { input: "**Confidence**: 15", expected: 10 },
    ];

    cases.forEach(({ input, expected }) => {
      const result = parseRound1Response(input);
      expect(result.confidence).toBe(expected);
    });
  });

  it("should handle heavy markdown in sections", () => {
    const input = `**Position**: We recommend bold and italic solutions
**Key Arguments**: 
- Item with code
- Link text
- Blockquote text
**Assumptions**: Bold assumptions work`;

    const result = parseRound1Response(input);

    expect(result.parsed).toBe(true);
    expect(result.position).toContain("bold");
    expect(result.keyArguments).toContain("code");
  });
});

describe("parseRound2Response", () => {
  it("should parse well-formed Round 2 response", () => {
    const input = `**Weakest Assumption**: The market will grow at 5% annually
**Strongest Point**: Current technology is proven and reliable
**Revised Position**: We should invest but with more caution
**Revised Confidence**: 6`;

    const result = parseRound2Response(input);

    expect(result.parsed).toBe(true);
    expect(result.weakestAssumption).toBe("The market will grow at 5% annually");
    expect(result.strongestPoint).toBe("Current technology is proven and reliable");
    expect(result.revisedPosition).toBe("We should invest but with more caution");
    expect(result.revisedConfidence).toBe(6);
  });

  it("should handle truncated Round 2 response", () => {
    const input = `**Weakest Assumption**: Initial cost estimates are wrong
**Revised Position**: Delay the project`;

    const result = parseRound2Response(input);

    expect(result.parsed).toBe(true);
    expect(result.weakestAssumption).toBe("Initial cost estimates are wrong");
    expect(result.revisedPosition).toBe("Delay the project");
  });

  it("should return parsed: false for unformatted Round 2 response", () => {
    const input = "This is a Round 2 response without proper formatting.";

    const result = parseRound2Response(input);

    expect(result.parsed).toBe(false);
    expect(result.raw).toBe(input);
  });
});