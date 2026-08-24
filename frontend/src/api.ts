// fetch wrapper for POST /v1/query (SPEC-FRONTEND §3, §4.3).
//
// Transport errors (fetch rejection or non-2xx status) are a distinct surface
// from the pipeline's honest failure (HTTP 200 with error non-null) and must
// never be fabricated into a pipeline answer.

import type { QueryResponse } from './types'

export type ApiResult = { ok: true; data: QueryResponse } | { ok: false; reason: string }

export async function askQuestion(question: string, debug: boolean): Promise<ApiResult> {
  try {
    const response = await fetch('/v1/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, debug }),
    })
    if (!response.ok) {
      return { ok: false, reason: `HTTP ${response.status}` }
    }
    return { ok: true, data: (await response.json()) as QueryResponse }
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : String(err) }
  }
}
