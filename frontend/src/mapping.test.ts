// Table-driven tests for the pure mapping function (SPEC-FRONTEND §8).
// Covers the five §2.1 states plus the precedence regression: an honest
// failure arriving with residual action='sql'.

import { describe, expect, it } from 'vitest'

import { mapResponse, transportError } from './mapping'
import type { QueryResponse } from './types'

const base: QueryResponse = {
  action: null,
  answer: null,
  sql: null,
  rows: null,
  truncated: false,
  error: null,
  trace: [],
}

describe('mapResponse', () => {
  it('sql success → sql view with columns from the first row', () => {
    const view = mapResponse({
      ...base,
      action: 'sql',
      answer: '2024 年生效的保单共 1234 张。',
      sql: 'SELECT count(*) FROM policies WHERE ...',
      rows: [
        { count: 1234 },
        { count: 5678 },
      ],
      truncated: false,
    })
    expect(view).toEqual({
      kind: 'sql',
      answer: '2024 年生效的保单共 1234 张。',
      sql: 'SELECT count(*) FROM policies WHERE ...',
      columns: ['count'],
      rows: [{ count: 1234 }, { count: 5678 }],
      truncated: false,
    })
  })

  it('sql success with truncated=true carries the flag through', () => {
    const view = mapResponse({ ...base, action: 'sql', rows: [{ n: 1 }], truncated: true })
    expect(view.kind).toBe('sql')
    expect(view.kind === 'sql' && view.truncated).toBe(true)
  })

  it('sql success with rows=[] (vs null) → sql view, empty columns', () => {
    const view = mapResponse({ ...base, action: 'sql', rows: [], answer: '没有匹配的行。' })
    expect(view).toEqual({
      kind: 'sql',
      answer: '没有匹配的行。',
      sql: '',
      columns: [],
      rows: [],
      truncated: false,
    })
  })

  it('clarify → clarify view (rows null)', () => {
    const view = mapResponse({ ...base, action: 'clarify', answer: '请问您想查询哪一年？' })
    expect(view).toEqual({ kind: 'clarify', answer: '请问您想查询哪一年？' })
  })

  it('refuse → refuse view', () => {
    const view = mapResponse({ ...base, action: 'refuse', answer: '抱歉，仅支持只读查询。' })
    expect(view).toEqual({ kind: 'refuse', answer: '抱歉，仅支持只读查询。' })
  })

  it('REGRESSION: honest failure with residual action="sql" must not render a table', () => {
    const view = mapResponse({
      ...base,
      action: 'sql',
      answer: '抱歉，本次查询未能完成：违反规则 R2。',
      error: 'validator: R2 non-SELECT statement',
    })
    expect(view).toEqual({
      kind: 'honest-failure',
      answer: '抱歉，本次查询未能完成：违反规则 R2。',
      error: 'validator: R2 non-SELECT statement',
    })
  })

  it('honest failure with action=null (router-parse path) → honest-failure', () => {
    const view = mapResponse({
      ...base,
      answer: '抱歉，本次查询未能完成：router parse failure。',
      error: 'router parse failure: ...',
    })
    expect(view.kind).toBe('honest-failure')
  })

  it('honest failure with answer=null falls back to empty answer (defensive)', () => {
    const view = mapResponse({ ...base, error: 'boom' })
    expect(view).toEqual({ kind: 'honest-failure', answer: '', error: 'boom' })
  })

  it('outside the truth table (action=null AND error=null) → unexpected', () => {
    expect(mapResponse({ ...base })).toEqual({ kind: 'unexpected' })
  })
})

describe('transportError', () => {
  it('produces the transport-error view with the reason', () => {
    expect(transportError('HTTP 502')).toEqual({ kind: 'transport-error', message: 'HTTP 502' })
    expect(transportError('Failed to fetch').kind).toBe('transport-error')
  })
})
