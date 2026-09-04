import { useMemo } from 'react'
import { marked } from 'marked'

marked.setOptions({ breaks: true, gfm: true })

export default function FinalAnswer({ summary, waiting }) {
  const html = useMemo(() => summary ? marked(summary) : '', [summary])

  return (
    <div className={`final-answer ${summary ? 'complete' : ''}`}>
      <div className="final-answer__header">
        <span className="final-answer__title">Final Answer</span>
        {summary && <span className="complete-badge">complete</span>}
      </div>

      <div className="final-answer__body">
        {summary ? (
          <div
            className="markdown-content"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        ) : (
          <div className="final-answer__placeholder">
            <div className={`placeholder-ring ${waiting ? '' : 'idle'}`} />
            <div className="placeholder-text">
              {waiting ? 'Waiting for summary…' : 'Submit a goal to begin'}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
