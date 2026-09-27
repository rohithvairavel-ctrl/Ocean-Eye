import { useState } from 'react';
import { RotateCcw, Ship, TriangleAlert } from 'lucide-react';
import { Json, coordinate, post, shortTime, time } from './api';
import MaritimeMap from './MaritimeMap';
import { Bar, Metric, PageHeader, Section, StateBlock, Tag } from './ui';

function Spark({ track, field, unit }: { track: Json[]; field: 'sog' | 'cog'; unit: string }) {
  const start = Date.parse(track[0].time),
    span = Math.max(Date.parse(track[track.length - 1].time) - start, 1);
  const values = track.map((p) => p[field]).filter((v) => v !== null && v !== undefined);
  const max = Math.max(...values, 1);
  const x = (p: Json) => ((Date.parse(p.time) - start) / span) * 300;
  const y = (p: Json) => 46 - (p[field] / max) * 40;
  return (
    <figure className="spark">
      <svg viewBox="0 0 300 50" role="img" aria-label={field === 'sog' ? 'Observed speed over ground' : 'Observed course over ground'}>
        <line x1="0" y1="46" x2="300" y2="46" className="spark-axis" />
        {track.map((p, i) => {
          if (p[field] == null) return null;
          const prev = track[i - 1];
          return (
            <g key={i}>
              {prev?.[field] != null && Date.parse(p.time) - Date.parse(prev.time) <= 1800000 && (
                <line x1={x(prev)} y1={y(prev)} x2={x(p)} y2={y(p)} className="spark-line" />
              )}
            </g>
          );
        })}
      </svg>
      <figcaption>
        {field === 'sog' ? 'Speed' : 'Course'} · 0–{max.toFixed(0)} {unit} · gaps over 30 min not joined
      </figcaption>
    </figure>
  );
}

function TrackTimeline({ v, a }: { v: Json; a: Json }) {
  const t0 = Date.parse(v.track[0].time),
    t1 = Date.parse(v.track[v.track.length - 1].time);
  const span = Math.max(t1 - t0, 1);
  const pos = (iso: string) => Math.max(0, Math.min(100, ((Date.parse(iso) - t0) / span) * 100));
  const [r0, r1] = a.origin.release_window;
  return (
    <div className="track-timeline" aria-label="AIS reporting timeline">
      <div className="tt-bar">
        <span className="tt-release" style={{ left: pos(r0) + '%', width: Math.max(pos(r1) - pos(r0), 0.6) + '%' }} title="Assumed release window" />
        {v.gaps.map((g: Json, i: number) => (
          <span key={i} className="tt-gap" style={{ left: pos(g.start) + '%', width: Math.max(pos(g.end) - pos(g.start), 0.6) + '%' }} title={`AIS gap ${Math.round(g.minutes)} min`} />
        ))}
        <span className="tt-obs" style={{ left: pos(a.observation_time) + '%' }} title="SAR observation" />
        <span className="tt-near" style={{ left: pos(v.nearest_time) + '%' }} title="Closest approach to modeled origin" />
      </div>
      <div className="tt-scale">
        <span>{shortTime(v.track[0].time)}</span>
        <span>{shortTime(v.track[v.track.length - 1].time)}</span>
      </div>
      <div className="tt-legend">
        <span><i className="tt-release" />Assumed release window</span>
        <span><i className="tt-gap" />AIS gap</span>
        <span><i className="tt-near" />Closest approach</span>
        <span><i className="tt-obs" />SAR observation</span>
      </div>
    </div>
  );
}

