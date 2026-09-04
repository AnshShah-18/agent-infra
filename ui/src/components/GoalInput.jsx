import { useState } from 'react'

export default function GoalInput({ onSubmit, disabled }) {
  const [value, setValue]   = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState(null)

  const handleSubmit = async () => {
    const desc = value.trim()
    if (!desc || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/goals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      onSubmit(data.goal_id, desc)
      setValue('')
    } catch (e) {
      setError('Failed to dispatch goal. Is the API running?')
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="goal-input-section">
      <div className="goal-input-label">// dispatch goal</div>
      <div className="goal-input-bar">
        <div className="goal-input-wrap">
          <input
            className="goal-input"
            type="text"
            placeholder="Describe a goal for the agent pipeline…"
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKey}
            disabled={disabled || loading}
          />
        </div>
        <button
          className={`dispatch-btn${loading ? ' loading' : ''}`}
          onClick={handleSubmit}
          disabled={!value.trim() || loading}
        >
          {loading ? 'DISPATCHING' : 'DISPATCH'}
        </button>
      </div>
      {error && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--red)', marginTop: 6 }}>
          {error}
        </div>
      )}
    </div>
  )
}
