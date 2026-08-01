export default function Breadcrumb({ trail }) {
  // trail: [{ label, onClick? }] — last item renders as current (non-clickable)
  return (
    <div className="breadcrumb-bar">
      {trail.map((item, i) => {
        const isLast = i === trail.length - 1
        return (
          <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isLast ? (
              <span className="current">{item.label}</span>
            ) : (
              <a onClick={item.onClick} style={{ cursor: 'pointer' }}>{item.label}</a>
            )}
            {!isLast && <span className="sep">/</span>}
          </span>
        )
      })}
    </div>
  )
}
