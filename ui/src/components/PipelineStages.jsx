const STAGES = [
  { key: 'planner',    label: 'Planner',    desc: 'Decomposes goals' },
  { key: 'executor',   label: 'Executor',   desc: 'Runs subtasks' },
  { key: 'critic',     label: 'Critic',     desc: 'Scores results' },
  { key: 'summarizer', label: 'Summarizer', desc: 'Synthesizes answer' },
]

export default function PipelineStages({ stages }) {
  return (
    <div className="pipeline-section">
      <div className="section-label">// pipeline stages</div>
      <div className="pipeline-stages">
        {STAGES.map(({ key, label, desc }) => {
          const data   = stages?.[key]
          const status = data?.status ?? 'pending'
          const count  = data?.count  ?? 0
          return (
            <div key={key} className={`stage-card ${status}`}>
              <div className="stage-name">{label}</div>
              <div className="stage-count">{count}</div>
              <div className="stage-status">{status}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 4 }}>
                {desc}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
