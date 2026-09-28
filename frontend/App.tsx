import { ReactNode, useEffect, useState } from 'react';
import {
  Bell,
  Clock3,
  Compass,
  FileText,
  FlaskConical,
  Fingerprint,
  FolderOpen,
  Leaf,
  LifeBuoy,
  LoaderCircle,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Satellite,
  SatelliteDish,
  ScanSearch,
  Search,
  Settings2,
  Ship,
  Waves,
  X,
} from 'lucide-react';
import { Json, api, post } from './api';
import { ClockContext, StateBlock, Status, useTicker } from './ui';
import { NewObservationToast, useLiveStatus } from './LiveData';
import Overview from './Overview';
import TruthLoop from './TruthLoop';
import Vessels from './Vessels';
import EvidenceGraph from './EvidenceGraph';
import CopernicusWatch, { NextObservation } from './Copernicus';
import { Alerts, Ecology, ResponsePlanning } from './Intelligence';
import {
  CasesData,
  DriftOrigin,
  GeographyForm,
  ImportForm,
  Provenance,
  Reports,
  SatelliteAnalysis,
  Settings,
  Timeline,
} from './Pages';

type Icon = typeof Compass;
const NAV: [string, [string, Icon][]][] = [
  ['Command', [['Overview', Compass], ['Alerts', Bell]]],
  ['Observe', [['Copernicus Watch', SatelliteDish], ['Satellite Analysis', Satellite]]],
  [
    'Investigate',
    [
      ['Drift & Origin', Waves],
      ['Vessel Intelligence', Ship],
      ['TruthLoop', FlaskConical],
      ['Evidence Graph', Network],
      ['Timeline', Clock3],
    ],
  ],
  ['Protect', [['Ecological Exposure', Leaf], ['Response Planning', LifeBuoy], ['Next Observation', ScanSearch]]],
  ['Evidence', [['Cases & Data', FolderOpen], ['Reports', FileText], ['Provenance', Fingerprint]]],
  ['System', [['Settings', Settings2]]],
];
const CASE_FREE = new Set(['Overview', 'Copernicus Watch', 'Cases & Data', 'Settings']);
const RUN_MESSAGES: [string, string][] = [
  ['Satellite', 'Processing SAR scene'],
  ['Environmental', 'Validating environmental forcing'],
  ['Monte Carlo', 'Running hindcast & forecast'],
  ['AIS cleaning', 'Reconstructing AIS tracks'],
  ['Behaviour', 'Screening vessel behaviour'],
  ['Attribution', 'Ranking investigative leads'],
  ['Impact', 'Screening potential exposure'],
  ['Forensic', 'Generating forensic report'],
];

function readCollapsed() {
  try {
    return localStorage.getItem('oe.sidebar') === 'collapsed';
  } catch {
    return false;
  }
}

