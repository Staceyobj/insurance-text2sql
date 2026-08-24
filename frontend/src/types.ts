// Verbatim mirror of the backend QueryResponse contract
// (SPEC-FRONTEND §4.2 / SPEC.md §7.2). Do not "improve" this shape.

export type Row = Record<string, string | number | boolean | null>

export type QueryResponse = {
  action: 'sql' | 'clarify' | 'refuse' | null
  answer: string | null
  sql: string | null
  rows: Row[] | null
  truncated: boolean
  error: string | null
  trace: Array<Record<string, unknown>>
}
