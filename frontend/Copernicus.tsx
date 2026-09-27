import { useEffect, useState } from 'react';
import { Check, Pause, Play, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { Json, api, del, label, patch, post, shortTime, time, sentence } from './api';
import MaritimeMap from './MaritimeMap';
import { Age, Bar, Countdown, PageHeader, Section, StateBlock, Status, Tag } from './ui';

function WatchAreaForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    const f = new FormData(e.currentTarget);
    try {
      await post('/copernicus/watch-areas', {
        name: f.get('name'),
        center: [Number(f.get('lon')), Number(f.get('lat'))],
        radius_km: Number(f.get('radius')),
        interval_minutes: Number(f.get('interval')),
        auto_analyze: f.get('auto_analyze') === 'on',
      });
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="form-grid-4">
      <label className="span-2">
        Name
        <input name="name" required maxLength={160} placeholder="Gulf approach" />
      </label>
      <label>
        Centre longitude
        <input name="lon" type="number" step="any" min="-180" max="180" required />
      </label>
      <label>
        Centre latitude
        <input name="lat" type="number" step="any" min="-90" max="90" required />
      </label>
      <label>
        Radius (km)
        <input name="radius" type="number" min="0.1" max="500" step="0.1" defaultValue="15" required />
      </label>
      <label>
        Catalogue check every (min)
        <input name="interval" type="number" min="5" max="1440" defaultValue="15" required />
      </label>
      <label className="check span-2">
        <input type="checkbox" name="auto_analyze" />
        Auto-analyze new scenes (needs calibrated imagery and a linked case with AIS + forcing; otherwise the scene waits)
      </label>
      {error && <p className="form-error span-4" role="alert">{error}</p>}
      <div className="form-actions span-4">
        <button type="button" className="btn btn-quiet" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Creating…' : 'Start watching'}</button>
      </div>
    </form>
  );
}

function ProviderRow({ p, refresh }: { p: Json; refresh: () => void }) {
  return (
    <tr>
      <td>
        <strong>{p.label}</strong>
        <span className="cell-note">{p.product || p.source}</span>
      </td>
      <td><Status state={p.status} /></td>
      <td className="wide small">{p.message || p.last_error || '—'}</td>
      <td className="small">{p.latest_data_timestamp ? shortTime(p.latest_data_timestamp) : '—'}</td>
      <td>{p.latest_data_timestamp && p.provider !== 'ocean_model' ? <Age from={p.latest_data_timestamp} /> : p.provider === 'ocean_model' && p.last_success ? <Age from={p.last_success} /> : '—'}</td>
      <td>{p.next_poll_at ? <Countdown to={p.next_poll_at} onZero={refresh} /> : p.next_expected_update_at ? shortTime(p.next_expected_update_at) : '—'}</td>
    </tr>
  );
}

