import { useEffect, useState } from 'react';
import {
  ArrowRight,
  ChevronRight,
  Compass,
  FlaskConical,
  Play,
  Plus,
  Radar,
  Ship,
  Waves,
  Wind,
} from 'lucide-react';
import { Json, api, coordinate, shortTime, time } from './api';
import MaritimeMap from './MaritimeMap';
import VesselIllustration from './VesselIllustration';
import { LiveStrip, provider } from './LiveData';
import { PageHeader, StateBlock, Status, Tag } from './ui';

const RUN_STAGE_TO_STEP: [string, string][] = [
  ['Satellite', 'OBSERVE'],
  ['Environmental', 'RECONSTRUCT'],
  ['Monte Carlo', 'RECONSTRUCT'],
  ['AIS', 'CORRELATE'],
  ['Behaviour', 'CORRELATE'],
  ['Attribution', 'CORRELATE'],
  ['Impact', 'PROTECT'],
  ['Forensic', 'PROVE'],
];

export function journey(
  a: Json,
  live: Json | null,
  truth: Json | null,
  scenarios: Json[] | null,
  job: Json | null,
) {
  const watches = (live?.watch_areas || []).filter((w: Json) => w.case_id === a.case_id);
  const vessels = a.vessels?.length || 0;
  const receptors = a.receptors?.features?.length || 0;
  const running =
    job?.state === 'RUNNING'
      ? RUN_STAGE_TO_STEP.find(([k]) => (job.stage || '').startsWith(k))?.[1]
      : null;
  const observeAgain = watches.some((w: Json) => w.last_status === 'SYNCING')
    ? ['ACTIVE', 'Querying the catalogue now']
    : watches.some((w: Json) => ['OFFLINE', 'ERROR', 'RATE_LIMITED'].includes(w.last_status))
      ? ['ERROR', 'Catalogue unreachable for a linked AOI']
      : watches.some((w: Json) => w.status === 'ACTIVE')
        ? ['ACTIVE', `${watches.length} AOI${watches.length > 1 ? 's' : ''} under watch`]
        : ['WAITING', 'No AOI from this case is under watch'];
  const steps: [string, string, string, string][] = [
    ['OBSERVE', 'Satellite Analysis', 'COMPLETE', `SAR scene ${shortTime(a.observation_time)}`],
    [
      'VERIFY',
      'Satellite Analysis',
      'BLOCKED',
      'No validated oil / look-alike classifier; needs independent confirmation',
    ],
    [
      'RECONSTRUCT',
      'Drift & Origin',
      a.origin ? 'COMPLETE' : 'UNAVAILABLE',
      `Origin region r90 ${a.origin?.radius90_km} km`,
    ],
    [
      'CORRELATE',
      'Vessel Intelligence',
      vessels ? 'COMPLETE' : 'UNAVAILABLE',
      vessels ? `${vessels} vessels screened` : 'AIS data required',
    ],
    [
      'CHALLENGE',
      'TruthLoop',
      !vessels ? 'UNAVAILABLE' : truth?.challenged ? 'COMPLETE' : truth ? 'WAITING' : 'WAITING',
      truth?.challenged
        ? `Stability ${truth.stability.state.toLowerCase()}`
        : 'Conclusion not yet challenged',
    ],
    [
      'PREDICT',
      'Drift & Origin',
      a.forecast ? 'COMPLETE' : 'UNAVAILABLE',
      `Forecast to +${a.forecast?.steps?.at(-1)?.hours} h`,
    ],
    [
      'PROTECT',
      'Ecological Exposure',
      receptors ? 'COMPLETE' : 'UNAVAILABLE',
      receptors ? `${receptors} receptor layers screened` : 'No receptor layer loaded',
    ],
    [
      'RESPOND',
      'Response Planning',
      scenarios === null ? 'WAITING' : scenarios.length ? 'COMPLETE' : 'WAITING',
      scenarios?.length
        ? `${scenarios.length} planning scenario${scenarios.length > 1 ? 's' : ''}`
        : 'No planning scenario yet',
    ],
    ['OBSERVE AGAIN', 'Next Observation', observeAgain[0], observeAgain[1]],
    ['PROVE', 'Reports', 'COMPLETE', 'Report + SHA-256 evidence package'],
  ];
  return steps.map(([name, page, state, detail]) => ({
    name,
    page,
    state: running ? (name === running ? 'ACTIVE' : state) : state,
    detail,
  }));
}

