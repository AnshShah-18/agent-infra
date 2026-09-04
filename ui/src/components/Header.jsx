import { useState, useEffect } from 'react'

export default function Header({ health }) {
  const [time, setTime] = useState('')

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setTime(now.toUTCString().split(' ')[4])
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <header className="header">
      <div className="header__brand">
        <span className="header__title">AGENT INFRA</span>
        <span className="header__sub">Control Room v3</span>
      </div>

      <div className="header__center">
        <div className="health-indicators">
          <div className="health-item">
            <div className={`health-dot ${health.kafka ? 'ok' : 'err'}`} />
            Kafka
          </div>
          <div className="health-item">
            <div className={`health-dot ${health.weaviate ? 'ok' : 'err'}`} />
            Weaviate
          </div>
        </div>
      </div>

      <div className="header__clock">
        UTC <span>{time}</span>
      </div>
    </header>
  )
}
