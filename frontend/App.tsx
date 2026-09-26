import { useEffect, useState, ReactNode } from 'react';
import {
  Activity,
  Anchor,
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Database,
  Eye,
  FileText,
  FolderOpen,
  Globe2,
  Layers,
  LoaderCircle,
  Maximize2,
  Network,
  Play,
  Plus,
  Radar,
  RefreshCw,
  Satellite,
  Search,
  Settings2,
  ShieldCheck,
  Ship,
  SlidersHorizontal,
  TriangleAlert,
  Waves,
  X,
} from 'lucide-react';
import { api, post, Json, time, coordinate } from './api';
import MaritimeMap from './MaritimeMap';
import { Workflow, Dossier, GlobalIncidents, Intelligence } from './Intelligence';

const nav = [
  ['Overview', Eye],
  ['Global Map', Globe2],
  ['Satellite Analysis', Satellite],
  ['Drift & Origin', Waves],
  ['AIS Correlation', Ship],
  ['Vessel Ranking', Activity],
  ['SAR-AIS Screening', Radar],
  ['Verification', ShieldCheck],
  ['Ecological Exposure', Waves],
  ['Response Twin', Anchor],
  ['Attention & Alerts', TriangleAlert],
  ['Evidence Graph', Network],
  ['Timeline', Clock3],
  ['Cases & Data', FolderOpen],
  ['Reports', FileText],
  ['Settings', Settings2],
] as const;
function Panel({
  title,
  icon: Icon = Activity,
  extra,
  children,
  className = '',
}: {
  title: string;
  icon?: any;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={'panel ' + className}>
      <div className="panel-heading">
        <h2>
          <Icon size={16} />
          {title}
        </h2>
        {extra}
      </div>
      {children}
    </section>
  );
}
function Stat({ label, value, unit }: { label: string; value: ReactNode; unit?: string }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
    </div>
  );
}
function MiniShape({
  geometry,
  center,
  color = '#53d9bc',
}: {
  geometry: Json;
  center: number[];
  color?: string;
}) {
  const ring = geometry?.coordinates?.[0] || [];
  if (!ring.length || !Array.isArray(ring[0])) return null;
  const scale = 520;
  return (
    <svg className="mini-shape" viewBox="0 0 180 100">
      <defs>
        <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
          <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#244a5e" strokeWidth=".4" />
        </pattern>
      </defs>
      <rect width="180" height="100" fill="url(#grid)" />
      <polygon
        points={ring
          .map(
            (p: number[]) =>
              `${90 + (p[0] - center[0]) * scale},${50 - (p[1] - center[1]) * scale}`,
          )
          .join(' ')}
        fill={color}
        fillOpacity=".18"
        stroke={color}
        strokeWidth="1.2"
      />
      <path d="M84 50h12M90 44v12" stroke="white" />
    </svg>
  );
}
function Spark({ track, field }: { track: Json[]; field: 'sog' | 'cog' }) {
  const start = Date.parse(track[0].time),
    span = Math.max(Date.parse(track[track.length - 1].time) - start, 1);
  const max = Math.max(...track.map((p) => p[field] ?? 0), 1);
  const x = (p: Json) => ((Date.parse(p.time) - start) / span) * 300;
  const y = (p: Json) => 55 - (p[field] / max) * 48;
  return (
    <>
      <svg
        viewBox="0 0 300 60"
        className="spark"
        role="img"
        aria-label={field === 'sog' ? 'Observed AIS speed history' : 'Observed AIS course history'}
      >
        <path d="M0 55H300" stroke="#244050" />
        {track.map((p, i) => {
          if (p[field] === null || p[field] === undefined) return null;
          const prev = track[i - 1];
          return (
            <g key={i}>
              {prev?.[field] != null && Date.parse(p.time) - Date.parse(prev.time) <= 1800000 && (
                <line
                  x1={x(prev)}
                  y1={y(prev)}
                  x2={x(p)}
                  y2={y(p)}
                  stroke="#41c4f4"
                  strokeWidth="1"
                />
              )}
              <circle cx={x(p)} cy={y(p)} r="1" fill="#41c4f4" />
            </g>
          );
        })}
      </svg>
      <p className="micro">
        {time(track[0].time)} to {time(track[track.length - 1].time)} · scale 0–{max.toFixed(1)}{' '}
        {field === 'sog' ? 'knots' : 'degrees'}. Gaps over 30 minutes and missing values are not
        connected.
      </p>
    </>
  );
}

