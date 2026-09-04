import { useState } from 'react'

export default function MemorySearch() {
  const [query,    setQuery]   = useState('')
  const [agentFilter, setAgentFilter] = useState('')
  const [results,  setResults] = useState([])
  const [loading,  setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const handleSearch = async () => {
    if (!query.trim() || loading) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ q: query.trim(), limit: 5 })
      if (agentFilter) params.set('agent_id', agentFilter)
      const res = await fetch(`/memory/search?${params}`)
      const data = await res.json()
      setResults(data)
      setSearched(true)
    } catch {
      setResults([])
      setSearched(true)
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') handleSearch()
  }

  return (
    <div className="memory-search">
      <div className="memory-search__header">
        <span className="memory-search__title">Memory Search</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          Weaviate semantic
        </span>
      </div>

      <div className="memory-search__body">
        <div className="search-bar">
          <input
            className="search-input"
            type="text"
            placeholder="Search episodic memory…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKey}
          />
          <select
            value={agentFilter}
            onChange={e => setAgentFilter(e.target.value)}
            style={{
              background: 'var(--bg-input)',
              border: '1px solid var(--border-bright)',
              borderRadius: 'var(--radius)',
              color: 'var(--text-mid)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              padding: '8px 10px',
              outline: 'none',
              cursor: 'pointer',
            }}
          >
            <option value="">All agents</option>
            <option value="planner">planner</option>
            <option value="executor">executor</option>
            <option value="critic">critic</option>
            <option value="summarizer">summarizer</option>
          </select>
          <button
            className="search-btn"
            onClick={handleSearch}
            disabled={!query.trim() || loading}
          >
            {loading ? '…' : 'SEARCH'}
          </button>
        </div>

        <div className="search-results">
          {searched && results.length === 0 && (
            <div className="search-empty">No results found</div>
          )}
          {results.map((r, i) => (
            <div key={i} className="search-result">
              <div className="search-result__meta">
                <span className={`agent-tag ${r.agent_id}`}>{r.agent_id}</span>
                <span className="distance-tag">
                  dist {(r.distance ?? 0).toFixed(3)}
                </span>
              </div>
              <div className="search-result__content">{r.content}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
