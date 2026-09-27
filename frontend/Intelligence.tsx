import { useEffect, useState, ReactNode } from 'react';
import { ChevronRight, ShieldAlert } from 'lucide-react';
import { api, post, Json, time, coordinate } from './api';
import MaritimeMap from './MaritimeMap';
import { NextBestObservation } from './Copernicus';

export function Workflow({
  navigate,
  a,
  page,
}: {
  navigate: (s: string) => void;
  a?: Json;
  page?: string;
}) {
  // Real, deterministic stage state -- never fabricated. COMPLETE means the
  // underlying result already exists in this run; WAITING means the stage is
  // available but requires an explicit analyst action (challenge, scenario,
  // watch) that has not necessarily been taken; UNAVAILABLE means this run's
  // own data structurally cannot support the stage (e.g. no AIS tracks).
  const vesselCount = a?.vessels?.length ?? 0;
  const stages: [string, string, string][] = [
    ['OBSERVE', 'Satellite Analysis', a ? 'COMPLETE' : 'WAITING'],
    ['VERIFY', 'Verification', a ? 'COMPLETE' : 'WAITING'],
    ['RECONSTRUCT', 'Drift & Origin', a?.origin ? 'COMPLETE' : 'WAITING'],
    ['INVESTIGATE', 'Vessel Ranking', !a ? 'WAITING' : vesselCount > 0 ? 'COMPLETE' : 'UNAVAILABLE'],
    ['CHALLENGE', 'TruthLoop', !a ? 'WAITING' : vesselCount > 0 ? 'WAITING' : 'UNAVAILABLE'],
    ['FORECAST', 'Drift & Origin', a?.forecast ? 'COMPLETE' : 'WAITING'],
    ['PROTECT', 'Ecological Exposure', a?.impact ? 'COMPLETE' : 'WAITING'],
    ['RESPOND', 'Response Twin', 'WAITING'],
    ['RE-OBSERVE', 'Copernicus Watch', 'WAITING'],
    ['PROVE', 'Evidence Graph', a?.evidence ? 'COMPLETE' : 'WAITING'],
  ].map(([label, target, state]) => [label, target, page === target ? 'ACTIVE' : state]);
  return (
    <nav className="mission-workflow" aria-label="Investigation journey">
      {stages.map(([label, target, state], i) => (
        <button key={label} className={'stage-' + state.toLowerCase()} onClick={() => navigate(target)} title={state}>
          <small>{String(i + 1).padStart(2, '0')}</small>
          {label}
          <span className={'stage-dot ' + state.toLowerCase()} />
        </button>
      ))}
    </nav>
  );
}
export function InvestigativePriorities({
  a,
  navigate,
  copernicusConfigured,
}: {
  a: Json;
  navigate: (s: string) => void;
  copernicusConfigured?: boolean;
}) {
  const [truthloop, setTruthloop] = useState<Json | null>(null);
  const [nextObs, setNextObs] = useState<Json | null>(null);
  useEffect(() => {
    setTruthloop(null);
    setNextObs(null);
    api(`/cases/${a.case_id}/truthloop?run_id=${a.run_id}`).then(setTruthloop).catch(() => setTruthloop(null));
    api(`/cases/${a.case_id}/next-observations?run_id=${a.run_id}`).then(setNextObs).catch(() => setNextObs(null));
  }, [a.case_id, a.run_id]);

  const lead = a.vessels?.[0];
  const exposure = (a.impact?.receptors || []).find((r: Json) => r.first_overlap_h !== null);
  const stabilityBadge: Record<string, string> = {
    ROBUST: 'green', MODERATE: 'amber', FRAGILE: 'red', INDETERMINATE: 'purple',
  };
  const topCandidate = nextObs?.candidates?.[0];

  const rows: [string, ReactNode, () => void][] = [
    [
      'TOP INVESTIGATIVE LEAD',
      lead ? <>{lead.name} <span className="muted">· {lead.score}/100</span></> : <span className="muted">No AIS-tracked vessels</span>,
      () => navigate('Vessel Ranking'),
    ],
    [
      'CONCLUSION STABILITY',
      truthloop ? (
        <span className={'badge ' + (stabilityBadge[truthloop.stability.state] || '')}>{truthloop.stability.state}</span>
      ) : (
        <span className="muted">Computing…</span>
      ),
      () => navigate('TruthLoop'),
    ],
    [
      'NEXT POTENTIAL EXPOSURE',
      exposure ? <>{exposure.name} <span className="muted">· +{exposure.first_overlap_h}h</span></> : <span className="muted">No sampled overlap within forecast</span>,
      () => navigate('Ecological Exposure'),
    ],
    [
      'NEXT-BEST OBSERVATION',
      topCandidate ? <>{topCandidate.target_type} <span className="muted">· {topCandidate.score}/100</span></> : <span className="muted">Computing…</span>,
      () => navigate('TruthLoop'),
    ],
    [
      'COPERNICUS STATUS',
      <span className={'badge ' + (copernicusConfigured ? 'green' : 'amber')}>{copernicusConfigured ? 'LIVE' : 'DEMO'}</span>,
      () => navigate('Copernicus Watch'),
    ],
    [
      'RESPONSE WINDOW',
      exposure ? <>~{exposure.first_overlap_h}h to modeled onset</> : <span className="muted">No modeled onset within forecast</span>,
      () => navigate('Response Twin'),
    ],
  ];

  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>
          <ShieldAlert size={16} />
          Investigative priorities
        </h2>
      </div>
      <ul className="priority-list">
        {rows.map(([label, value, go]) => (
          <li key={label}>
            <button onClick={go}>
              <span className="micro">{label}</span>
              <span>{value}</span>
              <ChevronRight size={13} />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Detail({ value, label = 'Evidence and provenance' }: { value: any; label?: string }) {
  return (
    <details>
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function Dossier({ a, v }: { a: Json; v: Json }) {
  return (
    <section className="intelligence-block">
      <h3>Vessel investigation dossier</h3>
      <p>INVESTIGATIVE RELEVANCE SCORE · {v.score}/100</p>
      <p className="limitation">Not a probability of culpability.</p>
      <p>
        {v.name} · MMSI {v.mmsi} · IMO {v.imo || 'Unavailable'} · AIS type code{' '}
        {v.vessel_type || 'Unavailable'}
      </p>
      <p>
        Nearest observed approach: {v.nearest_km} km · Ownership, history and violations:
        unavailable.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Factor</th>
              <th>Value / 100</th>
              <th>Weight</th>
              <th>Contribution</th>
            </tr>
          </thead>
          <tbody>
            {v.components.map((c: Json) => (
              <tr key={c.name}>
                <td>{c.name}</td>
                <td>{c.value}</td>
                <td>{(c.weight * 100).toFixed(0)}%</td>
                <td>{c.contribution}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Detail label="Supporting evidence" value={v.supporting} />
      <Detail label="Contradicting evidence" value={v.contradicting} />
      <Detail label="AIS reporting gaps and uncertainty" value={v.gaps} />
      <details>
        <summary>Observed speed / course history and coordinates</summary>
        <div className="history-table">
          <table>
            <thead>
              <tr>
                <th>UTC</th>
                <th>Position</th>
                <th>Knots</th>
                <th>Course °</th>
              </tr>
            </thead>
            <tbody>
              {v.track.map((p: Json, i: number) => (
                <tr key={i}>
                  <td>{time(p.time)}</td>
                  <td>{coordinate(p.coordinates)}</td>
                  <td>{p.sog ?? 'Unavailable'}</td>
                  <td>{p.cog ?? 'Unavailable'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <Detail
        label="AIS input provenance"
        value={a.provenance.inputs.filter((p: Json) => p.role === 'ais')}
      />
      <a
        className="secondary"
        href={`/api/v1/cases/${a.case_id}/vessels/${v.mmsi}/dossier`}
        target="_blank"
        rel="noreferrer"
      >
        Open dossier JSON
      </a>
    </section>
  );
}
export function GlobalIncidents({
  geography,
  select,
}: {
  geography: Json | null;
  select: (id: string) => void;
}) {
  const [items, setItems] = useState<Json[]>([]),
    [error, setError] = useState('');
  useEffect(() => {
    api<Json[]>('/incidents')
      .then(setItems)
      .catch((e) => setError(e.message));
  }, []);
  return (
    <section className="panel padded">
      <h2>Saved investigations · global incident view</h2>
      <p>
        {items.length} saved case{items.length === 1 ? '' : 's'}. Local case inventory; live global
        monitoring is not connected.
      </p>
      {error && <p role="alert">{error}</p>}
      <MaritimeMap
        analysis={null}
        geography={geography}
        selected={null}
        onSelect={() => {}}
        focus={null}
        global
        incidents={items}
        onCase={select}
      />
      <div className="incident-list">
        {items.map((c) => (
          <button key={c.id} onClick={() => select(c.id)}>
            <strong>{c.name}</strong>
            <span>
              {c.status} · {c.source_type}
            </span>
            <small>
              {time(c.observation_time)} ·{' '}
              {c.coordinates
                ? coordinate(c.coordinates)
                : 'Location unavailable until analysis completes'}
            </small>
          </button>
        ))}
      </div>
    </section>
  );
}
export function Intelligence({
  a,
  page,
  geography,
  onVessel,
}: {
  a: Json;
  page: string;
  geography: Json | null;
  onVessel: (id: string) => void;
}) {
  const [data, setData] = useState<Json | null>(null),
    [error, setError] = useState(''),
    [scenarios, setScenarios] = useState<Json[]>([]),
    [node, setNode] = useState<string>('sar'),
    [watchNotice, setWatchNotice] = useState('');
  const refresh = () =>
    Promise.all([
      api(`/cases/${a.case_id}/intelligence`),
      api<Json[]>(`/cases/${a.case_id}/scenarios?run_id=${a.run_id}`),
    ]).then(([d, s]) => {
      setData(d);
      setScenarios(s);
    });
  useEffect(() => {
    let active = true;
    setData(null);
    setError('');
    Promise.all([
      api(`/cases/${a.case_id}/intelligence`),
      api<Json[]>(`/cases/${a.case_id}/scenarios?run_id=${a.run_id}`),
    ])
      .then(([d, s]) => {
        if (active) {
          setData(d);
          setScenarios(s);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [a.run_id, page]);
  if (error)
    return (
      <section className="panel padded" role="alert">
        {error}
        <button
          onClick={() => {
            setError('');
            refresh().catch((e) => setError(e.message));
          }}
        >
          Retry
        </button>
      </section>
    );
  if (!data) return <section className="panel padded">Loading run-bound intelligence…</section>;
  if (page === 'Response Twin')
    return <ResponsePlanner a={a} geography={geography} scenarios={scenarios} changed={refresh} />;
  if (page === 'Verification')
    return (
      <section className="panel padded">
        <h2>OIL CANDIDATE — CLASSIFICATION PENDING</h2>
        <p>
          Pollutant identity: UNKNOWN. Dark-region segmentation is a screening measurement, not a
          validated oil classifier.
        </p>
        <Detail
          value={data.classification}
          label="Classifier integration contract / availability"
        />
        <h3>Supported incident evidence requirements</h3>
        {data.incident.types.map((t: Json) => (
          <Detail key={t.id || t.type} label={t.label} value={t} />
        ))}
      </section>
    );
  if (page === 'Ecological Exposure')
    return (
      <section className="panel padded">
        <h2>Ecological exposure intelligence</h2>
        <p>{data.ecology.level} · Forecast overlap is not confirmed ecological damage.</p>
        <p className="limitation">{data.ecology.species_assessment}</p>
        <p>{data.ecology.biodiversity_coverage}</p>
        {!data.ecology.receptors.length && (
          <p>
            No receptor layers loaded in this investigation. Import documented geographic layers and
            rerun.
          </p>
        )}
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Receptor / category</th>
                <th>First overlapping sample</th>
                <th>Dataset / version</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.ecology.receptors.map((r: Json, i: number) => (
                <tr key={i}>
                  <td>
                    {r.name}
                    <small>{r.kind}</small>
                  </td>
                  <td>
                    {r.first_overlap_time ? time(r.first_overlap_time) : 'No sampled overlap'}
                  </td>
                  <td>
                    {r.source} / {r.version}
                    <small>{r.source_type}</small>
                  </td>
                  <td>
                    {r.status}
                    <small>{r.uncertainty}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>{data.ecology.limitations}</p>
      </section>
    );
  if (page === 'Evidence Graph') {
    const graph = data.evidence;
    const current = graph.nodes.find((n: Json) => n.id === node) || graph.nodes[0];
    return (
      <section className="panel padded">
        <h2>Interactive forensic reasoning</h2>
        <p>Select a node to inspect its evidence, incoming dependencies and provenance.</p>
        <div className="reasoning-grid">
          {graph.nodes.map((n: Json) => (
            <button
              key={n.id}
              className={current.id === n.id ? 'chosen' : ''}
              onClick={() => setNode(n.id)}
            >
              <small>{n.kind}</small>
              <strong>{n.label}</strong>
            </button>
          ))}
        </div>
        <section className="intelligence-block">
          <h3>{current.label}</h3>
          <p>
            {current.kind} · Run {a.run_id}
          </p>
          <p>
            Informed by:{' '}
            {graph.edges
              .filter((e: Json) => e.target === current.id)
              .map((e: Json) => graph.nodes.find((n: Json) => n.id === e.source)?.label || e.source)
              .join(' → ') || 'Source observation'}
          </p>
          <p>
            Informs:{' '}
            {graph.edges
              .filter((e: Json) => e.source === current.id)
              .map((e: Json) => graph.nodes.find((n: Json) => n.id === e.target)?.label || e.target)
              .join(' → ') || 'Terminal evidence'}
          </p>
          {current.mmsi && (
            <button onClick={() => onVessel(current.mmsi)}>Open vessel dossier</button>
          )}
          <Detail label="Node evidence" value={current.details} />
          <Detail
            label="Source → processing → parameters → hash"
            value={{
              source_type: a.source_type,
              created: a.created,
              analysis_hash: a.analysis_hash,
              ...a.provenance,
            }}
          />
        </section>
      </section>
    );
  }
  return (
    <section className="panel padded">
      <h2>What requires attention?</h2>
      <p>
        Rule-based findings anchored to this observation: {time(a.observation_time)}. External
        notifications are not configured.
      </p>
      <NextBestObservation
        caseId={a.case_id}
        runId={a.run_id}
        onWatchCreated={(name) => setWatchNotice(`Added "${name}" to the Copernicus watch list.`)}
      />
      {watchNotice && (
        <p className="micro" style={{ color: '#60dab6' }}>
          {watchNotice}
        </p>
      )}
      {data.alerts.map((e: Json) => (
        <article className="intelligence-block" key={e.id}>
          <span className={'severity ' + e.severity}>{e.severity}</span>
          <h3>{e.title}</h3>
          <p>{e.why}</p>
          <p>
            <strong>Next action:</strong> {e.next_action}
          </p>
          <small>
            {time(e.event_time)} · {coordinate(e.location)}
          </small>
          <Detail
            label="Why this recommendation · rule, data, assumptions, uncertainty"
            value={e}
          />
        </article>
      ))}
    </section>
  );
}
function ResponsePlanner({
  a,
  geography,
  scenarios,
  changed,
}: {
  a: Json;
  geography: Json | null;
  scenarios: Json[];
  changed: () => Promise<any>;
}) {
  const [kind, setKind] = useState('no_action'),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<string>(''),
    [point, setPoint] = useState<number[] | null>(null);
  const scenario = scenarios.find((s) => s.id === selected) || scenarios[0] || null;
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const f = new FormData(event.currentTarget);
    const num = (key: string) => Number(f.get(key));
    try {
      const created = await post(`/cases/${a.case_id}/scenarios`, {
        run_id: a.run_id,
        name: f.get('name'),
        kind,
        departure: kind === 'no_action' ? null : [num('lon'), num('lat')],
        target: point,
        target_horizon_h: num('horizon'),
        speed_kn: num('speed'),
        speed_uncertainty_fraction: num('uncertainty') / 100,
        delay_h: num('delay'),
        setup_h: num('setup'),
        asset_name: f.get('asset'),
        asset_source: f.get('source'),
      });
      await changed();
      setSelected(created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel padded">
      <h2>Response digital twin · planning foundation</h2>
      <p>
        Geospatial travel and timing comparison. Asset availability and response effectiveness are
        unverified.
      </p>
      <div className="response-layout">
        <form onSubmit={submit} className="response-form">
          <label>
            Scenario name
            <input name="name" required maxLength={100} defaultValue="Response planning scenario" />
          </label>
          <label>
            Intervention
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {[
                ['no_action', 'No intervention'],
                ['boom', 'Containment boom'],
                ['dispatch', 'Dispatch response vessel'],
                ['interception', 'Cleanup / interception'],
                ['delayed_response', 'Delayed response'],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Asset name
            <input name="asset" required defaultValue="Operator-assumed response asset" />
          </label>
          <label>
            Asset source / availability
            <input
              name="source"
              required
              defaultValue="Operator assumption; availability unverified"
            />
          </label>
          <div className="form-pair">
            <label>
              Departure longitude
              <input
                name="lon"
                type="number"
                step="any"
                min="-180"
                max="180"
                required={kind !== 'no_action'}
                disabled={kind === 'no_action'}
              />
            </label>
            <label>
              Departure latitude
              <input
                name="lat"
                type="number"
                step="any"
                min="-90"
                max="90"
                required={kind !== 'no_action'}
                disabled={kind === 'no_action'}
              />
            </label>
          </div>
          <div className="form-pair">
            <label>
              Speed (knots)
              <input
                name="speed"
                type="number"
                min="0.1"
                max="60"
                step="0.1"
                defaultValue="12"
                required
              />
            </label>
            <label>
              Speed uncertainty (%)
              <input name="uncertainty" type="number" min="0" max="80" defaultValue="20" required />
            </label>
          </div>
          <div className="form-pair">
            <label>
              Departure delay (h)
              <input
                name="delay"
                type="number"
                min="0"
                max="96"
                step="0.1"
                defaultValue="0"
                required
              />
            </label>
            <label>
              Setup time (h)
              <input
                name="setup"
                type="number"
                min="0"
                max="48"
                step="0.1"
                defaultValue="1"
                required
              />
            </label>
          </div>
          <label>
            Forecast target horizon
            <select name="horizon" defaultValue="24">
              {[6, 12, 24, 48].map((h) => (
                <option key={h} value={h}>
                  +{h} hours
                </option>
              ))}
            </select>
          </label>
          <p>
            Target: {point ? coordinate(point) : 'Selected forecast centroid (modeled)'}. Click the
            map to select a target.
          </p>
          {point && (
            <button type="button" onClick={() => setPoint(null)}>
              Use forecast centroid
            </button>
          )}
          <button className="primary" disabled={busy}>
            {busy ? 'Computing…' : 'Save and evaluate scenario'}
          </button>
          {error && (
            <p role="alert" className="limitation">
              {error}
            </p>
          )}
        </form>
        <div>
          <MaritimeMap
            analysis={a}
            geography={geography}
            selected={null}
            onSelect={() => {}}
            focus={point}
            response={scenario}
            onPlanningPoint={setPoint}
          />
          <p className="micro">
            ASSUMED asset and straight geodesic route; land and navigation restrictions are not
            routed.
          </p>
        </div>
      </div>
      <h3>Compare saved scenarios</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Travel</th>
              <th>Arrival after observation</th>
              <th>Ready margin to target</th>
              <th>Exposure after intervention</th>
            </tr>
          </thead>
          <tbody>
            {scenarios.map((s) => (
              <tr key={s.id} onClick={() => setSelected(s.id)}>
                <td>
                  <button onClick={() => setSelected(s.id)}>{s.input.name}</button>
                </td>
                <td>{s.travel_km === null ? 'Baseline' : `${s.travel_km} km`}</td>
                <td>
                  {s.arrival_window_h?.map((n: number) => n.toFixed(2)).join(' – ') ||
                    'Not applicable'}{' '}
                  {s.arrival_window_h ? 'h' : ''}
                </td>
                <td>
                  {s.margin_to_target_h === null ? 'Not applicable' : `${s.margin_to_target_h} h`}
                </td>
                <td>NOT MODELED</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {scenario && (
        <article className="intelligence-block">
          <h3>{scenario.input.name}</h3>
          <p>{scenario.comparison}</p>
          <p>
            Baseline:{' '}
            {
              scenario.baseline.receptor_exposure.filter((r: Json) => r.first_overlap_h !== null)
                .length
            }{' '}
            loaded receptors with sampled potential exposure. Intervention: unknown.
          </p>
          <Detail value={scenario} label="Scenario results, assumptions, opportunities and hash" />
          <a className="secondary" href={`/api/v1/scenarios/${scenario.id}/export`}>
            Export scenario and audit JSON
          </a>
        </article>
      )}
    </section>
  );
}