export default function CopernicusWatch({
  live,
  refreshLive,
  navigate,
}: {
  live: Json | null;
  refreshLive: () => void;
  navigate: (p: string) => void;
}) {
  const [creating, setCreating] = useState(false),
    [syncing, setSyncing] = useState<string | null>(null),
    [results, setResults] = useState<Record<string, Json>>({}),
    [error, setError] = useState(''),
    [open, setOpen] = useState<string | null>(null),
    [observations, setObservations] = useState<Json[] | null>(null),
    [planBusy, setPlanBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setObservations(null);
    api<Json[]>(`/copernicus/watch-areas/${open}/observations`).then(setObservations).catch((e) => setError(e.message));
  }, [open, live?.events?.[0]?.id]);

  const act = async (fn: () => Promise<unknown>) => {
    setError('');
    try {
      await fn();
      refreshLive();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const syncNow = (id: string) =>
    act(async () => {
      setSyncing(id);
      try {
        const r = await post(`/copernicus/watch-areas/${id}/check-now`);
        setResults((m) => ({ ...m, [id]: r }));
      } finally {
        setSyncing(null);
      }
    });

  const header = (
    <PageHeader
      eyebrow="Observe"
      title="Copernicus Watch"
      question="What are we watching, and what has Sentinel-1 actually acquired?"
      actions={
        <button className="btn btn-primary" onClick={() => setCreating(!creating)}>
          {creating ? <X size={15} /> : <Plus size={15} />} {creating ? 'Close' : 'New watch area'}
        </button>
      }
    />
  );
  if (!live) return <>{header}<StateBlock kind="loading" title="Reading monitoring state" /></>;
  const areas: Json[] = live.watch_areas || [];
  const plan = live.providers.find((p: Json) => p.provider === 'acquisition_plan');
  const imagery = live.providers.find((p: Json) => p.provider === 'sentinel1_imagery');

  return (
    <>
      {header}
      {error && <StateBlock kind="error" compact title="Action failed">{error} Nothing else changed; retrying is safe.</StateBlock>}
      {creating && (
        <Section title="New watch area" surface>
          <WatchAreaForm
            onCreated={() => {
              setCreating(false);
              refreshLive();
            }}
            onCancel={() => setCreating(false)}
          />
        </Section>
      )}

      {imagery?.status === 'AUTH_REQUIRED' && (
        <StateBlock
          kind="auth"
          compact
          title={
            areas.length
              ? 'Catalogue monitoring is active. Calibrated imagery retrieval requires CDSE OAuth.'
              : 'Catalogue monitoring needs no credentials. Calibrated imagery retrieval requires CDSE OAuth.'
          }
        >
          Scenes are discovered from the public CDSE STAC catalogue without credentials and recorded as real acquisitions. Set
          CDSE_CLIENT_ID and CDSE_CLIENT_SECRET to retrieve calibrated SIGMA0 imagery and enable auto-analyze. Nothing is simulated meanwhile.
        </StateBlock>
      )}

      <Section title="Watched areas" note={`Catalogue polling every ${areas[0]?.interval_minutes ?? 15} min by default · scheduler ${live.scheduler?.running ? 'running' : 'stopped'}.`}>
        {!areas.length ? (
          <StateBlock
            kind="empty"
            title="No area is under watch"
            action={
              <button className="btn btn-quiet btn-sm" onClick={() => navigate('Next Observation')}>
                Choose a next-best observation
              </button>
            }
          >
            Catalogue monitoring starts when an AOI is added — from Next Observation, TruthLoop, or the form above.
          </StateBlock>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Area</th>
                  <th>Last catalogue check</th>
                  <th>Next check</th>
                  <th>Next planned pass</th>
                  <th>Auto-analyze</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {areas.map((w) => {
                  const r = results[w.id];
                  const isSyncing = syncing === w.id || w.last_status === 'SYNCING';
                  return (
                    <tr key={w.id} className={open === w.id ? 'on' : ''}>
                      <td>
                        <button className="link strong" onClick={() => setOpen(open === w.id ? null : w.id)}>
                          {w.name}
                        </button>
                        <span className="cell-note mono">
                          {w.center[1].toFixed(2)}, {w.center[0].toFixed(2)} · r {w.radius_km} km · {label(w.source)}
                        </span>
                      </td>
                      <td>
                        <Status state={isSyncing ? 'SYNCING' : w.status === 'PAUSED' ? 'PAUSED' : w.last_status} />
                        <span className="cell-note">
                          {isSyncing
                            ? 'Querying public CDSE STAC…'
                            : r?.outcome === 'NEW_EARTH_OBSERVATION'
                              ? `New Earth observation · ${r.new_observations.length} scene(s)`
                              : r?.outcome === 'NO_NEW_ACQUISITION'
                                ? 'No new acquisition'
                                : w.last_message || 'Not checked yet'}
                        </span>
                        {w.last_checked && <span className="cell-note">{shortTime(w.last_checked)}</span>}
                      </td>
                      <td>{w.status === 'PAUSED' ? <span className="muted">Paused</span> : <Countdown to={w.next_check_at} onZero={refreshLive} />}</td>
                      <td>
                        {w.next_planned_pass ? (
                          <>
                            <strong>{w.next_planned_pass.satellite}</strong> {w.next_planned_pass.mode}
                            <span className="cell-note">
                              {shortTime(w.next_planned_pass.begin)} · T− <Countdown to={w.next_planned_pass.begin} zeroText="now" />
                            </span>
                            <span className="cell-note warn-text">Planned — not guaranteed</span>
                          </>
                        ) : (
                          <span className="muted">Unknown</span>
                        )}
                      </td>
                      <td>
                        <label className="switch">
                          <input
                            type="checkbox"
                            checked={w.auto_analyze}
                            onChange={() => act(() => patch(`/copernicus/watch-areas/${w.id}`, { auto_analyze: !w.auto_analyze }))}
                          />
                          <span>{w.auto_analyze ? 'On' : 'Off'}</span>
                        </label>
                      </td>
                      <td className="row-actions">
                        <button className="btn btn-quiet btn-sm" disabled={isSyncing || w.status === 'PAUSED'} onClick={() => syncNow(w.id)}>
                          <RefreshCw size={13} className={isSyncing ? 'spin' : ''} /> Sync now
                        </button>
                        <button
                          className="icon-btn"
                          aria-label={w.status === 'ACTIVE' ? 'Pause watch' : 'Resume watch'}
                          title={w.status === 'ACTIVE' ? 'Pause' : 'Resume'}
                          onClick={() => act(() => patch(`/copernicus/watch-areas/${w.id}`, { status: w.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE' }))}
                        >
                          {w.status === 'ACTIVE' ? <Pause size={14} /> : <Play size={14} />}
                        </button>
                        <button
                          className="icon-btn"
                          aria-label="Stop watching this area"
                          title="Stop watching"
                          onClick={() => act(() => del(`/copernicus/watch-areas/${w.id}`))}
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {open && (
        <Section title={`Acquisitions · ${areas.find((w) => w.id === open)?.name || ''}`} note="Real Sentinel-1 scenes recorded from the CDSE catalogue for this AOI.">
          {!observations ? (
            <StateBlock kind="loading" compact title="Loading acquisitions" />
          ) : !observations.length ? (
            <StateBlock kind="empty" compact title="No acquisition recorded yet">
              Nothing was acquired over this AOI in the 10-day search window at the last check.
            </StateBlock>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Scene</th>
                    <th>Acquired</th>
                    <th>Orbit</th>
                    <th>Imagery</th>
                    <th>Auto-analyze</th>
                  </tr>
                </thead>
                <tbody>
                  {observations.map((o) => {
                    const s = o.provenance.scene || {};
                    const auto = o.provenance.auto_analyze;
                    return (
                      <tr key={o.id}>
                        <td>
                          <strong>{s.platform || 'Sentinel-1'} · {s.instrument_mode || '—'} {s.product_type || ''}</strong>
                          <a className="cell-note link mono" href={s.source_url} target="_blank" rel="noreferrer">{o.stac_id}</a>
                        </td>
                        <td>{o.acquired ? time(o.acquired) : '—'}<span className="cell-note">discovered {shortTime(o.created)}</span></td>
                        <td className="small">{[s.orbit_state, s.relative_orbit && `rel ${s.relative_orbit}`].filter(Boolean).join(' · ') || '—'}</td>
                        <td><Status state={o.status} /></td>
                        <td className="small">{auto ? <><Status state={auto.status} size="sm" /> <span className="cell-note">{auto.missing?.join('; ') || auto.reason || ''}</span></> : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      <Section
        title="Data sources"
        note="Each state is derived from recorded checks on this computer. External services are contacted by the local service, never by the browser."
        actions={
          <button
            className="btn btn-quiet btn-sm"
            disabled={planBusy}
            onClick={() =>
              act(async () => {
                setPlanBusy(true);
                try {
                  await post('/live-data/acquisition-plan/refresh');
                } finally {
                  setPlanBusy(false);
                }
              })
            }
          >
            <RefreshCw size={13} className={planBusy ? 'spin' : ''} /> {planBusy ? 'Fetching plan…' : 'Refresh acquisition plan'}
          </button>
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>State</th>
                <th>Detail</th>
                <th>Latest data</th>
                <th>Data age</th>
                <th>Next poll</th>
              </tr>
            </thead>
            <tbody>
              {live.providers.map((p: Json) => (
                <ProviderRow key={p.provider} p={p} refresh={refreshLive} />
              ))}
            </tbody>
          </table>
        </div>
        {plan?.plan_files?.length > 0 && (
          <p className="fine">Acquisition plan files: {plan.plan_files.join(', ')} · coverage to {plan.coverage_end ? shortTime(plan.coverage_end) : 'unknown'}.</p>
        )}
      </Section>
    </>
  );
}

export function NextObservation({
  a,
  geography,
  live,
  refreshLive,
}: {
  a: Json;
  geography: Json | null;
  live: Json | null;
  refreshLive: () => void;
}) {
  const [data, setData] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState<string | null>(null),
    [auto, setAuto] = useState(false),
    [done, setDone] = useState<Record<string, string>>({});
  useEffect(() => {
    setData(null);
    api(`/cases/${a.case_id}/next-observations?run_id=${a.run_id}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [a.run_id]);
  const watched = new Set((live?.watch_areas || []).map((w: Json) => w.origin_ref).filter(Boolean));
  const header = (
    <PageHeader
      eyebrow="Protect"
      title="Next observation"
      question="What should we observe next — and why?"
    >
      <p className="notice">Transparent weighted heuristic over this run's own outputs. It is not a trained model and not formal information gain.</p>
    </PageHeader>
  );
  if (error) return <>{header}<StateBlock kind="error" title="Next-best observation unavailable">{error}</StateBlock></>;
  if (!data) return <>{header}<StateBlock kind="loading" title="Ranking re-observation targets" /></>;
  return (
    <>
      {header}
      <div className="split">
        <div>
          <ol className="nbo-list">
            {data.candidates.map((c: Json, i: number) => {
              const ref = `nbo:${a.case_id}:${c.id}`;
              const isWatched = watched.has(ref) || done[c.id];
              return (
                <li key={c.id} className="nbo">
                  <div className="nbo-head">
                    <span className="nbo-rank mono">{i + 1}</span>
                    <div>
                      <strong>{sentence(c.target_type)}</strong>
                      <span className="muted small">{c.basis}</span>
                    </div>
                    <span className="nbo-score mono">{c.score}</span>
                  </div>
                  <p>{c.rationale}</p>
                  <div className="nbo-factors">
                    {c.components.map((f: Json) => (
                      <span key={f.name}>
                        {f.name.replaceAll('_', ' ')} <Bar value={f.value} tone="neutral" /> <b className="mono">{f.contribution}</b>
                      </span>
                    ))}
                  </div>
                  <div className="nbo-foot">
                    <span className="mono small">
                      AOI {c.center[1].toFixed(3)}, {c.center[0].toFixed(3)} · r {c.suggested_radius_km} km
                    </span>
                    {isWatched ? (
                      <span className="done"><Check size={13} /> {done[c.id] || 'Watching'}</span>
                    ) : (
                      <button
                        className="btn btn-quiet btn-sm"
                        disabled={busy === c.id}
                        onClick={async () => {
                          setBusy(c.id);
                          try {
                            const r = await post(`/cases/${a.case_id}/next-observations/${c.id}/watch?run_id=${a.run_id}&auto_analyze=${auto}`);
                            setDone((m) => ({ ...m, [c.id]: r.already_watching ? 'Already watching' : 'Added to Copernicus Watch' }));
                            refreshLive();
                          } catch (e) {
                            setError((e as Error).message);
                          } finally {
                            setBusy(null);
                          }
                        }}
                      >
                        <Plus size={13} /> {busy === c.id ? 'Adding…' : 'Add to Copernicus Watch'}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
          <label className="check">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            Auto-analyze scenes found at AOIs I add (reuses this case's AIS and forcing only where they genuinely cover the new acquisition)
          </label>
          <p className="fine">Weights: {Object.entries(data.weights).map(([k, v]: [string, any]) => `${k.replaceAll('_', ' ')} ${Math.round(v * 100)}%`).join(' · ')}</p>
        </div>
        <MaritimeMap analysis={a} geography={geography} candidates={data.candidates} layers={{ aoi: true, lead: true }} height={620} timeline={false} label="Candidate AOIs" />
      </div>
    </>
  );
}
