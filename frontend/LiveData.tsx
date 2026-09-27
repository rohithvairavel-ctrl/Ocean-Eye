import { useEffect, useRef, useState } from 'react';
import { Radio, Satellite, Ship, Waves, X } from 'lucide-react';
import { Json, api, shortTime } from './api';
import { Age, Countdown, Status } from './ui';

export function provider(live: Json | null, key: string): Json | null {
  return live?.providers?.find((p: Json) => p.provider === key) || null;
}

/** Polls the LOCAL backend's control plane. External services are never called from the browser. */
export function useLiveStatus(caseId: string, intervalMs = 7000) {
  const [live, setLive] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [offset, setOffset] = useState(0);
  const inflight = useRef(false),
    again = useRef(false);
  const refresh = async (): Promise<void> => {
    if (inflight.current) {
      // A refresh requested mid-flight (e.g. right after "Sync now" completes) must not be
      // dropped, or the UI would keep showing the pre-action state until the next poll.
      again.current = true;
      return;
    }
    inflight.current = true;
    try {
      const started = Date.now();
      const data = await api('/live-data/status' + (caseId ? `?case_id=${caseId}` : ''));
      const rtt = Date.now() - started;
      setOffset(Date.parse(data.server_time) - (started + rtt / 2));
      setLive(data);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inflight.current = false;
      if (again.current) {
        again.current = false;
        refresh();
      }
    }
  };
  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, intervalMs);
    return () => clearInterval(id);
  }, [caseId, intervalMs]);
  return { live, error, refresh, offset };
}

function Segment({
  icon: Icon,
  name,
  state,
  children,
  onClick,
  foot,
}: {
  icon: any;
  name: string;
  state?: string | null;
  children: React.ReactNode;
  onClick?: () => void;
  foot?: React.ReactNode;
}) {
  return (
    <div className="live-seg">
      <button className="live-seg-head" onClick={onClick} disabled={!onClick}>
        <Icon size={14} aria-hidden />
        <span>{name}</span>
        <Status state={state} size="sm" />
      </button>
      <dl>{children}</dl>
      {foot && <p className="live-foot">{foot}</p>}
    </div>
  );
}

