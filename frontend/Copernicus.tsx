import { useEffect, useState } from 'react';
import { CircleAlert, Plus, RefreshCw, SatelliteDish, Trash2 } from 'lucide-react';
import { api, post, Json, time } from './api';

function statusClass(status?: string) {
  switch (status) {
    case 'OK':
    case 'READY_TO_IMPORT':
      return 'copernicus-pill ok';
    case 'AUTH_REQUIRED':
      return 'copernicus-pill auth';
    case 'OFFLINE':
    case 'FETCH_FAILED':
      return 'copernicus-pill offline';
    case 'ERROR':
      return 'copernicus-pill error';
    default:
      return 'copernicus-pill pending';
  }
}

/** Live-monitor status card, shown wherever the Copernicus mode matters. */
export function CopernicusBadge({ status }: { status: Json | null }) {
  if (!status) return null;
  return (
    <span className={'badge ' + (status.credentials_configured ? 'green' : 'amber')}>
      <SatelliteDish size={11} style={{ marginRight: 4 }} />
      {status.credentials_configured ? 'COPERNICUS LIVE' : 'COPERNICUS DEMO — NOT CONFIGURED'}
    </span>
  );
}

export function NextBestObservation({
  caseId,
  runId,
  onWatchCreated,
}: {
  caseId: string;
  runId: string;
  onWatchCreated: (name: string) => void;
}) {
  const [data, setData] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState<string | null>(null);
  const load = () =>
    api(`/cases/${caseId}/next-observations?run_id=${runId}`)
      .then(setData)
      .catch((e) => setError(e.message));
  useEffect(() => {
    setData(null);
    setError('');
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, runId]);
  if (error) return <p className="limitation">{error}</p>;
  if (!data) return <p className="muted">Loading next-best-observation ranking…</p>;
  return (
    <section className="intelligence-block next-observation">
      <h3>Next-best-observation priority</h3>
      <p className="micro">
        {data.method} Weights: {Object.entries(data.weights).map(([k, v]: [string, any]) => `${k.replace(/_/g, ' ')} ${Math.round(v * 100)}%`).join(' · ')}
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Candidate target</th>
              <th>Priority /100</th>
              <th>Why</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.candidates.map((c: Json) => (
              <tr key={c.id}>
                <td>
                  <strong>{c.target_type}</strong>
                  <br />
                  <small className="muted">{c.basis}</small>
                </td>
                <td>{c.score}</td>
                <td style={{ maxWidth: 320 }}>{c.rationale}</td>
                <td>
                  <button
                    className="secondary"
                    disabled={busy === c.id}
                    onClick={async () => {
                      setBusy(c.id);
                      try {
                        await post(`/cases/${caseId}/next-observations/${c.id}/watch?run_id=${runId}`);
                        onWatchCreated(c.target_type);
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    {busy === c.id ? 'Adding…' : 'Add to Copernicus Watch'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="micro">
        Heuristic scoring only — not a probability. Each candidate is a starting point for an
        analyst's judgment about where to point the next acquisition.
      </p>
    </section>
  );
}

function WatchAreaForm({ onCreated }: { onCreated: () => void }) {
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
      });
      (e.target as HTMLFormElement).reset();
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="response-form watch-area-form">
      <label>
        Name
        <input name="name" required maxLength={160} placeholder="e.g. Gulf approach watch" />
      </label>
      <div className="form-pair">
        <label>
          Center longitude
          <input name="lon" type="number" step="any" min="-180" max="180" required />
        </label>
        <label>
          Center latitude
          <input name="lat" type="number" step="any" min="-90" max="90" required />
        </label>
      </div>
      <div className="form-pair">
        <label>
          Radius (km)
          <input name="radius" type="number" min="0.1" max="500" step="0.1" defaultValue="15" required />
        </label>
        <label>
          Poll interval (min)
          <input name="interval" type="number" min="5" max="1440" defaultValue="15" required />
        </label>
      </div>
      <button className="primary" disabled={busy}>
        {busy ? 'Creating…' : 'Create watch area'}
      </button>
      {error && (
        <p role="alert" className="limitation">
          {error}
        </p>
      )}
    </form>
  );
}

export default function CopernicusMonitor() {
  const [status, setStatus] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState<string | null>(null);
  const refresh = () =>
    api('/copernicus/status')
      .then(setStatus)
      .catch((e) => setError(e.message));
  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 20000);
    return () => clearInterval(interval);
  }, []);
  if (error) return <section className="panel padded">{error}</section>;
  if (!status) return <section className="panel padded">Loading Copernicus status…</section>;
  const areas: Json[] = status.watch_areas || [];
  return (
    <section className="panel padded">
      <h2>Copernicus Sentinel-1 live monitor</h2>
      <div className={'copernicus-summary ' + (status.credentials_configured ? 'live' : 'demo')}>
        <SatelliteDish size={20} />
        <div>
          <strong>{status.credentials_configured ? 'LIVE — credentials configured' : 'DEMO — CDSE credentials not configured'}</strong>
          <p className="micro">
            Collection {status.collection} · discovery window {status.search_window_days} days ·
            polling every {status.poll_granularity_seconds}s per watch area (backed off up to{' '}
            {status.max_backoff_minutes} min on repeated failure)
          </p>
        </div>
      </div>
      {!status.credentials_configured && (
        <p className="limitation">
          <CircleAlert size={13} /> Set CDSE_CLIENT_ID and CDSE_CLIENT_SECRET as environment
          variables (see .env.example) to enable live discovery. Watch areas remain saved and will
          report AUTH_REQUIRED, not fail silently, until credentials are supplied.
        </p>
      )}
      <p className="micro">{status.note}</p>
      {notice && <p className="micro" style={{ color: '#60dab6' }}>{notice}</p>}

      <h3>Watch areas</h3>
      {!areas.length && <p className="muted">No watch areas yet. Create one below, or use "Add to Copernicus Watch" from a case's Attention &amp; Alerts page.</p>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Center</th>
              <th>Radius</th>
              <th>Interval</th>
              <th>Status</th>
              <th>Last message</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {areas.map((a) => (
              <tr key={a.id}>
                <td>
                  <strong>{a.name}</strong>
                  <br />
                  <small className="muted">{a.source}{a.case_id ? ` · case ${a.case_id.slice(0, 8)}` : ''}</small>
                </td>
                <td>
                  {a.center_lat.toFixed(3)}°, {a.center_lon.toFixed(3)}°
                </td>
                <td>{a.radius_km} km</td>
                <td>{a.interval_minutes} min</td>
                <td>
                  <span className={statusClass(a.last_status)}>{a.last_status || 'PENDING'}</span>
                  <br />
                  <small className="muted">{a.status}</small>
                </td>
                <td style={{ maxWidth: 260 }}>
                  <small>{a.last_message || '—'}</small>
                  <br />
                  <small className="muted">{a.last_checked ? time(a.last_checked) : 'Never checked'}</small>
                </td>
                <td className="watch-actions">
                  <button
                    className="icon-button"
                    title="Check now"
                    disabled={busy === a.id}
                    onClick={async () => {
                      setBusy(a.id);
                      try {
                        const result = await post(`/copernicus/watch-areas/${a.id}/check-now`, {});
                        setNotice(`${a.name}: ${result.status}${result.new_observations ? ` · ${result.new_observations.length} new` : ''}`);
                        await refresh();
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    <RefreshCw size={14} />
                  </button>
                  <button
                    className="icon-button"
                    title={a.status === 'ACTIVE' ? 'Pause' : 'Resume'}
                    onClick={async () => {
                      await fetch(`/api/v1/copernicus/watch-areas/${a.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: a.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE' }),
                      });
                      await refresh();
                    }}
                  >
                    {a.status === 'ACTIVE' ? '❚❚' : '►'}
                  </button>
                  <button
                    className="icon-button"
                    title="Delete watch area"
                    onClick={async () => {
                      await fetch(`/api/v1/copernicus/watch-areas/${a.id}`, { method: 'DELETE' });
                      await refresh();
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>
        <Plus size={14} style={{ verticalAlign: 'middle' }} /> New watch area
      </h3>
      <WatchAreaForm onCreated={refresh} />
    </section>
  );
}
