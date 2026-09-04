import { useState, useEffect, useRef } from 'react'

function useCountUp(target, duration = 600) {
  const [val, setVal] = useState(0)
  const prev = useRef(0)
  useEffect(() => {
    const from = prev.current
    if (from === target) return
    const start = Date.now()
    const ease  = (t) => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t
    const id = setInterval(() => {
      const p = Math.min((Date.now() - start) / duration, 1)
      setVal(Math.round(from + (target - from) * ease(p)))
      if (p >= 1) { prev.current = target; clearInterval(id) }
    }, 16)
    return () => clearInterval(id)
  }, [target, duration])
  return val
}

export default function MetricsRow({ taskCount, approved, retries, progress }) {
  const animTasks    = useCountUp(taskCount)
  const animApproved = useCountUp(approved)
  const animRetries  = useCountUp(retries)
  const animProgress = useCountUp(progress)

  return (
    <div className="metrics-section">
      <div className="metrics-row">
        <div className="metric-card">
          <div className="metric-label">Total Tasks</div>
          <div className="metric-value cyan">{animTasks}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Approved</div>
          <div className="metric-value green">{animApproved}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Retries</div>
          <div className="metric-value amber">{animRetries}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Progress</div>
          <div className="metric-value purple">{animProgress}%</div>
        </div>
      </div>

      <div className="progress-bar-wrap">
        <div
          className="progress-bar-fill"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  )
}
