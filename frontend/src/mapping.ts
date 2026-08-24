// Pure response → view-model mapping (SPEC-FRONTEND §4.2).
//
// Dispatch precedence is MANDATORY: `error != null` is checked BEFORE `action`.
// The honest-failure node returns answer + trace only — it neither clears
// `action` nor `error_feedback` — so a failed query typically arrives as
// action='sql' with error non-null and rows=null; dispatching on action alone
// would render an empty table for it. Conversely every success path clears
// error_feedback, so at a terminal state error != null is exact.

import type { QueryResponse, Row } from './types'

export type ViewModel =
  | { kind: 'sql'; answer: string; sql: string; columns: string[]; rows: Row[]; truncated: boolean }
  | { kind: 'clarify'; answer: string }
  | { kind: 'refuse'; answer: string }
  | { kind: 'honest-failure'; answer: string; error: string }
  | { kind: 'unexpected' }
  | { kind: 'transport-error'; message: string }

export function mapResponse(data: QueryResponse): ViewModel {
  if (data.error !== null) {
    // `answer` alone is display-complete: the backend's honest-failure composer
    // embeds the reason in its user-facing sentence; `error` is diagnostic
    // detail (parser error / rule ID / first-line DB error), never main copy.
    return { kind: 'honest-failure', answer: data.answer ?? '', error: data.error }
  }
  switch (data.action) {
    case 'sql':
      return {
        kind: 'sql',
        answer: data.answer ?? '',
        sql: data.sql ?? '',
        columns: data.rows !== null && data.rows.length > 0 ? Object.keys(data.rows[0]) : [],
        rows: data.rows ?? [],
        truncated: data.truncated,
      }
    case 'clarify':
      return { kind: 'clarify', answer: data.answer ?? '' }
    case 'refuse':
      return { kind: 'refuse', answer: data.answer ?? '' }
    default:
      // Outside the §4.2 truth table (action=null AND error=null should be
      // impossible): surface it instead of fabricating a result.
      return { kind: 'unexpected' }
  }
}

export function transportError(message: string): ViewModel {
  return { kind: 'transport-error', message }
}
