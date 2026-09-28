import { useEffect, useState } from 'react';
import { ArrowRight, ChevronRight, FlaskConical, Play, Plus } from 'lucide-react';
import { Json, api, coordinate, overlapTime, shortTime, time, sentence } from './api';
import MaritimeMap from './MaritimeMap';
import { LiveStrip, provider } from './LiveData';
import { Countdown, Metric, PageHeader, StateBlock, Status, Tag } from './ui';

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

export function journey(a: Json, live: Json | null, truth: Json | null, scenarios: Json[] | null, job: Json | null) {
  const watches = (live?.watch_areas || []).filter((w: Json) => w.case_id === a.case_id);
  const vessels = a.vessels?.length || 0;
  const receptors = a.receptors?.features?.length || 0;
  const running = job?.state === 'RUNNING' ? RUN_STAGE_TO_STEP.find(([k]) => (job.stage || '').startsWith(k))?.[1] : null;
  const observeAgain = watches.some((w: Json) => w.last_status === 'SYNCING')
    ? ['ACTIVE', 'Querying the catalogue now']
    : watches.some((w: Json) => ['OFFLINE', 'ERROR', 'RATE_LIMITED'].includes(w.last_status))
      ? ['ERROR', 'Catalogue unreachable for a linked AOI']
      : watches.some((w: Json) => w.status === 'ACTIVE')
        ? ['ACTIVE', `${watches.length} AOI${watches.length > 1 ? 's' : ''} under watch`]
        : ['WAITING', 'No AOI from this case is under watch'];
  const steps: [string, string, string, string][] = [
    ['OBSERVE', 'Satellite Analysis', 'COMPLETE', `SAR scene ${shortTime(a.observation_time)}`],
    ['VERIFY', 'Satellite Analysis', 'BLOCKED', 'No validated oil / look-alike classifier; needs independent confirmation'],
    ['RECONSTRUCT', 'Drift & Origin', a.origin ? 'COMPLETE' : 'UNAVAILABLE', `Origin region r90 ${a.origin?.radius90_km} km`],
    ['CORRELATE', 'Vessel Intelligence', vessels ? 'COMPLETE' : 'UNAVAILABLE', vessels ? `${vessels} vessels screened` : 'AIS data required'],
    [
      'CHALLENGE',
      'TruthLoop',
      !vessels ? 'UNAVAILABLE' : truth?.challenged ? 'COMPLETE' : truth ? 'WAITING' : 'WAITING',
      truth?.challenged ? `Stability ${truth.stability.state.toLowerCase()}` : 'Conclusion not yet challenged',
    ],
    ['PREDICT', 'Drift & Origin', a.forecast ? 'COMPLETE' : 'UNAVAILABLE', `Forecast to +${a.forecast?.steps?.at(-1)?.hours} h`],
    ['PROTECT', 'Ecological Exposure', receptors ? 'COMPLETE' : 'UNAVAILABLE', receptors ? `${receptors} receptor layers screened` : 'No receptor layer loaded'],
    [
      'RESPOND',
      'Response Planning',
      scenarios === null ? 'WAITING' : scenarios.length ? 'COMPLETE' : 'WAITING',
      scenarios?.length ? `${scenarios.length} planning scenario${scenarios.length > 1 ? 's' : ''}` : 'No planning scenario yet',
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

export function Journey({ steps, navigate, page }: { steps: Json[]; navigate: (p: string) => void; page?: string }) {
  return (
    <ol className="journey" aria-label="Investigation journey">
      {steps.map((s, i) => (
        <li key={s.name} className={`journey-step st-${s.state.toLowerCase()} ${page === s.page ? 'here' : ''}`}>
          <button onClick={() => navigate(s.page)} title={`${s.name}: ${s.state.toLowerCase()} — ${s.detail}`}>
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
  const [nbo, setNbo] = useState<Json | null>(null),
    [scenarios, setScenarios] = useState<Json[] | null>(null);
  useEffect(() => {
    if (!a) return;
    setNbo(null);
    setScenarios(null);
    api(`/cases/${a.case_id}/next-observations?run_id=${a.run_id}`).then(setNbo).catch(() => setNbo({ candidates: [] }));
    api<Json[]>(`/cases/${a.case_id}/scenarios?run_id=${a.run_id}`).then(setScenarios).catch(() => setScenarios([]));
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
            Provider health and watched-area activity below come from the backend scheduler. Open Copernicus Watch to inspect
            real catalogue observations, calibrated SAR provenance, acquisition plans and ocean-model coverage.
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
          <p className="fine"><strong>Live Operations</strong> uses external provider state. <strong>Demo Case</strong> uses a synthetic Sentinel-1-compatible scene, fictional vessels and seeded forcing, labelled DEMO throughout.</p>
        </div>
      </>
    );

  const steps = journey(a, live, truth, scenarios, job);
  const lead = a.vessels[0];
  const exposure = a.impact.receptors.find((r: Json) => r.first_overlap_h !== null);
  const topObs = nbo?.candidates?.[0];
  const caseWatches = (live?.watch_areas || []).filter((w: Json) => w.case_id === a.case_id);
  const cat = provider(live, 'sentinel1_catalogue');
  const nearestNext = caseWatches
    .map((w: Json) => w.next_check_at)
    .filter(Boolean)
    .sort()[0];
  const demo = a.source_type !== 'REAL';
  const currentStep = steps.find((s) => s.state === 'ACTIVE') || steps.find((s) => s.state === 'WAITING');
  const spread48 = a.forecast.steps.find((s: Json) => s.hours === 48)?.spread90_km;
  const margins: number[] = (scenarios || [])
    .filter((s) => s.margin_to_target_h !== null && s.margin_to_target_h !== undefined)
    .map((s) => s.margin_to_target_h);

  const priorities: [string, React.ReactNode, React.ReactNode, string][] = [
    [
      'Top investigative lead',
      lead ? <>{lead.name}</> : 'No AIS-tracked vessels',
      lead ? <>Relevance {lead.score}/100 · not a probability of culpability</> : 'AIS data is required before vessel attribution can run.',
      'Vessel Intelligence',
    ],
    [
      'TruthLoop stability',
      truth?.challenged ? <Status state={truth.stability.state} /> : <Status state="NOT_CHALLENGED" />,
      truth?.challenged
        ? truth.stability.reasons[0]
        : 'Run the adversarial challenges before relying on the leading explanation.',
      'TruthLoop',
    ],
    [
      'Next potential exposure',
      exposure ? exposure.name : 'No sampled overlap',
      exposure
        ? <>+{exposure.first_overlap_h} h after observation · {shortTime(overlapTime(a, exposure)!)} · potential, not confirmed</>
        : a.receptors.features.length ? 'Forecast envelope does not reach loaded receptors by +48 h.' : 'No receptor layer loaded for this AOI.',
      'Ecological Exposure',
    ],
    [
      'Next-best observation',
      topObs ? sentence(topObs.target_type) : nbo ? 'None available' : 'Loading…',
      topObs ? <>Priority {topObs.score}/100 · heuristic, not a probability</> : '',
      'Next Observation',
    ],
    [
      'Copernicus coverage',
      caseWatches.length ? `${caseWatches.length} AOI${caseWatches.length > 1 ? 's' : ''} under watch` : 'Not watching this case',
      caseWatches.length ? (
        <>
          Next check <Countdown to={nearestNext} onZero={refreshLive} /> · auto-analyze{' '}
          {caseWatches.some((w: Json) => w.auto_analyze) ? 'on' : 'off'}
        </>
      ) : (
        'Add a next-best-observation AOI to Copernicus Watch.'
      ),
      caseWatches.length ? 'Copernicus Watch' : 'Next Observation',
    ],
    [
      'Response window',
      exposure ? `Modeled onset +${exposure.first_overlap_h} h` : 'No modeled onset',
      scenarios?.length
        ? `${scenarios.length} planning scenario${scenarios.length > 1 ? 's' : ''}` +
          (margins.length ? ` · tightest ready margin ${Math.min(...margins)} h` : '')
        : 'Compare no action against a planning scenario.',
      'Response Planning',
    ],
  ];

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
              <FlaskConical size={15} /> {truth?.challenged ? 'Review challenge' : 'Challenge conclusion'}
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

      <LiveStrip live={live} error={liveError} refresh={refreshLive} navigate={navigate} />

      <div className="overview-grid">
        <div className="overview-map">
          <MaritimeMap
            analysis={a}
            geography={geography}
            selected={selected}
            onSelect={setSelected}
            truth={truth}
            layers={{ aoi: false, ais: true, currents: true, gaps: false }}
            height={560}
            onOpenVessel={onOpenVessel}
          />
          <div className="facts">
            <Metric label="Oil candidate" value={a.spill.area_km2.toFixed(1)} unit="km²" hint={`contrast ${a.spill.contrast_db} dB`} tone="hazard" />
            <Metric label="Origin uncertainty" value={a.origin.radius90_km} unit="km" hint="90% region radius · modeled" tone="model" />
            <Metric label="Forecast spread +48 h" value={spread48 ?? '—'} unit="km" hint="90% envelope · predicted" tone="forecast" />
            <Metric label="Vessels screened" value={a.vessels.length} hint={`${a.dark.gap_count} AIS gap${a.dark.gap_count === 1 ? '' : 's'} observed`} />
          </div>
        </div>
        <aside className="priorities" aria-label="Investigative priorities">
          <h2>Investigative priorities</h2>
          <ul>
            {priorities.map(([name, value, detail, page]) => (
              <li key={name}>
                <button onClick={() => navigate(page)}>
                  <span className="priority-name">{name}</span>
                  <span className="priority-value">{value}</span>
                  {detail && <span className="priority-detail">{detail}</span>}
                  <ChevronRight size={14} className="priority-go" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>

      <section className="journey-wrap" aria-label="Investigation journey">
        <div className="section-head">
          <h2>Investigation journey</h2>
          <p className="section-note">States are computed from this run's data and live monitoring — nothing is marked complete by default.</p>
        </div>
        <Journey steps={steps} navigate={navigate} />
      </section>
    </>
  );
}
