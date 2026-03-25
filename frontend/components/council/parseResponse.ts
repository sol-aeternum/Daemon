/**
 * Council response parsers for Round 1 and Round 2 formats
 * Extracts structured data from model responses using regex with graceful fallback
 */

export interface ParsedResponse {
  position: string;
  keyArguments: string;
  assumptions: string;
  blindSpot: string;
  confidence: number;
  missingInformation: string;
  raw: string;
  parsed: boolean;
}

export interface Round2ParsedResponse {
  weakestAssumption: string;
  strongestPoint: string;
  revisedPosition: string;
  revisedConfidence: number;
  raw: string;
  parsed: boolean;
}

/**
 * Extract confidence value from text
 * Handles formats: "7", "7/10", "7 out of 10", "7 — I'm confident"
 */
function extractConfidence(text: string): number {
  if (!text) return 0;
  
  // Look for leading number
  const match = text.match(/^\s*(\d+)/);
  if (match) {
    const num = parseInt(match[1], 10);
    // Clamp to reasonable range (0-10)
    return Math.max(0, Math.min(10, num));
  }
  
  return 0;
}

/**
 * Parse Round 1 council response format
 * Expected sections: **Position**, **Key Arguments**, **Assumptions**, **Blind Spot**, **Confidence**, **Missing Information**
 */
export function parseRound1Response(response: string): ParsedResponse {
  const result: ParsedResponse = {
    position: '',
    keyArguments: '',
    assumptions: '',
    blindSpot: '',
    confidence: 0,
    missingInformation: '',
    raw: response,
    parsed: false
  };

  if (!response) return result;

  // Define section patterns
  const sections: Record<string, keyof ParsedResponse> = {
    '\\*\\*Position\\*\\*': 'position',
    '\\*\\*Key Arguments\\*\\*': 'keyArguments',
    '\\*\\*Assumptions\\*\\*': 'assumptions',
    '\\*\\*Blind Spot\\*\\*': 'blindSpot',
    '\\*\\*Confidence\\*\\*': 'confidence',
    '\\*\\*Missing Information\\*\\*': 'missingInformation'
  };

  let foundCount = 0;

  // Extract each section
  for (const [pattern, field] of Object.entries(sections)) {
    const regex = new RegExp(`${pattern}\\s*:?\\s*\\n?([^*]*)`, 'i');
    const match = response.match(regex);
    
    if (match && match[1]) {
      const value = match[1].trim();
      if (field === 'confidence') {
        result.confidence = extractConfidence(value);
      } else {
        (result as unknown as Record<string, string>)[field] = value;
      }
      foundCount++;
    }
  }

  // Only mark as parsed if we found at least 2 sections
  result.parsed = foundCount >= 2;
  return result;
}

/**
 * Parse Round 2 council response format
 * Expected sections: **Weakest Assumption**, **Strongest Point**, **Revised Position**, **Revised Confidence**
 * Plus per-agent critiques (which we ignore for now)
 */
export function parseRound2Response(response: string): Round2ParsedResponse {
  const result: Round2ParsedResponse = {
    weakestAssumption: '',
    strongestPoint: '',
    revisedPosition: '',
    revisedConfidence: 0,
    raw: response,
    parsed: false
  };

  if (!response) return result;

  // Define section patterns for Round 2
  const sections: Record<string, keyof Round2ParsedResponse> = {
    '\\*\\*Weakest Assumption\\*\\*': 'weakestAssumption',
    '\\*\\*Strongest Point\\*\\*': 'strongestPoint',
    '\\*\\*Revised Position\\*\\*': 'revisedPosition',
    '\\*\\*Revised Confidence\\*\\*': 'revisedConfidence'
  };

  let foundCount = 0;

  // Extract each section
  for (const [pattern, field] of Object.entries(sections)) {
    const regex = new RegExp(`${pattern}\\s*:?\\s*\\n?([^*]*)`, 'i');
    const match = response.match(regex);
    
    if (match && match[1]) {
      const value = match[1].trim();
      if (field === 'revisedConfidence') {
        result.revisedConfidence = extractConfidence(value);
      } else {
        (result as unknown as Record<string, string>)[field] = value;
      }
      foundCount++;
    }
  }

  // Only mark as parsed if we found at least 2 sections
  result.parsed = foundCount >= 2;
  return result;
}