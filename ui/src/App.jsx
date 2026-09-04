import { useState, useEffect, useRef } from 'react'
import Header from './components/Header.jsx'
import GoalInput from './components/GoalInput.jsx'
import PipelineStages from './components/PipelineStages.jsx'
import MetricsRow from './components/MetricsRow.jsx'
import TaskFeed from './components/TaskFeed.jsx'
import FinalAnswer from './components/FinalAnswer.jsx'
import MemorySearch from './components/MemorySearch.jsx'

export default function App() {
  const [health, setHealth]             = useState({ kafka: false, weaviate: false })
  const [goalId, setGoalId]             = useState(null)
  const [goalDesc, setGoalDesc]         = useState('')
  const [status, setStatus]             = useState(null)
  const [tasks, setTasks]               = useState([])
  const [summary, setSummary]           = useState(null)
  const summaryDoneRef                  = useRef(false)

  // Health polling — always active
  useEffect(() => {
    const poll = () =>
      fetch('/health')
        .then(r => r.json())
        .then(setHealth)
        .catch(() => setHealth({ kafka: false, weaviate: false }))
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  // Pipeline polling — active while goalId is set
  useEffect(() => {
    if (!goalId) return
    summaryDoneRef.current = false
    setSummary(null)
    setStatus(null)
    setTasks([])

    const pollStatus  = () => fetch(`/goals/${goalId}/status`).then(r => r.json()).then(setStatus).catch(() => {})
    const pollTasks   = () => fetch(`/goals/${goalId}/tasks`).then(r => r.json()).then(setTasks).catch(() => {})
    const pollSummary = () => {
      if (summaryDoneRef.current) return
      fetch(`/goals/${goalId}/summary`)
        .then(r => r.json())
        .then(d => {
          if (d.summary) {
            setSummary(d.summary)
            summaryDoneRef.current = true
          }
        })
        .catch(() => {})
    }

    pollStatus(); pollTasks(); pollSummary()
    const id = setInterval(() => {
      pollStatus()
      pollTasks()
      pollSummary()
    }, 3000)
    return () => clearInterval(id)
  }, [goalId])

  const handleGoalSubmit = (id, description) => {
    setGoalId(id)
    setGoalDesc(description)
    setTasks([])
    setSummary(null)
    setStatus(null)
  }

  const approved = tasks.filter(t => t.status === 'approved').length
  const rejected = tasks.filter(t => t.status === 'rejected').length
  const retries  = tasks.reduce((n, t) => n + (t.retry_count || 0), 0)
  const progress = status?.progress ?? 0

  return (
    <div className="app">
      <Header health={health} />

      <GoalInput onSubmit={handleGoalSubmit} disabled={false} />

      {goalId && (
        <div className="goal-status-bar">
          <span className="goal-id-display">
            GOAL <span>#{goalId.slice(0, 8)}</span>
          </span>
          <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>—</span>
          <span className="goal-id-display" style={{ color: 'var(--text-mid)' }}>
            {goalDesc}
          </span>
        </div>
      )}

      <PipelineStages stages={status?.stages} />

      <MetricsRow
        taskCount={status?.task_count ?? 0}
        approved={approved}
        retries={retries}
        progress={progress}
      />

      <div className="main-grid">
        <TaskFeed tasks={tasks} />

        <div className="right-col">
          <FinalAnswer summary={summary} waiting={!!goalId && !summary} />
          <MemorySearch />
        </div>
      </div>
    </div>
  )
}
