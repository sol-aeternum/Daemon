/** Per-turn values, not preferences. Missing/null is the legacy contract. */
export type AnswerDetail = 'brief' | 'balanced' | 'detailed';
export type ResearchEffort = 'auto' | 'research';
export interface AnswerControls {
  readonly answer_detail?: AnswerDetail | null;
  readonly research_effort?: ResearchEffort | null;
}

export const DEFAULT_ANSWER_CONTROLS = Object.freeze({
  answer_detail: 'balanced',
  research_effort: 'auto',
} satisfies AnswerControls);

/** Copy only actual fields; never backfill historical held requests. */
export function copyAnswerControls(value: AnswerControls): AnswerControls {
  return Object.freeze({
    ...(value.answer_detail !== undefined
      ? { answer_detail: value.answer_detail }
      : {}),
    ...(value.research_effort !== undefined
      ? { research_effort: value.research_effort }
      : {}),
  });
}

export function sameAnswerControls(
  a: AnswerControls,
  b: AnswerControls,
): boolean {
  return (
    a.answer_detail === b.answer_detail &&
    a.research_effort === b.research_effort
  );
}

export function newTurnControls(value: AnswerControls): AnswerControls {
  return copyAnswerControls({
    answer_detail: value.answer_detail ?? DEFAULT_ANSWER_CONTROLS.answer_detail,
    research_effort:
      value.research_effort ?? DEFAULT_ANSWER_CONTROLS.research_effort,
  });
}