export function LiveStrip({
  live,
  error,
  refresh,
  navigate,
}: {
  live: Json | null;
  error: string;
  refresh: () => void;
  navigate: (page: string) => void;
}) {
  if (error && !live)
    return (
      <div className="live-strip offline" role="status">
        <Status state="OFFLINE" /> <span className="muted">Live data status unavailable — {error}</span>
      </div>
    );
  if (!live) return <div className="live-strip loading" aria-busy="true"><span className="muted">Reading live data network…</span></div>;
  const cat = provider(live, 'sentinel1_catalogue');
  const imagery = provider(live, 'sentinel1_imagery');
  const ocean = provider(live, 'ocean_model');
  const forcing = provider(live, 'case_forcing');
  const ais = provider(live, 'ais');
  const latest = live.events?.[0];
  const pass = live.next_planned_pass;
  const reasons: string[] = live.health?.reasons || [];
  return (
    <>
    {error && (
      <div className="live-lost" role="alert">
        <Status state="OFFLINE" text="Connection lost" size="sm" /> The local OCEAN-EYE service stopped responding. Showing the last
        known state from {shortTime(live.generated_at)} — values may be out of date. Retrying automatically.
      </div>
    )}
    <div className={`live-strip ${error ? 'is-stale' : ''}`} aria-label="Live data network">
      <Segment
        icon={Satellite}
        name="Sentinel-1"
        state={cat?.status}
        onClick={() => navigate('Copernicus Watch')}
        foot={<>Calibrated imagery <Status state={imagery?.status} size="sm" /></>}
      >
        {cat?.status === 'NOT_CONFIGURED' ? (
          <div className="live-row wide">
            <dt>Catalogue</dt>
            <dd>
              No AOI under watch · <button className="link" onClick={() => navigate('Next Observation')}>Add one</button>
            </dd>
          </div>
        ) : (
          <>
            <div className="live-row">
              <dt>Latest acquisition</dt>
              <dd title={cat?.latest_data_timestamp || ''}>
                {cat?.latest_data_timestamp ? `${latest?.platform || 'Sentinel-1'} · ${shortTime(cat.latest_data_timestamp)}` : 'None in 10 days'}
              </dd>
            </div>
            <div className="live-row">
              <dt>Data age</dt>
              <dd><Age from={cat?.latest_data_timestamp} /></dd>
            </div>
            <div className="live-row">
              <dt>Next catalogue check</dt>
              <dd>{cat?.status === 'SYNCING' ? <span className="mono">Syncing…</span> : <Countdown to={cat?.next_poll_at} onZero={refresh} />}</dd>
            </div>
          </>
        )}
        <div className="live-row">
          <dt>Next planned pass</dt>
          <dd title={pass ? `${pass.satellite} ${pass.mode} · ${pass.begin} · planned, not guaranteed` : 'No reliable planned acquisition found'}>
            {pass ? (
              <>
                {pass.satellite.replace('Sentinel-', 'S')} · T− <Countdown to={pass.begin} zeroText="now" />
              </>
            ) : (
              <span className="muted">Unknown</span>
            )}
          </dd>
        </div>
      </Segment>
      <Segment
        icon={Waves}
        name="Ocean"
        state={ocean?.status}
        onClick={() => navigate('Drift & Origin')}
        foot={forcing ? <>Case forcing <Status state={forcing.status} size="sm" /></> : undefined}
      >
        <div className="live-row">
          <dt>Model valid to</dt>
          <dd>{ocean?.model_coverage_end ? shortTime(ocean.model_coverage_end) : <span className="muted">Unknown</span>}</dd>
        </div>
        <div className="live-row">
          <dt>Currents</dt>
          <dd>{ocean?.data_age_seconds != null ? <Age from={ocean.last_success} /> : <span className="muted">None</span>}</dd>
        </div>
      </Segment>
      <Segment icon={Ship} name="AIS" state={ais?.status} onClick={() => navigate('Vessel Intelligence')}>
        <div className="live-row">
          <dt>Latest</dt>
          <dd>{ais?.latest_data_timestamp ? shortTime(ais.latest_data_timestamp) : <span className="muted">None</span>}</dd>
        </div>
        <div className="live-row">
          <dt>Age</dt>
          <dd><Age from={ais?.latest_data_timestamp} /></dd>
        </div>
      </Segment>
      <Segment icon={Radio} name="Health" state={live.health?.state} onClick={() => navigate('Copernicus Watch')}>
        <div className="live-row wide">
          <dt>Sources</dt>
          <dd title={reasons.join('\n')}>{reasons.length ? `${reasons.length} source${reasons.length === 1 ? ' needs' : 's need'} attention` : 'All sources responding'}</dd>
        </div>
        <div className="live-row wide">
          <dt>Scheduler</dt>
          <dd>Scheduler {live.scheduler?.running ? 'running' : 'stopped'}</dd>
        </div>
      </Segment>
    </div>
    </>
  );
}

/** Announces genuinely new observations recorded by the backend since this page opened. */
export function NewObservationToast({ live, navigate }: { live: Json | null; navigate: (p: string) => void }) {
  const seen = useRef<Set<string> | null>(null);
  const [queue, setQueue] = useState<Json[]>([]);
  useEffect(() => {
    const events: Json[] = live?.events || [];
    if (!live) return;
    if (seen.current === null) {
      seen.current = new Set(events.map((e) => e.id));
      return;
    }
    const fresh = events.filter((e) => !seen.current!.has(e.id));
    fresh.forEach((e) => seen.current!.add(e.id));
    if (fresh.length) setQueue((q) => [...fresh, ...q].slice(0, 3));
  }, [live?.generated_at]);
  if (!queue.length) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {queue.map((e) => {
        const auto = e.auto_analyze?.status;
        return (
          <div className="toast" key={e.id}>
            <div>
              <span className="eyebrow accent">New Earth observation</span>
              <strong>
                {e.platform || 'Sentinel-1'} · {e.instrument_mode || 'SAR'} · {shortTime(e.acquired)}
              </strong>
              <span className="muted">
                {e.watch_name} ·{' '}
                {auto === 'ANALYZED'
                  ? 'Analysis complete'
                  : auto === 'BLOCKED'
                    ? 'Auto-analyze blocked: calibrated imagery needs CDSE OAuth'
                    : auto === 'WAITING_FOR_DATA'
                      ? 'Waiting for data'
                      : e.status === 'AUTH_REQUIRED'
                        ? 'Catalogue record · imagery needs OAuth'
                        : e.status.replaceAll('_', ' ').toLowerCase()}
              </span>
            </div>
            <div className="toast-actions">
              <button className="link" onClick={() => navigate('Copernicus Watch')}>Inspect</button>
              <button aria-label="Dismiss" className="icon-btn" onClick={() => setQueue((q) => q.filter((x) => x.id !== e.id))}>
                <X size={14} />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
