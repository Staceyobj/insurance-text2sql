// Single-page query UI (SPEC-FRONTEND §4.1): form → result panel → trace panel.
// All copy is Chinese, matching the answerer's output language.

import { useState, type FormEvent } from 'react'

import { askQuestion } from './api'
import { mapResponse, transportError, type ViewModel } from './mapping'
import type { Row } from './types'

type UiState =
  | { phase: 'idle' }
  | { phase: 'pending' }
  | { phase: 'done'; view: ViewModel; trace: Array<Record<string, unknown>>; showTrace: boolean }

function cellText(value: Row[string]): string {
  // Verbatim rendering — values are never re-parsed (SPEC-FRONTEND §4.2).
  return value === null ? 'NULL' : String(value)
}

function entryText(entry: Record<string, unknown>, key: string): string {
  const value = entry[key]
  return value === undefined || value === null ? '' : String(value)
}

function SqlView({ view }: { view: Extract<ViewModel, { kind: 'sql' }> }) {
  return (
    <section className="panel result">
      <p className="answer">{view.answer}</p>
      <details className="sql">
        <summary>已执行的 SQL（验证后归一化）</summary>
        <pre>
          <code>{view.sql}</code>
        </pre>
      </details>
      {view.truncated && <p className="notice">结果超出单次返回的行数上限，已截断显示。</p>}
      {view.rows.length === 0 ? (
        <p className="muted">查询执行成功，但没有匹配的行。</p>
      ) : (
        <table>
          <thead>
            <tr>
              {view.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((row, index) => (
              <tr key={index}>
                {view.columns.map((column) => (
                  <td key={column}>{cellText(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function ResultPanel({ view }: { view: ViewModel }) {
  switch (view.kind) {
    case 'sql':
      return <SqlView view={view} />
    case 'clarify':
      return (
        <section className="panel result">
          <p className="answer">{view.answer}</p>
          <p className="hint">
            请补充上述信息后，把问题<strong>完整地</strong>重新提交（本服务不保留对话上下文）。
          </p>
        </section>
      )
    case 'refuse':
      return (
        <section className="panel result">
          <p className="answer">{view.answer}</p>
        </section>
      )
    case 'honest-failure':
      return (
        <section className="panel result failure">
          <p className="answer">{view.answer}</p>
          <details className="diag">
            <summary>错误详情（诊断信息）</summary>
            <pre>
              <code>{view.error}</code>
            </pre>
          </details>
        </section>
      )
    case 'unexpected':
      return (
        <section className="panel result failure">
          <p className="answer">收到意外的服务响应（action 与 error 均为空），无法展示结果。请稍后重试。</p>
        </section>
      )
    case 'transport-error':
      return (
        <section className="panel result transport">
          <p className="answer">服务暂时不可达（{view.message}）。</p>
          <p className="hint">请确认后端已启动（make api）后重试。这不是查询失败，请求没有到达问答服务。</p>
        </section>
      )
  }
}

function TracePanel({ trace }: { trace: Array<Record<string, unknown>> }) {
  return (
    <section className="panel trace">
      <h2>调试追踪</h2>
      <ol>
        {trace.map((entry, index) => (
          <li key={index}>
            <code>{entryText(entry, 'node')}</code>
            <span className="duration">{entryText(entry, 'duration_ms')} ms</span>
            <span className="digests">
              in {entryText(entry, 'input_digest')} → out {entryText(entry, 'output_digest')}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

export default function App() {
  const [question, setQuestion] = useState('')
  const [debug, setDebug] = useState(false)
  const [state, setState] = useState<UiState>({ phase: 'idle' })

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setState({ phase: 'pending' })
    const result = await askQuestion(question, debug)
    if (result.ok) {
      setState({ phase: 'done', view: mapResponse(result.data), trace: result.data.trace, showTrace: debug })
    } else {
      setState({ phase: 'done', view: transportError(result.reason), trace: [], showTrace: debug })
    }
  }

  return (
    <main>
      <header>
        <h1>保险数据问答</h1>
        <p className="muted">输入自然语言问题，查询六张保险数据表（只读）。演示用途，数据均为虚构。</p>
      </header>

      <form onSubmit={handleSubmit}>
        <label htmlFor="question">问题</label>
        <textarea
          id="question"
          required
          rows={2}
          value={question}
          placeholder="例如：2024年生效的保单有多少张？"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className="controls">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={debug}
              onChange={(event) => setDebug(event.target.checked)}
            />
            调试模式（返回各步骤追踪）
          </label>
          <button type="submit" disabled={state.phase === 'pending'}>
            {state.phase === 'pending' ? '查询中…' : '查询'}
          </button>
        </div>
      </form>

      {state.phase === 'pending' && <p className="muted status">正在查询，请稍候…</p>}
      {state.phase === 'done' && (
        <>
          <ResultPanel view={state.view} />
          {state.showTrace && state.trace.length > 0 && <TracePanel trace={state.trace} />}
        </>
      )}
    </main>
  )
}