export default function App() {
  const [page, setPage] = useState('Overview'),
    [cases, setCases] = useState<Json[]>([]),
    [caseId, setCaseId] = useState(''),
    [a, setA] = useState<Json | null>(null),
    [geography, setGeography] = useState<Json | null>(null),
    [selected, setSelected] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [job, setJob] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [modal, setModal] = useState<string | null>(null),
    [query, setQuery] = useState(''),
    [results, setResults] = useState<Json[]>([]),
    [focus, setFocus] = useState<number[] | null>(null),
    [counter, setCounter] = useState<Json | null>(null),
    [hour, setHour] = useState(24),
    [presentation, setPresentation] = useState(false),
    [windage, setWindage] = useState(0.03),
    [audit, setAudit] = useState<Json[]>([]),
    [inventory, setInventory] = useState<Json | null>(null),
    [health, setHealth] = useState<Json | null>(null);
  const refreshCases = async () => {
    const list = await api<Json[]>('/cases');
    setCases(list);
    return list;
  };
  const load = async (id: string) => {
    setCaseId(id);
    setA(null);
    setCounter(null);
    setSelected(null);
    const c = await api('/cases/' + id);
    if (c.latest_run) {
      const data = await api('/cases/' + id + '/analysis');
      setA(data);
      setSelected(data.vessels[0]?.mmsi);
      setWindage(data.origin.parameters.windage);
    }
  };
  useEffect(() => {
    Promise.all([refreshCases(), api('/geography'), api('/health')])
      .then(([list, g, h]) => {
        setGeography(g);
        setHealth(h);
        if (list.length) load(list[0].id).catch((e) => setError(e.message));
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (query.length < 2) {
      setResults([]);
      return;
    }
    let cancel = false;
    const timer = setTimeout(
      () =>
        api<Json[]>('/search?q=' + encodeURIComponent(query) + (caseId ? '&case_id=' + caseId : ''))
          .then((v) => {
            if (!cancel) setResults(v);
          })
          .catch(() => {}),
      250,
    );
    return () => {
      cancel = true;
      clearTimeout(timer);
    };
  }, [query, caseId]);
  useEffect(() => {
    if (!job || job.state !== 'RUNNING') return;
    const interval = setInterval(async () => {
      try {
        const status = await api('/runs/' + job.run_id);
        setJob({ ...status, run_id: job.run_id });
        if (status.state === 'COMPLETE') {
          const data = await api('/cases/' + caseId + '/analysis');
          setA(data);
          setSelected(data.vessels[0]?.mmsi);
          setBusy(false);
          setNotice('Investigation complete. All panels reflect this analysis run.');
          refreshCases();
        } else if (status.state === 'FAILED') {
          setError(status.error);
          setBusy(false);
        }
      } catch (e) {
        setError((e as Error).message);
        setBusy(false);
      }
    }, 800);
    return () => clearInterval(interval);
  }, [job?.run_id, job?.state, caseId]);
  useEffect(() => {
    if (page === 'Reports' && caseId)
      api<Json[]>('/cases/' + caseId + '/audit')
        .then(setAudit)
        .catch((e) => setError(e.message));
    if (page === 'Cases & Data')
      api('/datasets')
        .then(setInventory)
        .catch((e) => setError(e.message));
  }, [page, caseId, a?.run_id]);
  const action = async (fn: () => Promise<void>) => {
    setError('');
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const createDemo = () =>
    action(async () => {
      setBusy(true);
      const c = await post('/cases/demo');
      await refreshCases();
      await load(c.id);
      setGeography(await api('/geography'));
      setBusy(false);
      setNotice('Demo inputs created. Run the full investigation to compute results.');
    });
  const run = () =>
    action(async () => {
      if (!caseId) return;
      setBusy(true);
      setCounter(null);
      const j = await post('/cases/' + caseId + '/run', { windage });
      setJob(j);
      setNotice('');
    });
  const remove = (mmsi?: string) =>
    action(async () => {
      const primary = mmsi ? a?.vessels.find((v: Json) => v.mmsi === mmsi) : a?.vessels[0];
      if (!primary) return;
      setCounter(
        await post('/cases/' + caseId + '/counterfactual', { exclude_mmsi: primary.mmsi }),
      );
      setNotice(`${primary.name} excluded from this view. Original evidence remains preserved.`);
    });
  const ranking = counter?.ranking || a?.attribution.ranking || [];
  const vessel = a?.vessels.find((v: Json) => v.mmsi === selected) || a?.vessels[0];
  const switchPage = (p: string) => {
    setPage(p);
    setNotice('');
  };
  const badges = (
    <span className="badge amber">
      {a?.source_type === 'REAL' ? 'EXPERIMENTAL SCREENING' : 'DEMO DATA'}
    </span>
  );
  const vesselDetails = vessel && (
    <>
      <div className="vessel-identity">
        <div className="ship-avatar">
          <Ship size={39} />
        </div>
        <div>
          <strong>{vessel.name}</strong>
          <span>MMSI {vessel.mmsi}</span>
          <span className="badge red">Investigative candidate</span>
        </div>
      </div>
      <div className="evidence-columns">
        <div>
          <h4>Observed behavior</h4>
          {vessel.supporting.map((s: string, i: number) => (
            <p className="evidence-line" key={i}>
              <TriangleAlert size={13} />
              {s}
            </p>
          ))}
          <p className="muted">{vessel.contradicting[0]}</p>
          <button
            className="text-button"
            onClick={() => {
              setPage('AIS Correlation');
              setFocus(vessel.position);
            }}
          >
            View full AIS track <ArrowRight size={13} />
          </button>
        </div>
        <div>
          <h4>
            Relevance contributions <span>/ 100</span>
          </h4>
          {vessel.components.map((c: Json) => (
            <div className="component" key={c.name}>
              <span>{c.name}</span>
              <div>
                <i style={{ width: c.value + '%' }} />
              </div>
              <b>{c.contribution.toFixed(1)}</b>
            </div>
          ))}
          <p className="micro">Investigative relevance score · not a probability of culpability.</p>
        </div>
      </div>
    </>
  );
  return (
    <div className={'app ' + (presentation ? 'presentation' : '')}>
      <header>
        <a className="brand" onClick={() => switchPage('Overview')}>
          <div className="brand-symbol">
            <Eye size={35} />
          </div>
          <div>
            OCEAN<span>-EYE</span>
            <small>MARITIME FORENSIC INTELLIGENCE</small>
          </div>
        </a>
        <div className="header-search">
          <Search size={16} />
          <input
            aria-label="Global search"
            placeholder="Search country, sea, vessel, MMSI or coordinates…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <kbd>⌕</kbd>
          {results.length > 0 && (
            <div className="search-results">
              {results.map((r, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setFocus(r.coordinates);
                    setQuery('');
                    setPage('Global Map');
                    if (r.kind === 'vessel') setSelected(r.id);
                  }}
                >
                  <span>{r.name}</span>
                  <small>{r.kind}</small>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="system-status">
          <span className="live-dot" />
          {health ? 'System online' : 'Connecting'}
        </div>
        <button
          className="icon-button"
          title="Presentation mode"
          onClick={() => setPresentation(!presentation)}
        >
          <Maximize2 size={18} />
        </button>
        <div className="analyst-avatar">AN</div>
      </header>
      <aside>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {nav.map(([name, Icon]) => (
            <button
              key={name}
              className={page === name ? 'active' : ''}
              onClick={() => switchPage(name)}
            >
              <Icon size={17} />
              <span>{name}</span>
              {page === name && <ChevronRight size={14} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <ShieldCheck size={22} />
          <strong>Local investigation</strong>
          <span>Evidence stays on this computer</span>
          <small>OCEAN-EYE v1.0</small>
        </div>
      </aside>
      <main>
        <div className="page-heading">
          <div>
            <div className="eyebrow">
              MARITIME INTELLIGENCE <ChevronRight size={11} /> {page.toUpperCase()}
            </div>
            <h1>{page === 'Overview' ? 'Investigation command center' : page}</h1>
            <p>{a ? a.name : 'From satellite observation to maritime accountability.'}</p>
          </div>
          <div className="heading-actions">
            {badges}
            <button onClick={() => setModal('import')} className="secondary">
              <Plus size={15} /> Import case
            </button>
            <button onClick={caseId ? run : createDemo} disabled={busy} className="primary">
              {busy ? <LoaderCircle size={16} className="spin" /> : <Play size={15} />}{' '}
              {busy ? 'Processing' : caseId ? 'Run full investigation' : 'Open demo investigation'}
            </button>
          </div>
        </div>
        {error && (
          <div className="banner error" role="alert">
            <TriangleAlert size={17} />
            <span>{error}</span>
            <button onClick={() => setError('')}>
              <X size={15} />
            </button>
          </div>
        )}
        {notice && (
          <div className="banner">
            <Check size={15} />
            {notice}
            <button onClick={() => setNotice('')}>
              <X size={15} />
            </button>
          </div>
        )}
        {job?.state === 'RUNNING' && (
          <div className="progress-banner">
            <LoaderCircle className="spin" size={18} />
            <div>
              <strong>Investigation in progress</strong>
              <span>{job.stage || 'Preparing input snapshots'}</span>
            </div>
            <div className="indeterminate" />
          </div>
        )}
        {a && (
          <div className="case-strip">
            <strong>{a.data_label}</strong>
            <span>
              <FolderOpen size={14} /> CASE {caseId.slice(0, 8).toUpperCase()}
            </span>
            <span>
              <Satellite size={14} />
              {a.spill.sensor}
            </span>
            <span>
              <Clock3 size={14} />
              {time(a.observation_time)}
            </span>
            <span className="hash" title={a.analysis_hash}>
              <ShieldCheck size={14} /> {a.analysis_hash.slice(0, 12)}
            </span>
            <button onClick={() => setModal('provenance')}>
              View provenance <ArrowRight size={12} />
            </button>
          </div>
        )}
        {page === 'Global Map' && (
          <GlobalIncidents
            geography={geography}
            select={(id) =>
              action(async () => {
                await load(id);
                setPage('Overview');
              })
            }
          />
        )}
        {a && <Workflow navigate={setPage} />}
        {a && page === 'Overview' && (
          <div className="classification-banner">
            <strong>OIL CANDIDATE — CLASSIFICATION PENDING</strong>
            <span>
              {a.spill.area_km2.toFixed(2)} km² · {coordinate(a.spill.centroid)} ·{' '}
              {time(a.observation_time)}
            </span>
            <span>Origin is a modeled uncertainty region. Pollutant identity is UNKNOWN.</span>
            <button onClick={() => setPage('Attention & Alerts')}>
              Review attention and response priorities
            </button>
          </div>
        )}
        {!a && !busy && page !== 'Global Map' && page !== 'Cases & Data' && page !== 'Settings' && (
          <section className="welcome">
            <div className="welcome-radar">
              <Radar size={64} />
            </div>
            <span className="eyebrow">CONNECTED FORENSIC WORKFLOW</span>
            <h2>
              Every observation has a story.
              <br />
              Follow the evidence.
            </h2>
            <p>
              Process a SAR scene, reconstruct the spill origin, correlate vessel activity, and
              export an auditable investigation.
            </p>
            <div className="workflow-pills">
              {['Satellite', 'Oil candidate', 'Origin', 'AIS', 'Attribution', 'Report'].map(
                (s, i) => (
                  <span key={s}>
                    {i > 0 && <ArrowRight size={12} />} {s}
                  </span>
                ),
              )}
            </div>
            <button className="primary" onClick={caseId ? run : createDemo}>
              <Play size={15} />
              {caseId ? 'Run this investigation' : 'Create reproducible demo'}
            </button>
            <p className="micro">
              20 fictional vessels · georeferenced synthetic SAR · deterministic analysis
            </p>
          </section>
        )}
        {a && (page === 'Overview' || page === 'AIS Correlation' || page === 'Drift & Origin') && (
          <>
            <div className="dashboard-grid">
              <div className="main-column">
                <Panel
                  title="Investigation map"
                  icon={Globe2}
                  extra={
                    <span className="micro">
                      {a.vessels.length} VESSELS · {a.spill.component_count} CANDIDATE
                    </span>
                  }
                >
                  <MaritimeMap
                    analysis={a}
                    geography={geography}
                    selected={selected}
                    onSelect={setSelected}
                    focus={focus}
                    global={false}
                    forecastHour={hour}
                  />
                </Panel>
                <Panel
                  title="Backtracking simulation"
                  icon={Waves}
                  extra={<span className="micro">SPILL ORIGIN · CONDITIONAL ENSEMBLE</span>}
                >
                  <div className="backtrack">
                    {[...a.origin.snapshots].reverse().map((s: Json, i: number) => (
                      <button
                        className="time-frame"
                        key={i}
                        onClick={() => {
                          setFocus(s.center);
                          setModal('origin');
                        }}
                      >
                        <strong>T − {s.hours_before}h</strong>
                        <MiniShape geometry={s.geometry} center={a.spill.centroid} />
                        <span>{i === 0 ? 'Possible origin zone' : 'Modeled particle spread'}</span>
                      </button>
                    ))}
                    <button className="time-frame" onClick={() => setPage('Satellite Analysis')}>
                      <strong>T · Observed</strong>
                      <img src={a.assets + '/mask.png'} alt="Computed segmentation mask" />
                      <span>Detected candidate</span>
                    </button>
                  </div>
                </Panel>
                <Panel
                  title="AIS vessel behavior analysis"
                  icon={Ship}
                  extra={
                    <button className="text-button" onClick={() => setModal('vessel')}>
                      Inspect evidence <ArrowRight size={13} />
                    </button>
                  }
                >
                  {vesselDetails}
                </Panel>
              </div>
              <div className="right-column">
                <Panel
                  title="Oil candidate analysis"
                  icon={Radar}
                  extra={<span className="badge red">CANDIDATE</span>}
                >
                  <div className="spill-overview">
                    <div className="sar-preview">
                      <img
                        src={a.assets + '/satellite.png'}
                        alt="Synthetic SAR image processed by the detector"
                      />
                      <img className="mask-overlay" src={a.assets + '/mask.png'} alt="" />
                      <span>SAR / VV</span>
                    </div>
                    <div>
                      <Stat label="Surface area" value={a.spill.area_km2.toFixed(2)} unit="km²" />
                      <Stat label="Perimeter" value={a.spill.perimeter_km.toFixed(1)} unit="km" />
                      <Stat label="Contrast" value={a.spill.contrast_db} unit="dB" />
                      <Stat label="Oil type" value="Unconfirmed" />
                    </div>
                  </div>
                  <button className="panel-link" onClick={() => setPage('Satellite Analysis')}>
                    View segmentation & method <ArrowRight size={13} />
                  </button>
                </Panel>
                <Panel
                  title="Drift & origin analysis"
                  icon={Waves}
                  extra={<span className="badge green">90% REGION</span>}
                >
                  <div className="origin-preview">
                    <MiniShape geometry={a.origin.geometry} center={a.origin.centroid} />
                    <div>
                      <span className="muted">Modeled origin</span>
                      <strong>{coordinate(a.origin.centroid)}</strong>
                      <span className="muted">Release window · assumed</span>
                      <b>
                        {new Date(a.origin.release_window[0]).toISOString().slice(11, 16)}–
                        {new Date(a.origin.release_window[1]).toISOString().slice(11, 16)} UTC
                      </b>
                      <small>
                        {time(a.origin.release_window[0]).split(',')[0]} · assumed window
                      </small>
                    </div>
                  </div>
                  <div className="panel-foot">
                    Uncertainty radius <b>{a.origin.radius90_km} km</b>
                    <button onClick={() => setModal('origin')}>
                      Details <ChevronRight size={12} />
                    </button>
                  </div>
                </Panel>
                <Panel
                  title="Environmental forecast"
                  icon={Layers}
                  extra={
                    <span className="badge amber">
                      {a.impact.receptors.some((r: Json) => r.first_overlap_h)
                        ? 'EXPOSURE'
                        : 'SCREENING'}
                    </span>
                  }
                >
                  <div className="forecast-tabs">
                    {[6, 12, 24, 48].map((h) => (
                      <button
                        className={hour === h ? 'chosen' : ''}
                        onClick={() => setHour(h)}
                        key={h}
                      >
                        +{h}h
                      </button>
                    ))}
                  </div>
                  <Stat
                    label="Particle spread (90%)"
                    value={a.forecast.steps.find((s: Json) => s.hours === hour)?.spread90_km}
                    unit="km"
                  />
                  {a.impact.receptors.slice(0, 4).map((r: Json) => (
                    <div className="receptor" key={r.name}>
                      <span>
                        <i />
                        {r.name}
                      </span>
                      <b>{r.first_overlap_h ? `+${r.first_overlap_h}h overlap` : 'No overlap'}</b>
                    </div>
                  ))}
                  <p className="micro padded">
                    Forecast envelope overlap; not a calibrated impact probability.
                  </p>
                </Panel>
                <Panel
                  title="Vessel attribution & ranking"
                  icon={Activity}
                  extra={<span className="micro">INVESTIGATIVE RELEVANCE SCORE</span>}
                >
                  <div className="ranking-list">
                    {ranking.slice(0, 4).map((v: Json, i: number) => (
                      <button
                        key={v.mmsi}
                        className={'rank-row ' + (selected === v.mmsi ? 'selected' : '')}
                        onClick={() => setSelected(v.mmsi)}
                      >
                        <span className={'rank-number rank-' + i}>{i + 1}</span>
                        <div>
                          <strong>{v.name}</strong>
                          <small>{v.mmsi}</small>
                        </div>
                        <div className="rank-score">
                          <b>{v.score.toFixed(1)}</b>
                          <i>
                            <em style={{ width: v.score + '%' }} />
                          </i>
                        </div>
                        <ChevronRight size={14} />
                      </button>
                    ))}
                  </div>
                  <button className="panel-link" onClick={() => switchPage('Vessel Ranking')}>
                    Compare all {ranking.length} candidates <ArrowRight size={13} />
                  </button>
                </Panel>
              </div>
            </div>
            {page === 'Drift & Origin' && (
              <Panel title="Environmental sensitivity" icon={SlidersHorizontal}>
                <div className="padded">
                  <label>
                    Windage coefficient: {(windage * 100).toFixed(1)}%{' '}
                    <input
                      type="range"
                      min="0"
                      max="0.1"
                      step="0.005"
                      value={windage}
                      onChange={(e) => setWindage(+e.target.value)}
                    />
                  </label>
                  <button className="secondary" disabled={busy} onClick={run}>
                    Recompute entire case
                  </button>
                  <p className="muted">
                    Creates a new immutable run using the changed coefficient. Results update after
                    processing completes.
                  </p>
                </div>
              </Panel>
            )}
          </>
        )}
        {a && page === 'Satellite Analysis' && (
          <div className="analysis-layout">
            <Panel title="Satellite preprocessing & segmentation" icon={Satellite}>
              <div className="image-comparison">
                <figure>
                  <img src={a.assets + '/satellite.png'} alt="Input SAR" />
                  <figcaption>Input · calibrated Sigma0</figcaption>
                </figure>
                <figure>
                  <img src={a.assets + '/processed.png'} alt="Median-filtered SAR" />
                  <figcaption>Speckle screening · median filter</figcaption>
                </figure>
                <figure>
                  <img src={a.assets + '/mask.png'} alt="Computed candidate mask" />
                  <figcaption>Computed segmentation mask</figcaption>
                </figure>
              </div>
              <div className="metric-row">
                <Stat label="Area" value={a.spill.area_km2} unit="km²" />
                <Stat label="Candidate pixels" value={a.spill.candidate_pixels} />
                <Stat label="Components" value={a.spill.component_count} />
                <Stat label="Threshold" value={a.spill.threshold_db} unit="dB" />
              </div>
            </Panel>
            <Panel title="Model & image provenance" icon={ShieldCheck}>
              <div className="padded">
                <h3>{a.spill.method}</h3>
                <p className="muted">
                  Rejected weak-damping regions:{' '}
                  {a.spill.look_alike_screening?.rejected_regions.length ?? 'Rerun to calculate'}.
                  This screen is not a validated look-alike classifier.
                </p>
                <p>
                  CRS: {a.spill.crs} · Resolution: {a.spill.resolution.join(' × ')} m
                </p>
                <p>{a.spill.calibration}</p>
                {a.spill.limitations.map((s: string) => (
                  <p className="limitation" key={s}>
                    <TriangleAlert size={14} />
                    {s}
                  </p>
                ))}
                <a className="secondary" href={a.assets + '/mask.tif'}>
                  Download georeferenced mask <ArrowDownToLine size={14} />
                </a>
              </div>
            </Panel>
          </div>
        )}
        {a && page === 'Vessel Ranking' && (
          <>
            <div className="toolbar">
              <p>INVESTIGATIVE RELEVANCE SCORE — Not a probability of culpability.</p>
              <button className="secondary" onClick={() => setModal('vessel')}>
                Why this vessel?
              </button>
              {counter ? (
                <button className="secondary" onClick={() => setCounter(null)}>
                  <RefreshCw size={14} /> Restore all candidates
                </button>
              ) : (
                <button className="secondary" onClick={() => remove()}>
                  Remove primary candidate
                </button>
              )}
            </div>
            <Panel title="Compare vessel evidence" icon={Ship}>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Vessel / MMSI</th>
                      <th>Investigative relevance score</th>
                      <th>Origin distance</th>
                      <th>Minimum speed</th>
                      <th>Course change</th>
                      <th>AIS gaps</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {ranking.map((v: Json, i: number) => (
                      <tr key={v.mmsi} className={v.mmsi === selected ? 'highlight' : ''}>
                        <td>{i + 1}</td>
                        <td>
                          <strong>{v.name}</strong>
                          <small>{v.mmsi}</small>
                        </td>
                        <td>
                          <b className="cyan">{v.score} / 100</b>
                        </td>
                        <td>{v.nearest_km} km</td>
                        <td>{v.minimum_speed_kn} kn</td>
                        <td>{v.course_change_deg}°</td>
                        <td>{v.gaps.length}</td>
                        <td>
                          <button
                            className="text-button"
                            onClick={() => {
                              setSelected(v.mmsi);
                              setModal('vessel');
                            }}
                          >
                            Evidence <ChevronRight size={12} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <div className="hypotheses">
              {a.attribution.hypotheses.map((h: Json) => (
                <Panel key={h.name} title={h.name} icon={Radar}>
                  <p className="padded muted">{h.status}</p>
                </Panel>
              ))}
            </div>
          </>
        )}
        {a && page === 'SAR-AIS Screening' && (
          <>
            <div className="metric-row">
              <Stat label="Bright SAR returns" value={a.dark.sar_returns.length} />
              <Stat label="Unmatched returns" value={a.dark.unmatched_returns} />
              <Stat label="AIS reporting gaps" value={a.dark.gap_count} />
              <Stat label="Assessment" value="Unresolved" />
            </div>
            <Panel title="SAR–AIS cross-validation" icon={Radar}>
              <div className="padded">
                <p className="limitation">
                  <TriangleAlert size={16} />
                  {a.dark.limitations}
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>SAR return</th>
                      <th>Nearest MMSI</th>
                      <th>Distance</th>
                      <th>Time offset</th>
                      <th>Match</th>
                    </tr>
                  </thead>
                  <tbody>
                    {a.dark.sar_returns.map((r: Json) => (
                      <tr key={r.id}>
                        <td>{r.id}</td>
                        <td>{r.nearest_mmsi}</td>
                        <td>{r.distance_km} km</td>
                        <td>{r.ais_time_offset_min} min</td>
                        <td>{r.matched ? 'Spatial/temporal match' : 'Unmatched'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="Unobserved AIS intervals" icon={Clock3}>
              <div className="padded">
                {a.vessels
                  .filter((v: Json) => v.gaps.length)
                  .map((v: Json) => (
                    <div key={v.mmsi}>
                      <h3>{v.name}</h3>
                      {v.gaps.map((g: Json, i: number) => (
                        <p key={i}>
                          {time(g.start)} → {time(g.end)} · {g.minutes} min
                          <br />
                          <span className="muted">{g.interpretation}</span>
                        </p>
                      ))}
                    </div>
                  ))}
              </div>
            </Panel>
          </>
        )}
        {a &&
          [
            'Evidence Graph',
            'Verification',
            'Ecological Exposure',
            'Response Twin',
            'Attention & Alerts',
          ].includes(page) && (
            <Intelligence
              key={a.run_id}
              a={a}
              page={page}
              geography={geography}
              onVessel={(id) => {
                setSelected(id);
                setModal('vessel');
              }}
            />
          )}
        {a && page === 'Timeline' && (
          <Panel title="Forensic investigation timeline" icon={Clock3}>
            <div className="timeline">
              {a.timeline.map((event: Json, i: number) => (
                <div key={i}>
                  <span className={'timeline-dot ' + event.type} />
                  <time>{time(event.time)}</time>
                  <h3>{event.event}</h3>
                  <span className="badge">{event.type}</span>
                </div>
              ))}
            </div>
          </Panel>
        )}
        {page === 'Cases & Data' && (
          <>
            <div className="toolbar">
              <p>Local cases and importable source datasets</p>
              <button className="secondary" disabled={busy} onClick={createDemo}>
                <Plus size={15} /> New demo case
              </button>
              <button className="primary" onClick={() => setModal('import')}>
                Import investigation
              </button>
            </div>
            <Panel title="Case management" icon={FolderOpen}>
              <table>
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Created</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <strong>{c.name}</strong>
                        <small>{c.id.slice(0, 8)}</small>
                      </td>
                      <td>{time(c.created)}</td>
                      <td>
                        <span className="badge amber">{c.source_type}</span>
                      </td>
                      <td>{c.latest_run ? 'Analyzed' : 'Ready to run'}</td>
                      <td>
                        <button
                          disabled={busy}
                          className="text-button"
                          onClick={() =>
                            action(async () => {
                              await load(c.id);
                              setPage('Overview');
                            })
                          }
                        >
                          Open <ArrowRight size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <div className="hypotheses">
              {inventory?.sources.map((s: Json) => (
                <Panel key={s.name} title={s.name} icon={Database}>
                  <div className="padded">
                    <p className="muted">{s.note || s.schema}</p>
                    <a href={s.url} target="_blank" rel="noreferrer">
                      Open source <ArrowRight size={13} />
                    </a>
                  </div>
                </Panel>
              ))}
            </div>
            <Panel title="Global reference layers" icon={Globe2}>
              <div className="padded">
                <p>
                  {geography?.features.length || 0} imported geographic features. Import WGS84
                  GeoJSON for seas, oceans, ports, EEZs, protected areas, corridors, fisheries or
                  infrastructure.
                </p>
                <button className="secondary" onClick={() => setModal('geography')}>
                  Import geographic layer
                </button>
                {a && a.geographic_context?.length > 0 && (
                  <p className="muted">
                    Current scene: {a.geographic_context.map((c: Json) => c.name).join(' · ')}
                  </p>
                )}
                <p className="micro">
                  Natural Earth countries provide a cartographic basemap. Legal maritime boundaries
                  require a separately sourced EEZ layer.
                </p>
              </div>
            </Panel>
          </>
        )}
        {a && page === 'Reports' && (
          <>
            <div className="report-hero">
              <div className="report-icon">
                <FileText size={42} />
              </div>
              <div>
                <span className="eyebrow">AUDITABLE EVIDENCE PACKAGE</span>
                <h2>One investigation. Every source.</h2>
                <p>
                  Measured geometry, ranked evidence, uncertainty, input snapshots and SHA-256
                  checksums.
                </p>
              </div>
              <div>
                <a className="primary" href={a.assets + '/report.pdf'}>
                  <ArrowDownToLine size={16} /> Forensic PDF
                </a>
                <a className="secondary" href={a.assets + '/evidence.zip'}>
                  <ArrowDownToLine size={16} /> Full evidence ZIP
                </a>
                <a
                  className="secondary"
                  href={`/api/v1/cases/${a.case_id}/response-evidence?run_id=${a.run_id}`}
                >
                  <ArrowDownToLine size={16} /> Evidence + response scenarios ZIP
                </a>
              </div>
            </div>
            <Panel title="Scientific traceability" icon={ShieldCheck}>
              <div className="padded">
                <p className="hash-text">Analysis hash: {a.analysis_hash}</p>
                {a.provenance.inputs.map((s: Json) => (
                  <div className="provenance-row" key={s.role}>
                    <strong>{s.role}</strong>
                    <span>{s.name}</span>
                    <span className="badge">{s.source_type}</span>
                    <code>{s.sha256}</code>
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="Chain of custody" icon={Clock3}>
              <div className="audit-list">
                {audit.map((e) => (
                  <div key={e.seq}>
                    <span>{time(e.time)}</span>
                    <strong>{e.action.replaceAll('_', ' ')}</strong>
                    <code title={e.hash}>{e.hash.slice(0, 18)}…</code>
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="Investigation limitations" icon={TriangleAlert}>
              <div className="padded">
                {a.limitations.map((s: string) => (
                  <p className="limitation" key={s}>
                    <TriangleAlert size={14} />
                    {s}
                  </p>
                ))}
              </div>
            </Panel>
          </>
        )}
        {page === 'Settings' && (
          <div className="analysis-layout">
            <Panel title="Analysis configuration" icon={SlidersHorizontal}>
              <div className="padded">
                <label>
                  Wind contribution: {(windage * 100).toFixed(1)}%
                  <input
                    type="range"
                    min="0"
                    max=".1"
                    step=".005"
                    value={windage}
                    onChange={(e) => setWindage(+e.target.value)}
                  />
                </label>
                <p className="muted">
                  This coefficient takes effect on the next investigation run. Original results
                  remain versioned.
                </p>
                <button className="primary" disabled={!caseId || busy} onClick={run}>
                  Apply & recompute
                </button>
              </div>
            </Panel>
            <Panel title="System capabilities" icon={Database}>
              <div className="padded">
                {health &&
                  Object.entries(health).map(([k, v]) => (
                    <Stat key={k} label={k.replaceAll('_', ' ')} value={String(v)} />
                  ))}
                <p className="micro">
                  Local single-user application. Authentication and production job infrastructure
                  are deployment extensions.
                </p>
              </div>
            </Panel>
          </div>
        )}
        <footer>
          <div>
            <Waves size={18} />
            <strong>OCEAN-EYE</strong>
            <span>DETECT. RECONSTRUCT. ATTRIBUTE. PROTECT.</span>
          </div>
          <span>Explainable evidence · Visible uncertainty · Local by design</span>
        </footer>
      </main>
      {modal && (
        <div className="modal-backdrop" onClick={() => setModal(null)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={modal}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-heading">
              <h2>
                {modal === 'vessel'
                  ? 'Why this vessel?'
                  : modal === 'import'
                    ? 'Import investigation inputs'
                    : modal === 'geography'
                      ? 'Import geographic layer'
                      : modal === 'origin'
                        ? 'Origin model & uncertainty'
                        : 'Scientific provenance'}
              </h2>
              <button
                className="icon-button"
                aria-label="Close dialog"
                onClick={() => setModal(null)}
              >
                <X size={19} />
              </button>
            </div>
            {modal === 'vessel' && vessel && (
              <div className="padded">
                <Dossier a={a!} v={vessel} />
                <h3>Speed over time</h3>
                <Spark track={vessel.track} field="sog" />
                <h3>Course over time</h3>
                <Spark track={vessel.track} field="cog" />
                <p className="micro">
                  Chronological AIS speed observations in knots. Missing intervals are not directly
                  observed.
                </p>
                <h3>Contradicting evidence</h3>
                {vessel.contradicting.map((s: string) => (
                  <p key={s} className="limitation">
                    <TriangleAlert size={14} />
                    {s}
                  </p>
                ))}
                <button
                  className="secondary"
                  onClick={() => {
                    remove(vessel.mmsi);
                    setModal(null);
                    setPage('Vessel Ranking');
                  }}
                >
                  Exclude this candidate & rerank
                </button>
              </div>
            )}
            {modal === 'origin' && a && (
              <div className="padded">
                <Stat label="Modeled origin" value={coordinate(a.origin.centroid)} />
                <Stat label="Conditional 90% radius" value={a.origin.radius90_km} unit="km" />
                <p>{a.origin.uncertainty}</p>
                <p>{a.origin.release_window_basis}</p>
                <pre>{JSON.stringify(a.origin.parameters, null, 2)}</pre>
              </div>
            )}
            {modal === 'provenance' && a && (
              <div className="padded">
                <pre>{JSON.stringify(a.provenance, null, 2)}</pre>
                <h3>Data quality</h3>
                <pre>{JSON.stringify(a.quality, null, 2)}</pre>
              </div>
            )}
            {modal === 'import' && (
              <ImportForm
                onDone={async (id) => {
                  setModal(null);
                  await refreshCases();
                  await load(id);
                  setPage('Overview');
                }}
              />
            )}
            {modal === 'geography' && (
              <GeographyForm
                onDone={async () => {
                  setGeography(await api('/geography'));
                  setModal(null);
                }}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
function ImportForm({ onDone }: { onDone: (id: string) => Promise<void> }) {
  const [working, setWorking] = useState(false),
    [error, setError] = useState('');
  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setWorking(true);
    setError('');
    const form = new FormData(e.currentTarget);
    const metadata = {
      name: form.get('name'),
      source_type: form.get('source_type'),
      observation_time: new Date(String(form.get('observation')) + 'Z').toISOString(),
      release_window: [
        new Date(String(form.get('start')) + 'Z').toISOString(),
        new Date(String(form.get('end')) + 'Z').toISOString(),
      ],
      radiometry: form.get('radiometry'),
    };
    form.set('metadata', JSON.stringify(metadata));
    try {
      const result = await api('/cases/import', { method: 'POST', body: form });
      await onDone(result.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  };
  return (
    <form className="import-form" onSubmit={submit}>
      <p className="muted">
        Upload calibrated, georeferenced SAR, a MarineCadastre-compatible CSV and time-indexed
        environmental JSON. All times below are UTC.
      </p>
      <label>
        Case name
        <input name="name" required defaultValue="Imported maritime investigation" />
      </label>
      <div className="form-grid">
        <label>
          Source type
          <select name="source_type">
            <option value="SYNTHETIC">Synthetic / demonstration</option>
            <option value="REAL">Real satellite and AIS inputs</option>
          </select>
        </label>
        <label>
          Radiometry
          <select name="radiometry">
            <option value="sigma0_db">Sigma0 in decibels</option>
            <option value="sigma0_linear">Sigma0 linear</option>
          </select>
        </label>
      </div>
      <label>
        SAR observation (UTC)
        <input type="datetime-local" name="observation" required defaultValue="2025-08-12T14:20" />
      </label>
      <div className="form-grid">
        <label>
          Assumed release from
          <input type="datetime-local" name="start" required defaultValue="2025-08-11T20:00" />
        </label>
        <label>
          Assumed release until
          <input type="datetime-local" name="end" required defaultValue="2025-08-11T23:00" />
        </label>
      </div>
      <label>
        Satellite GeoTIFF
        <input type="file" name="satellite" accept=".tif,.tiff" required />
      </label>
      <label>
        AIS CSV
        <input type="file" name="ais_file" accept=".csv" required />
      </label>
      <label>
        Environmental forcing JSON
        <input type="file" name="environment" accept=".json" required />
      </label>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <button className="primary" disabled={working}>
        {working ? 'Validating inputs…' : 'Create case from uploads'}
      </button>
    </form>
  );
}
function GeographyForm({ onDone }: { onDone: () => Promise<void> }) {
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  return (
    <form
      className="import-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await api('/geography/import', { method: 'POST', body: new FormData(e.currentTarget) });
          await onDone();
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        WGS84 GeoJSON
        <input type="file" name="file" accept=".json,.geojson" required />
      </label>
      <label>
        Source / publisher
        <input name="source" required />
      </label>
      <label>
        Dataset version
        <input name="version" required />
      </label>
      <label>
        Layer kind
        <select name="kind">
          {[
            'country',
            'sea',
            'ocean',
            'port',
            'eez',
            'coastline',
            'protected_area',
            'coral',
            'mangrove',
            'seagrass',
            'habitat',
            'species_range',
            'sensitive_ecosystem',
            'fishery',
            'infrastructure',
            'shipping_corridor',
          ].map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
      </label>
      <label>
        Source type
        <select name="source_type">
          <option>REAL</option>
          <option>SYNTHETIC</option>
        </select>
      </label>
      {error && <p className="error-text">{error}</p>}
      <button className="primary" disabled={busy}>
        {busy ? 'Importing…' : 'Validate & import layer'}
      </button>
    </form>
  );
}
