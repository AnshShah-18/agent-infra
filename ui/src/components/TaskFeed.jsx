function scoreBadgeClass(score) {
  if (score === null || score === undefined) return ''
  if (score >= 7) return 'high'
  if (score >= 4) return 'mid'
  return 'low'
}

function StatusBadge({ status, retryCount }) {
  const label = retryCount > 0 && status !== 'approved' ? 'retrying' : status
  return <span className={`status-badge ${label}`}>{label}</span>
}

export default function TaskFeed({ tasks }) {
  return (
    <div className="task-feed">
      <div className="task-feed__header">
        <span className="task-feed__title">Task Feed</span>
        <span className="task-count-badge">{tasks.length} tasks</span>
      </div>

      <div className="task-feed__scroll">
        {tasks.length === 0 ? (
          <div className="feed-empty">
            <div className="feed-empty__icon">◈</div>
            <div className="feed-empty__text">Awaiting tasks</div>
          </div>
        ) : (
          [...tasks].reverse().map(task => (
            <div key={task.id} className={`task-card ${task.status}`}>
              <div className="task-card__top">
                <span className="task-id">#{task.id}</span>
                <div className="task-badges">
                  {task.score !== null && task.score !== undefined && (
                    <span className={`score-badge ${scoreBadgeClass(task.score)}`}>
                      {task.score}/10
                    </span>
                  )}
                  <StatusBadge status={task.status} retryCount={task.retry_count} />
                </div>
              </div>

              <div className="task-desc">{task.description}</div>

              {task.retry_count > 0 && (
                <div className="task-retry">↻ retried {task.retry_count}×</div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