export function Journey({
  steps,
  navigate,
  page,
}: {
  steps: Json[];
  navigate: (p: string) => void;
  page?: string;
}) {
  return (
    <ol className="journey" aria-label="Investigation journey">
      {steps.map((s, i) => (
        <li
          key={s.name}
          className={`journey-step st-${s.state.toLowerCase()} ${page === s.page ? 'here' : ''}`}
        >
          <button
            onClick={() => navigate(s.page)}
            title={`${s.name}: ${s.state.toLowerCase()} — ${s.detail}`}
          >
            <span className="journey-index">{String(i + 1).padStart(2, '0')}</span>
            <span className="journey-name">{s.name}</span>
            <Status state={s.state} size="sm" />
          </button>
        </li>
      ))}
    </ol>
  );
}

export default function Overview({
  a,
  live,
  liveError,
  refreshLive,
  truth,
  job,
  geography,
  selected,
  setSelected,
  navigate,
  busy,
  onRun,
  onDemo,
  onImport,
  onOpenVessel,
}: {
  a: Json | null;
  live: Json | null;
  liveError: string;
  refreshLive: () => void;
  truth: Json | null;
  job: Json | null;
  geography: Json | null;
  selected: string | null;
  setSelected: (id: string) => void;
  navigate: (page: string) => void;
  busy: boolean;
  onRun: () => void;
  onDemo: () => void;
  onImport: () => void;
  onOpenVessel: (mmsi: string) => void;
}) {
  const [scenarios, setScenarios] = useState<Json[] | null>(null);
  useEffect(() => {
    if (!a) return;
    setScenarios(null);
    api<Json[]>(`/cases/${a.case_id}/scenarios?run_id=${a.run_id}`)
      .then(setScenarios)
      .catch(() => setScenarios([]));
  }, [a?.run_id]);

  if (!a)
    return (
      <>
        <PageHeader
          eyebrow="Near-real-time Earth observation"
          title="Live operations"
          question="Monitor genuine external providers and Sentinel-1 acquisitions, or open a clearly labelled investigation."
        />
        <LiveStrip live={live} error={liveError} refresh={refreshLive} navigate={navigate} />
        <div className="welcome">
          <p>
            Provider health and watched-area activity below come from the backend scheduler. Open
            Copernicus Watch to inspect real catalogue observations, calibrated SAR provenance,
            acquisition plans and ocean-model coverage.
          </p>
          <div className="welcome-actions">
            <button className="btn btn-primary" onClick={() => navigate('Copernicus Watch')}>
              Open live operations <ArrowRight size={15} />
            </button>
            <button className="btn btn-primary" onClick={onDemo} disabled={busy}>
              <Play size={15} /> Create demo case
            </button>
            <button className="btn btn-quiet" onClick={onImport}>
              <Plus size={15} /> Import case
            </button>
          </div>
          <p className="fine">
            <strong>Live Operations</strong> uses external provider state.{' '}
            <strong>Demo Case</strong> uses a synthetic Sentinel-1-compatible scene, fictional
            vessels and seeded forcing, labelled DEMO throughout.
          </p>
        </div>
      </>
    );

  const steps = journey(a, live, truth, scenarios, job);
  const lead = a.vessels[0];
  const exposure = a.impact.receptors.find((r: Json) => r.first_overlap_h !== null);
  const cat = provider(live, 'sentinel1_catalogue');
  const demo = a.source_type !== 'REAL';
  const currentStep =
    steps.find((s) => s.state === 'ACTIVE') || steps.find((s) => s.state === 'WAITING');
  const spread48 = a.forecast.steps.find((s: Json) => s.hours === 48)?.spread90_km;
  const spread24 = a.forecast.steps.find((s: Json) => s.hours === 24)?.spread90_km;
  const forcing = a.environment?.records?.reduce((best: Json, row: Json) =>
    Math.abs(Date.parse(row.time) - Date.parse(a.observation_time)) <
    Math.abs(Date.parse(best.time) - Date.parse(a.observation_time))
      ? row
      : best,
  );
  const currentSpeed = forcing
    ? Math.hypot(forcing.current_east_ms, forcing.current_north_ms)
    : null;
  const currentDirection = forcing
    ? ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][
        Math.round(
          (((Math.atan2(forcing.current_east_ms, forcing.current_north_ms) * 180) / Math.PI + 360) %
            360) /
            45,
        ) % 8
      ]
    : '—';
  const leadGapMinutes = lead?.gaps?.reduce((sum: number, gap: Json) => sum + gap.minutes, 0) || 0;
  const overlap = lead?.components?.find(
    (component: Json) => component.name === 'Trajectory overlap',
  )?.value;
  return (
    <>
      <PageHeader
        eyebrow={
          <>
            Investigation · <span className="mono">{a.case_id.slice(0, 8)}</span>
          </>
        }
        title={a.name}
        actions={
          <>
            <button className="btn btn-quiet" onClick={onRun} disabled={busy}>
              <Play size={15} /> {busy ? 'Processing…' : 'Re-run investigation'}
            </button>
            <button className="btn btn-primary" onClick={() => navigate('TruthLoop')}>
              <FlaskConical size={15} />{' '}
              {truth?.challenged ? 'Review challenge' : 'Challenge conclusion'}
            </button>
          </>
        }
      >
        <div className="incident-meta">
          <span className="classification">Oil candidate — classification pending</span>
          <Tag tone={demo ? 'demo' : 'ok'}>{demo ? 'Demo data' : 'Real inputs'}</Tag>
          <span>{coordinate(a.spill.centroid)}</span>
          <span>{time(a.observation_time)}</span>
          {currentStep && (
            <span>
              Stage <b>{currentStep.name.toLowerCase()}</b>
            </span>
          )}
        </div>
      </PageHeader>

      <details className="overview-live">
        <summary>
          <span>Data sources</span>
          <span className="overview-source-badges">
            <Status state={cat?.status || 'WAITING'} size="sm" /> Sentinel-1 ·{' '}
            <Tag tone="ok">Ocean current</Tag> ·{' '}
            <Tag tone={demo ? 'demo' : 'ok'}>{demo ? 'AIS demo' : 'AIS'}</Tag>
          </span>
          <span>Open freshness and provider details</span>
        </summary>
        <LiveStrip live={live} error={liveError} refresh={refreshLive} navigate={navigate} />
      </details>

      <div className="overview-command">
        <section className="overview-map" aria-label="Investigation map">
          <div className="map-section-head">
            <div>
              <span className="eyebrow">Operational picture</span>
              <h2>What happened, and where?</h2>
            </div>
            <p>
              <b>Red</b> is observed · <b>Purple</b> is modeled origin · <b>Blue</b> is predicted
              drift
            </p>
          </div>
          <MaritimeMap
            analysis={a}
            geography={geography}
            selected={selected}
            onSelect={setSelected}
            truth={truth}
            layers={{ aoi: false, ais: true, currents: true, gaps: false }}
            height={520}
            onOpenVessel={onOpenVessel}
          />
        </section>

        <aside className="incident-rail" aria-label="Incident summary">
          <section className="intel-card spill-card">
            <div className="intel-card-head">
              <span>
                <Radar size={15} /> Oil candidate
              </span>
              <Tag tone="warn">Pending</Tag>
            </div>
            <strong className="intel-primary">{a.spill.area_km2.toFixed(1)} km²</strong>
            <p>
              SAR detected a dark surface feature. It still requires independent oil/look-alike
              classification.
            </p>
            <dl className="compact-dl">
              <div>
                <dt>Contrast</dt>
                <dd>{a.spill.contrast_db} dB</dd>
              </div>
              <div>
                <dt>Observed</dt>
                <dd>{shortTime(a.observation_time)}</dd>
              </div>
              <div>
                <dt>Location</dt>
                <dd>{coordinate(a.spill.centroid)}</dd>
              </div>
            </dl>
            <button className="card-link" onClick={() => navigate('Satellite Analysis')}>
              Inspect SAR evidence <ChevronRight size={14} />
            </button>
          </section>

          <section className="intel-card lead-card">
            <div className="intel-card-head">
              <span>
                <Ship size={15} /> Leading vessel
              </span>
              <Tag tone="demo">{demo ? 'Demo' : 'AIS'}</Tag>
            </div>
            {lead ? (
              <>
                <VesselIllustration type={lead.type} compact />
                <button className="lead-name" onClick={() => onOpenVessel(lead.mmsi)}>
                  {lead.name}
                  <ChevronRight size={15} />
                </button>
                <p className="lead-score">
                  Relevance {lead.score}/100 <span>investigative ranking, not culpability</span>
                </p>
                <dl className="compact-dl">
                  <div>
                    <dt>Closest to origin</dt>
                    <dd>{lead.nearest_km} km</dd>
                  </div>
                  <div>
                    <dt>Track overlap</dt>
                    <dd>{overlap?.toFixed?.(0) ?? '—'}%</dd>
                  </div>
                  <div>
                    <dt>AIS interruption</dt>
                    <dd>{leadGapMinutes ? `${Math.round(leadGapMinutes)} min` : 'None'}</dd>
                  </div>
                </dl>
                <button className="card-link" onClick={() => onOpenVessel(lead.mmsi)}>
                  Open vessel dossier <ChevronRight size={14} />
                </button>
              </>
            ) : (
              <StateBlock kind="empty" title="No vessel lead">
                AIS tracks are required before the system can rank nearby vessels.
              </StateBlock>
            )}
          </section>
        </aside>
      </div>

      <section className="evidence-row" aria-label="Modeled evidence and forecast">
        <article className="intel-card evidence-card">
          <div className="intel-card-head">
            <span>
              <Waves size={15} /> Environmental forcing
            </span>
            <Status state="CURRENT" size="sm" />
          </div>
          <div className="evidence-value">
            {currentDirection} · {currentSpeed?.toFixed(2) ?? '—'} m/s
          </div>
          <p>Current direction used by the origin and drift models at observation time.</p>
          <div className="vector-pair">
            <span>
              <Compass size={13} /> E {forcing?.current_east_ms ?? '—'} m/s
            </span>
            <span>
              <Wind size={13} /> N {forcing?.current_north_ms ?? '—'} m/s
            </span>
          </div>
          <details>
            <summary>Technical detail</summary>
            <p>
              Forcing uncertainty ±{forcing?.current_sigma_ms ?? '—'} m/s. Wind E{' '}
              {forcing?.wind_east_ms ?? '—'}, N {forcing?.wind_north_ms ?? '—'} m/s.
            </p>
          </details>
        </article>

        <article className="intel-card evidence-card origin-card">
          <div className="intel-card-head">
            <span>
              <Radar size={15} /> Modeled origin
            </span>
            <Tag tone="model">Modeled</Tag>
          </div>
          <div className="evidence-value">{a.origin.radius90_km} km uncertainty radius</div>
          <p>
            Backtracking places the likely release within the purple region, based on the supplied
            release window.
          </p>
          <div className="time-window">
            <span>{shortTime(a.origin.release_window?.[0])}</span>
            <i />
            <span>{shortTime(a.origin.release_window?.[1])}</span>
          </div>
          <button className="card-link" onClick={() => navigate('Drift & Origin')}>
            Review backtracking <ChevronRight size={14} />
          </button>
        </article>

        <article className="intel-card evidence-card forecast-card">
          <div className="intel-card-head">
            <span>
              <ArrowRight size={15} /> Predicted drift
            </span>
            <Tag tone="forecast">Forecast</Tag>
          </div>
          <div className="forecast-horizons">
            <span>
              <small>+24 h</small>
              <b>{spread24 ?? '—'} km</b>
            </span>
            <span>
              <small>+48 h</small>
              <b>{spread48 ?? '—'} km</b>
            </span>
          </div>
          <p>
            Blue envelopes show where the candidate may spread; they do not confirm contamination.
          </p>
          <button className="card-link" onClick={() => navigate('Ecological Exposure')}>
            {exposure
              ? `${exposure.name} · potential +${exposure.first_overlap_h} h`
              : 'No sampled receptor overlap'}{' '}
            <ChevronRight size={14} />
          </button>
        </article>

        <article className="intel-card evidence-card next-card">
          <div className="intel-card-head">
            <span>
              <FlaskConical size={15} /> Investigate next
            </span>
            <Status
              state={truth?.challenged ? truth.stability.state : 'NOT_CHALLENGED'}
              size="sm"
            />
          </div>
          <div className="evidence-value">
            {truth?.challenged ? 'Review challenged lead' : 'Challenge the leading explanation'}
          </div>
          <p>
            {truth?.challenged
              ? truth.stability.reasons[0]
              : 'Test whether the vessel ranking survives timing, drift and data-quality alternatives.'}
          </p>
          <button className="card-link" onClick={() => navigate('TruthLoop')}>
            Open TruthLoop <ChevronRight size={14} />
          </button>
        </article>
      </section>

      <section className="journey-wrap" aria-label="Investigation journey">
        <div className="section-head">
          <h2>Investigation journey</h2>
          <p className="section-note">
            States are computed from this run's data and live monitoring — nothing is marked
            complete by default.
          </p>
        </div>
        <Journey steps={steps} navigate={navigate} />
      </section>
    </>
  );
}