export default function App() {
  const [page, setPage] = useState('Overview'),
    [cases, setCases] = useState<Json[]>([]),
    [caseId, setCaseId] = useState(''),
    [a, setA] = useState<Json | null>(null),
    [loadingCase, setLoadingCase] = useState(true),
    [geography, setGeography] = useState<Json | null>(null),
    [health, setHealth] = useState<Json | null>(null),
    [selected, setSelected] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [job, setJob] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [modal, setModal] = useState<string | null>(null),
    [query, setQuery] = useState(''),
    [results, setResults] = useState<Json[]>([]),
    [windage, setWindage] = useState(0.03),
    [truth, setTruth] = useState<Json | null>(null),
    [truthError, setTruthError] = useState(''),
    [intel, setIntel] = useState<Json | null>(null),
    [intelError, setIntelError] = useState(''),
    [scenarios, setScenarios] = useState<Json[] | null>(null),
    [collapsed, setCollapsed] = useState(readCollapsed);

  const { live, error: liveError, refresh: refreshLive, offset } = useLiveStatus(caseId);
  const now = useTicker(offset);

  const refreshCases = async () => {
    const list = await api<Json[]>('/cases');
    setCases(list);
    return list;
  };
  const load = async (id: string) => {
    setCaseId(id);
    setA(null);
    setSelected(null);
    setLoadingCase(true);
    try {
      const c = await api('/cases/' + id);
      if (c.latest_run) {
        const data = await api('/cases/' + id + '/analysis');
        setA(data);
        setSelected(data.vessels[0]?.mmsi || null);
        setWindage(data.origin.parameters.windage);
      }
    } finally {
      setLoadingCase(false);
    }
  };

  useEffect(() => {
    Promise.all([refreshCases(), api('/geography'), api('/health')])
      .then(([list, g, h]) => {
        setGeography(g);
        setHealth(h);
        // Live Operations is the neutral default. Investigations are opened
        // explicitly so a saved demo never masquerades as current operations.
        setCaseId('');
        setA(null);
        setLoadingCase(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoadingCase(false);
      });
  }, []);

  // Run-bound shared views: TruthLoop (cheap GET; challenge is an explicit action), intelligence, scenarios.
  useEffect(() => {
    setTruth(null);
    setTruthError('');
    setIntel(null);
    setIntelError('');
    setScenarios(null);
    if (!a) return;
    api(`/cases/${a.case_id}/truthloop?run_id=${a.run_id}`).then(setTruth).catch((e) => setTruthError(e.message));
  }, [a?.run_id]);
  const loadIntel = () =>
    a
      ? api(`/cases/${a.case_id}/intelligence`)
          .then(setIntel)
          .catch((e) => setIntelError(e.message))
      : Promise.resolve();
  const loadScenarios = () =>
    a
      ? api<Json[]>(`/cases/${a.case_id}/scenarios?run_id=${a.run_id}`)
          .then(setScenarios)
          .catch(() => setScenarios([]))
      : Promise.resolve();
  useEffect(() => {
    if (!a) return;
    if (['Alerts', 'Ecological Exposure', 'Evidence Graph', 'Satellite Analysis'].includes(page) && !intel) loadIntel();
    if (page === 'Response Planning' && scenarios === null) loadScenarios();
  }, [page, a?.run_id, intel, scenarios]);

  useEffect(() => {
    if (query.length < 2) {
      setResults([]);
      return;
    }
    let cancel = false;
    const timer = setTimeout(
      () =>
        api<Json[]>('/search?q=' + encodeURIComponent(query) + (caseId ? '&case_id=' + caseId : ''))
          .then((v) => !cancel && setResults(v.slice(0, 8)))
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
          setSelected(data.vessels[0]?.mmsi || null);
          setBusy(false);
          setNotice('Investigation complete — every page now reflects this run.');
          refreshCases();
          refreshLive();
        } else if (status.state === 'FAILED') {
          setError(status.error);
          setBusy(false);
        }
      } catch (e) {
        setError((e as Error).message);
        setBusy(false);
      }
    }, 900);
    return () => clearInterval(interval);
  }, [job?.run_id, job?.state, caseId]);

  useEffect(() => {
    try {
      localStorage.setItem('oe.sidebar', collapsed ? 'collapsed' : 'open');
    } catch {
      /* storage unavailable: preference is per-session only */
    }
  }, [collapsed]);

  useEffect(() => {
    document.getElementById('main')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [page]);

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
      setCaseId(c.id);
      setA(null);
      setGeography(await api('/geography'));
      const j = await post('/cases/' + c.id + '/run', {});
      setJob(j);
      setNotice('');
    });
  const run = () =>
    action(async () => {
      if (!caseId) return;
      setBusy(true);
      const j = await post('/cases/' + caseId + '/run', { windage });
      setJob(j);
      setNotice('');
    });
  const navigate = (p: string) => {
    setPage(p);
    setNotice('');
  };
  const openVessel = (mmsi: string) => {
    setSelected(mmsi);
    navigate('Vessel Intelligence');
  };

  let content: ReactNode;
  if (loadingCase && !CASE_FREE.has(page)) content = <StateBlock kind="loading" title="Opening investigation" />;
  else if (!a && !CASE_FREE.has(page))
    content = (
      <StateBlock
        kind="empty"
        title={caseId ? 'This investigation has not been analysed yet' : 'No investigation open'}
        action={
          <button className="btn btn-primary" onClick={caseId ? run : createDemo} disabled={busy}>
            {caseId ? 'Run investigation' : 'Open demo investigation'}
          </button>
        }
      >
        {page} needs a completed analysis run.
      </StateBlock>
    );
  else
    switch (page) {
      case 'Overview':
        content = (
          <Overview
            a={a}
            live={live}
            liveError={liveError}
            refreshLive={refreshLive}
            truth={truth}
            job={job}
            geography={geography}
            selected={selected}
            setSelected={setSelected}
            navigate={navigate}
            busy={busy}
            onRun={run}
            onDemo={createDemo}
            onImport={() => setModal('import')}
            onOpenVessel={openVessel}
          />
        );
        break;
      case 'Alerts':
        content = <Alerts a={a!} data={intel} error={intelError} navigate={navigate} />;
        break;
      case 'Copernicus Watch':
        content = <CopernicusWatch live={live} refreshLive={refreshLive} navigate={navigate} />;
        break;
      case 'Satellite Analysis':
        content = <SatelliteAnalysis a={a!} intel={intel} />;
        break;
      case 'Drift & Origin':
        content = <DriftOrigin a={a!} geography={geography} live={live} windage={windage} setWindage={setWindage} busy={busy} onRun={run} />;
        break;
      case 'Vessel Intelligence':
        content = <Vessels a={a!} geography={geography} selected={selected} setSelected={setSelected} truth={truth} navigate={navigate} />;
        break;
      case 'TruthLoop':
        content = (
          <TruthLoop
            a={a!}
            truth={truth}
            truthError={truthError}
            setTruth={(t) => {
              setTruth(t);
              setIntel(null);
            }}
            live={live}
            refreshLive={refreshLive}
            navigate={navigate}
            onOpenVessel={openVessel}
          />
        );
        break;
      case 'Evidence Graph':
        content = <EvidenceGraph a={a!} truth={truth} navigate={navigate} onOpenVessel={openVessel} data={intel} error={intelError} />;
        break;
      case 'Timeline':
        content = <Timeline a={a!} truth={truth} live={live} />;
        break;
      case 'Ecological Exposure':
        content = <Ecology a={a!} data={intel} error={intelError} geography={geography} />;
        break;
      case 'Response Planning':
        content = (
          <ResponsePlanning
            a={a!}
            geography={geography}
            scenarios={scenarios}
            reload={async () => {
              await loadScenarios();
              setIntel(null);
            }}
          />
        );
        break;
      case 'Next Observation':
        content = <NextObservation a={a!} geography={geography} live={live} refreshLive={refreshLive} />;
        break;
      case 'Cases & Data':
        content = (
          <CasesData
            cases={cases}
            caseId={caseId}
            geography={geography}
            busy={busy}
            onOpen={(id) =>
              action(async () => {
                await load(id);
                navigate('Overview');
              })
            }
            onDemo={createDemo}
            onImport={() => setModal('import')}
            onGeography={() => setModal('geography')}
          />
        );
        break;
      case 'Reports':
        content = <Reports a={a!} truth={truth} />;
        break;
      case 'Provenance':
        content = <Provenance a={a!} />;
        break;
      case 'Settings':
        content = <Settings health={health} live={live} windage={windage} setWindage={setWindage} busy={busy} canRun={!!caseId} onRun={run} />;
        break;
    }

  const runMessage = job?.state === 'RUNNING' ? RUN_MESSAGES.find(([k]) => (job.stage || '').startsWith(k))?.[1] || 'Preparing input snapshots' : null;

  return (
    <ClockContext.Provider value={{ now, offset }}>
      <a className="skip" href="#main">Skip to content</a>
      <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
        <aside className="sidebar" aria-label="Primary">
          <div className="brand">
            <span className="brand-mark" aria-hidden>
              <svg viewBox="0 0 32 32" width="28" height="28">
                <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="1.5" opacity=".45" />
                <path d="M4 17c4-5 8-7 12-7s8 2 12 7c-4 4-8 6-12 6s-8-2-12-6z" fill="none" stroke="currentColor" strokeWidth="1.6" />
                <circle cx="16" cy="16.5" r="3.4" fill="currentColor" />
              </svg>
            </span>
            {!collapsed && (
              <span className="brand-text">
                OCEAN-EYE<small>Maritime forensic intelligence</small>
              </span>
            )}
          </div>
          <nav>
            {NAV.map(([group, items]) => (
              <div className="nav-group" key={group}>
                {!collapsed && <span className="nav-label">{group}</span>}
                {items.map(([name, IconC]) => (
                  <button
                    key={name}
                    className={page === name ? 'active' : ''}
                    aria-label={name}
                    aria-current={page === name ? 'page' : undefined}
                    title={collapsed ? name : undefined}
                    onClick={() => navigate(name)}
                  >
                    <IconC size={17} aria-hidden />
                    {!collapsed && <span>{name}</span>}
                  </button>
                ))}
              </div>
            ))}
          </nav>
          <button
            className="collapse"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          >
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            {!collapsed && <span>Collapse</span>}
          </button>
        </aside>

        <div className="workspace">
          <header className="topbar">
            <label className="case-switch">
              <span className="sr-only">Investigation</span>
              <select
                value={caseId}
                onChange={(e) =>
                  action(async () => {
                    if (e.target.value) await load(e.target.value);
                    else {
                      setCaseId('');
                      setA(null);
                      setSelected(null);
                      setLoadingCase(false);
                      navigate('Overview');
                    }
                  })
                }
                aria-label="Open investigation"
              >
                <option value="">Live operations</option>
                {cases.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.source_type === 'SYNTHETIC' ? 'Demo case · ' : 'Real case · '}{c.name} · {c.id.slice(0, 4)}
                  </option>
                ))}
              </select>
            </label>
            <div className="search">
              <Search size={15} aria-hidden />
              <input
                aria-label="Search places, vessels, MMSI or coordinates"
                placeholder="Search places, vessels, MMSI or lat, lon"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {results.length > 0 && (
                <ul className="search-results" role="listbox">
                  {results.map((r, i) => (
                    <li key={i}>
                      <button
                        onClick={() => {
                          setQuery('');
                          if (r.kind === 'vessel') openVessel(r.id);
                          else navigate('Overview');
                        }}
                      >
                        <span>{r.name}</span>
                        <small>{r.kind}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="topbar-status">
              {runMessage ? (
                <span className="running">
                  <LoaderCircle size={14} className="spin" aria-hidden /> {runMessage}…
                </span>
              ) : page === 'Overview' ? null : (
                <Status
                  state={liveError ? 'OFFLINE' : live?.health?.state || 'PENDING'}
                  text={liveError ? 'Local service unreachable' : live?.health ? `System ${live.health.state.toLowerCase()}` : 'Connecting'}
                />
              )}
            </div>
          </header>
          {runMessage && <div className="progress" role="progressbar" aria-label={runMessage}><i /></div>}

          <main id="main" tabIndex={-1}>
            {error && (
              <div className="banner error" role="alert">
                <span>{error}</span>
                <button className="icon-btn" aria-label="Dismiss" onClick={() => setError('')}>
                  <X size={14} />
                </button>
              </div>
            )}
            {notice && (
              <div className="banner" role="status">
                <span>{notice}</span>
                <button className="icon-btn" aria-label="Dismiss" onClick={() => setNotice('')}>
                  <X size={14} />
                </button>
              </div>
            )}
            <div className="page" key={page}>
              {content}
            </div>
          </main>
        </div>
      </div>
      <NewObservationToast live={live} navigate={navigate} />
      {modal && (
        <div className="modal-backdrop" onClick={() => setModal(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label={modal === 'import' ? 'Import investigation' : 'Import reference layer'} onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2>{modal === 'import' ? 'Import investigation' : 'Import reference layer'}</h2>
              <button className="icon-btn" aria-label="Close" onClick={() => setModal(null)}>
                <X size={16} />
              </button>
            </div>
            {modal === 'import' ? (
              <ImportForm
                onDone={async (id) => {
                  setModal(null);
                  await refreshCases();
                  await load(id);
                  navigate('Overview');
                }}
              />
            ) : (
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
    </ClockContext.Provider>
  );
}
