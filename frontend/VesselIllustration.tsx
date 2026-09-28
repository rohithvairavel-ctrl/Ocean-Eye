function category(value: string | null | undefined) {
  const type = (value || '').toLowerCase();
  const code = Number(type);
  if (type.includes('tanker') || (code >= 80 && code < 90)) return 'tanker';
  if (type.includes('container') || type.includes('cargo') || (code >= 70 && code < 80))
    return 'cargo';
  return 'ship';
}

export default function VesselIllustration({
  type,
  compact = false,
}: {
  type?: string | null;
  compact?: boolean;
}) {
  const kind = category(type);
  return (
    <figure className={`vessel-illustration${compact ? ' compact' : ''}`}>
      <svg viewBox="0 0 640 240" role="img" aria-label={`Generic ${kind} illustration`}>
        <defs>
          <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#172326" />
            <stop offset="1" stopColor="#0b0f10" />
          </linearGradient>
          <linearGradient id="hull" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#526267" />
            <stop offset="1" stopColor="#263337" />
          </linearGradient>
        </defs>
        <rect width="640" height="240" fill="url(#sea)" />
        <path d="M0 190 Q80 180 160 190 T320 190 T480 190 T640 190 V240 H0Z" fill="#102023" />
        <path
          d="M74 155 H540 L596 176 L559 199 H129 L88 184Z"
          fill="url(#hull)"
          stroke="#789096"
          strokeWidth="2"
        />
        <path d="M84 181 H578" stroke="#48c9b0" strokeWidth="3" opacity=".8" />
        {kind === 'tanker' ? (
          <>
            <rect x="175" y="127" width="290" height="27" rx="8" fill="#34464a" />
            {[210, 275, 340, 405].map((x) => (
              <ellipse key={x} cx={x} cy="127" rx="26" ry="7" fill="#71868b" />
            ))}
          </>
        ) : kind === 'cargo' ? (
          <>
            {[170, 222, 274, 326, 378, 430].map((x, i) => (
              <rect
                key={x}
                x={x}
                y={i % 2 ? 111 : 122}
                width="46"
                height={i % 2 ? 42 : 31}
                rx="2"
                fill={i % 3 === 0 ? '#47776e' : '#53676b'}
              />
            ))}
          </>
        ) : (
          <path d="M172 153 L212 126 H424 L462 153Z" fill="#405257" />
        )}
        <path d="M470 153 V99 H535 L553 153Z" fill="#d6dedc" opacity=".9" />
        <rect x="493" y="110" width="26" height="12" fill="#173034" />
        <path d="M509 99 V70 M509 76 L532 88 M509 82 L488 91" stroke="#9fb1b4" strokeWidth="3" />
        <circle cx="509" cy="68" r="4" fill="#48c9b0" />
      </svg>
      <figcaption>Generic {kind} illustration · not a vessel photograph</figcaption>
    </figure>
  );
}