export default function Vessels({
  a,
  geography,
  selected,
  setSelected,
  truth,
  navigate,
}: {
  a: Json;
  geography: Json | null;
  selected: string | null;
  setSelected: (m: string) => void;
  truth: Json | null;
  navigate: (p: string) => void;
}) {
  const [counter, setCounter] = useState<Json | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const vessels: Json[] = a.vessels;
  if (!vessels.length)
    return (
      <>
        <PageHeader eyebrow="Investigate" title="Vessel intelligence" question="Which vessels intersect the evidence?" />
        <StateBlock kind="empty" title="No AIS-tracked vessels in this run">
          AIS data is required before vessel attribution can run. Import an AIS archive that overlaps the release window.
        </StateBlock>
      </>
    );
  const v = vessels.find((x) => x.mmsi === selected) || vessels[0];
  const rank = vessels.findIndex((x) => x.mmsi === v.mmsi) + 1;
  const factor = (x: Json, name: string) => x.components.find((c: Json) => c.name === name)?.value ?? 0;
  const keyAnomaly = (x: Json) => {
    const top = x.components.slice().sort((p: Json, q: Json) => q.contribution - p.contribution)[0];
    return top ? `${top.name} (${top.contribution.toFixed(1)})` : '—';
  };
  const challengeRows = truth?.challenged
    ? truth.challenges.filter((c: Json) => c.available && c.after_ranking)
    : [];

  const exclude = async () => {
    setBusy(true);
    setError('');
    try {
      setCounter(await post(`/cases/${a.case_id}/counterfactual`, { exclude_mmsi: v.mmsi }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Investigate"
        title="Vessel intelligence"
        question="Which vessels intersect the evidence — and how strongly?"
      >
        <p className="notice">
          <TriangleAlert size={14} aria-hidden /> The investigative relevance score ranks screening evidence. It is <b>not</b> a probability of
          culpability, and an AIS gap is not evidence of deliberate concealment.
        </p>
      </PageHeader>

      <div className="master-detail">
        <div className="master">
          <div className="table-wrap tall">
            <table className="selectable">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Vessel</th>
                  <th>Relevance</th>
                  <th>Closest</th>
                  <th>Overlap</th>
                  <th>AIS gaps</th>
                  <th>Key factor</th>
                </tr>
              </thead>
              <tbody>
                {vessels.map((x, i) => (
                  <tr
                    key={x.mmsi}
                    className={x.mmsi === v.mmsi ? 'on' : ''}
                    tabIndex={0}
                    aria-selected={x.mmsi === v.mmsi}
                    onClick={() => {
                      setSelected(x.mmsi);
                      setCounter(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelected(x.mmsi);
                        setCounter(null);
                      }
                    }}
                  >
                    <td className="mono">{i + 1}</td>
                    <td>
                      <strong>{x.name}</strong>
                      <span className="cell-note mono">{x.mmsi}</span>
                    </td>
                    <td className="score-cell">
                      <span className="mono">{x.score.toFixed(1)}</span>
                      <Bar value={x.score} tone={i === 0 ? 'active' : 'neutral'} />
                    </td>
                    <td className="mono">{x.nearest_km} km</td>
                    <td className="mono">{factor(x, 'Trajectory overlap').toFixed(0)}%</td>
                    <td className="mono">{x.gaps.length ? `${x.gaps.length} · ${Math.round(x.gaps.reduce((s: number, g: Json) => s + g.minutes, 0))}m` : '—'}</td>
                    <td className="small">{keyAnomaly(x)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <MaritimeMap
            analysis={a}
            geography={geography}
            selected={v.mmsi}
            onSelect={setSelected}
            truth={truth}
            layers={{ gaps: true, forecast: false, ais: true, aoi: false }}
            height={380}
            timeline={false}
            label="Selected route segment"
          />
        </div>

        <article className="dossier" aria-label={`Vessel dossier: ${v.name}`}>
          <div className="dossier-head">
            <div className="dossier-icon"><Ship size={20} aria-hidden /></div>
            <div>
              <span className="eyebrow">Vessel dossier · rank {rank} of {vessels.length}</span>
              <h2>{v.name}</h2>
              <span className="muted mono">
                MMSI {v.mmsi} · IMO {v.imo || 'unavailable'} · type {v.vessel_type || 'unavailable'}
              </span>
            </div>
          </div>
          <div className="dossier-metrics">
            <Metric label="Relevance score" value={v.score.toFixed(1)} unit="/100" hint="screening, not culpability" tone="active" />
            <Metric label="Closest approach" value={v.nearest_km} unit="km" hint={shortTime(v.nearest_time)} />
            <Metric label="Minimum speed" value={v.minimum_speed_kn} unit="kn" hint={`baseline ${v.baseline_speed_kn} kn`} />
            <Metric label="Course change" value={v.course_change_deg} unit="°" />
          </div>

          <h3>Reporting timeline</h3>
          <TrackTimeline v={v} a={a} />

          <h3>Behaviour</h3>
          <div className="sparks">
            <Spark track={v.track} field="sog" unit="kn" />
            <Spark track={v.track} field="cog" unit="°" />
          </div>

          <div className="evidence-lists two">
            <div>
              <h3 className="tone-text-ok">Supporting</h3>
              {v.supporting.length ? <ul>{v.supporting.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul> : <p className="muted">None recorded.</p>}
            </div>
            <div>
              <h3 className="tone-text-hazard">Contradicting</h3>
              {v.contradicting.length ? <ul>{v.contradicting.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul> : <p className="muted">None recorded.</p>}
            </div>
          </div>

          <h3>Score factors</h3>
          <div className="factors">
            {v.components.map((c: Json) => (
              <div key={c.name} className="factor-row">
                <span>{c.name}</span>
                <Bar value={c.value} tone="neutral" />
                <span className="mono small">{(c.weight * 100).toFixed(0)}% wt</span>
                <b className="mono">{c.contribution.toFixed(1)}</b>
              </div>
            ))}
          </div>

          <h3>TruthLoop behaviour</h3>
          {!truth?.challenged ? (
            <StateBlock kind="waiting" compact title="Conclusion not challenged yet" action={<button className="link" onClick={() => navigate('TruthLoop')}>Open TruthLoop</button>}>
              Challenge results show whether this vessel holds its rank when evidence is removed.
            </StateBlock>
          ) : (
            <ul className="truth-behaviour">
              {challengeRows.map((c: Json) => {
                const pos = c.after_ranking.findIndex((r: Json) => r.mmsi === v.mmsi);
                return (
                  <li key={c.id}>
                    <span>{c.label}</span>
                    <span className="mono">{pos >= 0 ? `#${pos + 1}` : c.id === 'exclude_strongest_candidate' && rank === 1 ? 'excluded' : '> #5'}</span>
                  </li>
                );
              })}
            </ul>
          )}

          <h3>Counterfactual</h3>
          <p className="fine">Exclude this vessel and re-rank the remaining candidates. Original evidence is preserved.</p>
          <button className="btn btn-quiet btn-sm" onClick={exclude} disabled={busy}>
            <RotateCcw size={13} /> {busy ? 'Re-ranking…' : 'Exclude & re-rank'}
          </button>
          {error && <StateBlock kind="error" compact title="Re-rank failed">{error}</StateBlock>}
          {counter && (
            <ol className="counter-list enter">
              {counter.ranking.slice(0, 3).map((r: Json) => (
                <li key={r.mmsi}>
                  {r.name} <span className="mono">{r.score.toFixed(1)}</span>
                </li>
              ))}
            </ol>
          )}
          <a className="link small" href={`/api/v1/cases/${a.case_id}/vessels/${v.mmsi}/dossier`} target="_blank" rel="noreferrer">
            Export dossier JSON
          </a>
        </article>
      </div>

      <Section
        title="SAR–AIS anomaly screening"
        note={a.dark.limitations}
      >
        <div className="facts">
          <Metric label="Bright SAR returns" value={a.dark.sar_returns.length} />
          <Metric label="Unmatched returns" value={a.dark.unmatched_returns} hint="not confirmed dark vessels" tone="warn" />
          <Metric label="AIS reporting gaps" value={a.dark.gap_count} hint="may reflect receiver coverage" />
          <Metric label="Assessment" value="Unresolved" />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>SAR return</th>
                <th>Position</th>
                <th>Nearest AIS vessel</th>
                <th>Distance</th>
                <th>Time offset</th>
                <th>Screening result</th>
              </tr>
            </thead>
            <tbody>
              {a.dark.sar_returns.map((r: Json) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td className="mono small">{coordinate(r.coordinates)}</td>
                  <td className="mono">{r.nearest_mmsi}</td>
                  <td className="mono">{r.distance_km} km</td>
                  <td className="mono">{r.ais_time_offset_min} min</td>
                  <td>{r.matched ? <Tag tone="ok">AIS match within tolerance</Tag> : <Tag tone="warn">Unmatched · screening only</Tag>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="fine">Observation time {time(a.observation_time)}. Match tolerance: 2 km and 15 min.</p>
      </Section>
    </>
  );
}
