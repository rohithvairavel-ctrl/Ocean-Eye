import { useEffect, useState } from 'react';
import { ArrowDownToLine, FileText, Plus, TriangleAlert } from 'lucide-react';
import { Json, api, coordinate, overlapTime, shortTime, time } from './api';
import MaritimeMap from './MaritimeMap';
import { provider } from './LiveData';
import { Metric, PageHeader, Section, StateBlock, Status, Tag } from './ui';

/* ------------------------------------------------------------------ */
/* Satellite analysis (includes classification status)                */
/* ------------------------------------------------------------------ */

export function SatelliteAnalysis({ a, intel }: { a: Json; intel: Json | null }) {
  const s = a.spill;
  return (
    <>
      <PageHeader eyebrow="Observe" title="Satellite analysis" question="What did the SAR observation contain?">
        <div className="incident-meta">
          <span className="classification">Oil candidate — classification pending</span>
          <span>{s.sensor}</span>
          <span>{time(a.observation_time)}</span>
          <span>{s.polarizations?.join(' / ')}</span>
        </div>
      </PageHeader>
      <div className="triptych">
        {[
          ['satellite.png', 'Calibrated σ⁰ input', 'Observed backscatter'],
          ['processed.png', 'Speckle screening', 'Median filter'],
          ['mask.png', 'Dark-region candidate', 'Computed segmentation'],
        ].map(([file, title, note]) => (
          <figure key={file}>
            <img src={`${a.assets}/${file}`} alt={title} loading="lazy" />
            <figcaption>
              <strong>{title}</strong>
              <span>{note}</span>
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="facts">
        <Metric label="Candidate area" value={s.area_km2.toFixed(2)} unit="km²" tone="hazard" />
        <Metric label="Perimeter" value={s.perimeter_km.toFixed(1)} unit="km" />
        <Metric label="Contrast" value={s.contrast_db} unit="dB" />
        <Metric label="Threshold" value={s.threshold_db} unit="dB" />
        <Metric label="Components" value={s.component_count} hint={`${s.candidate_pixels} pixels`} />
      </div>
      <div className="split even">
        <Section title="Classification status">
          <StateBlock kind="waiting" title="Pollutant identity: unknown">
            Dark-region segmentation is a screening measurement. No validated oil / look-alike classifier is installed, so there is
            no classification accuracy or confidence to report. Confirmation needs a second observation or field evidence.
          </StateBlock>
          <p className="fine">
            Look-alike screen: {s.look_alike_screening?.rejected_regions?.length ?? 0} weak-damping region(s) rejected. This screen is not a validated classifier.
          </p>
          {intel && (
            <details>
              <summary>Evidence required per incident type</summary>
              <ul className="plain">
                {intel.incident.types.map((t: Json) => (
                  <li key={t.id || t.type}>
                    <b>{t.label}</b> — {(t.required_evidence || []).join(', ')}{t.supported_workflow ? '' : ' (workflow not supported)'}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Section>
        <Section title="Method & image provenance">
          <dl className="kv">
            <dt>Method</dt>
            <dd>{s.method}</dd>
            <dt>CRS</dt>
            <dd className="mono">{s.crs}</dd>
            <dt>Resolution</dt>
            <dd className="mono">{s.resolution.join(' × ')} m</dd>
            <dt>Radiometry</dt>
            <dd>{s.radiometry}</dd>
            <dt>Calibration</dt>
            <dd>{s.calibration}</dd>
          </dl>
          <a className="btn btn-quiet btn-sm" href={`${a.assets}/mask.tif`}>
            <ArrowDownToLine size={13} /> Georeferenced mask (GeoTIFF)
          </a>
        </Section>
      </div>
      <Section title="Limitations">
        <ul className="limitations">
          {s.limitations.map((l: string) => (
            <li key={l}>
              <TriangleAlert size={13} aria-hidden /> {l}
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Drift & origin                                                     */
/* ------------------------------------------------------------------ */

export function DriftOrigin({
  a,
  geography,
  live,
  windage,
  setWindage,
  busy,
  onRun,
}: {
  a: Json;
  geography: Json | null;
  live: Json | null;
  windage: number;
  setWindage: (n: number) => void;
  busy: boolean;
  onRun: () => void;
}) {
  const [series, setSeries] = useState<Json | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    setSeries(null);
    api(`/cases/${a.case_id}/environment/series?run_id=${a.run_id}`)
      .then(setSeries)
      .catch((e) => setError(e.message));
  }, [a.run_id]);
  const o = a.origin;
  const ocean = provider(live, 'ocean_model');
  return (
    <>
      <PageHeader
        eyebrow="Investigate"
        title="Drift & origin"
        question="Where could the candidate have come from, and where may it go?"
      />
      <div className="split wide-left">
        <MaritimeMap
          analysis={a}
          geography={geography}
          layers={{ hindcast: true, lead: false, selected: false, aoi: false, currents: true }}
          height={600}
          label="Hindcast → observation → forecast"
        />
        <div className="stack">
          <Section title="Modeled origin region">
            <dl className="kv">
              <dt>Centre</dt>
              <dd>{coordinate(o.centroid)}</dd>
              <dt>90% radius</dt>
              <dd className="mono">{o.radius90_km} km</dd>
              <dt>Release window</dt>
              <dd>
                {shortTime(o.release_window[0])} – {shortTime(o.release_window[1])} <Tag tone="warn">assumed</Tag>
              </dd>
              <dt>Spill age</dt>
              <dd className="mono">{o.age_hours[0]}–{o.age_hours[1]} h</dd>
              <dt>Method</dt>
              <dd>{o.method}</dd>
            </dl>
            <p className="fine">{o.uncertainty}. {o.release_window_basis}.</p>
          </Section>
          <Section title="Forecast envelope">
            <div className="table-wrap">
              <table className="compact">
                <thead>
                  <tr>
                    <th>Horizon</th>
                    <th>Valid time</th>
                    <th>90% spread</th>
                  </tr>
                </thead>
                <tbody>
                  {a.forecast.steps.map((s: Json) => (
                    <tr key={s.hours}>
                      <td className="mono">+{s.hours} h</td>
                      <td>{shortTime(s.time)}</td>
                      <td className="mono">{s.spread90_km} km</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="fine">{a.forecast.uncertainty}</p>
          </Section>
        </div>
      </div>

      <Section
        title="Environmental forcing"
        note="Values the drift model used. Forcing is a model or supplied input — never an observation of the slick."
      >
        {error ? (
          <StateBlock kind="error" compact title="Forcing series unavailable">{error}</StateBlock>
        ) : !series ? (
          <StateBlock kind="loading" compact title="Reading forcing series" />
        ) : !series.available ? (
          <StateBlock kind="empty" compact title="No forcing records">{series.reason}</StateBlock>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Sample</th>
                    <th>Valid time</th>
                    <th>Current speed</th>
                    <th>Flowing toward</th>
                    <th>Kind</th>
                  </tr>
                </thead>
                <tbody>
                  {series.points.map((p: Json) => (
                    <tr key={p.label}>
                      <td><b>{p.label.toLowerCase().replace(/^\w/, (c: string) => c.toUpperCase())}</b></td>
                      <td>{shortTime(p.valid_time)}</td>
                      <td className="mono">{p.available ? `${p.speed_ms.toFixed(2)} m/s` : '—'}</td>
                      <td className="mono">{p.available ? `${p.direction_to_deg.toFixed(0)}°` : '—'}</td>
                      <td>{p.available ? <Tag tone={p.kind === 'FORECAST' ? 'forecast' : p.kind === 'SYNTHETIC' ? 'demo' : 'neutral'}>{p.kind.toLowerCase()}</Tag> : <span className="muted">{p.reason}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="fine">
              Source: {series.source} · coverage {shortTime(series.coverage[0])} – {shortTime(series.coverage[1])}. {series.direction_convention}
            </p>
          </>
        )}
        <div className="inline-status">
          <span>Copernicus Marine currents</span>
          <Status state={ocean?.status} />
          <span className="fine">{ocean?.message}</span>
        </div>
      </Section>

      <Section title="Sensitivity" note="Re-running creates a new immutable run; the current run and its evidence are preserved.">
        <div className="sensitivity">
          <label>
            Windage coefficient <b className="mono">{(windage * 100).toFixed(1)}%</b>
            <input type="range" min="0" max="0.1" step="0.005" value={windage} onChange={(e) => setWindage(+e.target.value)} />
          </label>
          <button className="btn btn-quiet" disabled={busy} onClick={onRun}>
            {busy ? 'Running hindcast…' : 'Re-run with this windage'}
          </button>
        </div>
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Timeline                                                            */
/* ------------------------------------------------------------------ */

export function Timeline({ a, truth, live }: { a: Json; truth: Json | null; live: Json | null }) {
  const events: Json[] = [
    ...a.timeline.map((e: Json) => ({ ...e, kind: e.type === 'observation' ? 'OBSERVED' : e.type === 'assumption' ? 'ASSUMED' : 'OBSERVED' })),
    ...a.forecast.steps.map((s: Json) => ({ time: s.time, event: `Forecast envelope +${s.hours} h (90% spread ${s.spread90_km} km)`, kind: 'PREDICTED' })),
    ...a.impact.receptors
      .filter((r: Json) => overlapTime(a, r))
      .map((r: Json) => ({ time: overlapTime(a, r), event: `Potential exposure: ${r.name}`, kind: 'PREDICTED' })),
    { time: a.created, event: 'Investigation run completed and evidence package hashed', kind: 'RECORDED' },
    ...(truth?.challenged ? [{ time: truth.challenged_at, event: `TruthLoop challenge recorded — stability ${truth.stability.state.toLowerCase()}`, kind: 'RECORDED' }] : []),
    ...(live?.events || [])
      .filter((e: Json) => (live?.watch_areas || []).some((w: Json) => w.id === e.watch_id && w.case_id === a.case_id))
      .map((e: Json) => ({ time: e.acquired, event: `New Earth observation over watched AOI (${e.platform || 'Sentinel-1'})`, kind: 'OBSERVED' })),
  ].sort((x, y) => Date.parse(x.time) - Date.parse(y.time));
  const tone: Record<string, any> = { OBSERVED: 'hazard', ASSUMED: 'warn', PREDICTED: 'forecast', RECORDED: 'ok' };
  return (
    <>
      <PageHeader eyebrow="Investigate" title="Timeline" question="What happened, in order — and what kind of knowledge is each event?" />
      <ol className="timeline">
        {events.map((e, i) => (
          <li key={i} className={`tl-${e.kind.toLowerCase()}`}>
            <time>{time(e.time)}</time>
            <span className="tl-dot" aria-hidden />
            <div>
              <p>{e.event}</p>
              <Tag tone={tone[e.kind]}>{e.kind.toLowerCase()}</Tag>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Cases & data (includes the saved-incident world map)               */
/* ------------------------------------------------------------------ */

export function CasesData({
  cases,
  caseId,
  geography,
  busy,
  onOpen,
  onDemo,
  onImport,
  onGeography,
}: {
  cases: Json[];
  caseId: string;
  geography: Json | null;
  busy: boolean;
  onOpen: (id: string) => void;
  onDemo: () => void;
  onImport: () => void;
  onGeography: () => void;
}) {
  const [incidents, setIncidents] = useState<Json[] | null>(null),
    [inventory, setInventory] = useState<Json | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    api<Json[]>('/incidents').then(setIncidents).catch((e) => setError(e.message));
    api('/datasets').then(setInventory).catch(() => {});
  }, [cases.length]);
  return (
    <>
      <PageHeader
        eyebrow="Evidence"
        title="Cases & data"
        question="Which investigations and inputs exist on this computer?"
        actions={
          <>
            <button className="btn btn-quiet" onClick={onDemo} disabled={busy}>
              <Plus size={15} /> New demo case
            </button>
            <button className="btn btn-primary" onClick={onImport}>
              Import investigation
            </button>
          </>
        }
      />
      {error && <StateBlock kind="error" compact title="Could not list investigations">{error}</StateBlock>}
      <div className="split">
        <div className="table-wrap">
          <table className="selectable">
            <thead>
              <tr>
                <th>Investigation</th>
                <th>Observed</th>
                <th>Data</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(incidents || []).map((c) => (
                <tr key={c.id} className={c.id === caseId ? 'on' : ''} tabIndex={0} onClick={() => onOpen(c.id)} onKeyDown={(e) => e.key === 'Enter' && onOpen(c.id)}>
                  <td>
                    <strong>{c.name}</strong>
                    <span className="cell-note mono">{c.id.slice(0, 8)}{c.coordinates ? ' · ' + coordinate(c.coordinates) : ''}</span>
                  </td>
                  <td className="small">{shortTime(c.observation_time)}</td>
                  <td><Tag tone={c.source_type === 'REAL' ? 'ok' : 'demo'}>{c.source_type === 'REAL' ? 'real' : 'demo'}</Tag></td>
                  <td><Status state={c.status === 'NOT_ANALYZED' ? 'WAITING' : c.status} text={c.status === 'NOT_ANALYZED' ? 'Not analysed' : undefined} size="sm" /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {incidents && !incidents.length && (
            <StateBlock kind="empty" compact title="No investigations yet">Create the demo case or import calibrated inputs.</StateBlock>
          )}
        </div>
        <MaritimeMap analysis={null} geography={geography} global incidents={incidents || []} onCase={onOpen} height={420} timeline={false} />
      </div>
      <div className="split even">
        <Section title="Reference layers" actions={<button className="btn btn-quiet btn-sm" onClick={onGeography}>Import layer</button>}>
          <p>{geography?.features.length || 0} geographic features loaded (countries, receptors and any imported layers).</p>
          <p className="fine">Natural Earth countries are a cartographic basemap, not legal maritime boundaries. Import a sourced EEZ layer for jurisdiction.</p>
        </Section>
        <Section title="Supported source datasets">
          <ul className="plain">
            {inventory?.sources.map((s: Json) => (
              <li key={s.name}>
                <a className="link" href={s.url} target="_blank" rel="noreferrer">{s.name}</a>
                <span className="cell-note">{s.note || s.schema}</span>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Reports                                                             */
/* ------------------------------------------------------------------ */

export function Reports({ a, truth }: { a: Json; truth: Json | null }) {
  const lead = a.vessels[0];
  const exposed = a.impact.receptors.filter((r: Json) => r.first_overlap_h !== null);
  return (
    <>
      <PageHeader
        eyebrow="Evidence"
        title="Reports"
        question="What can be handed to an authority — and what does it claim?"
        actions={
          <>
            <a className="btn btn-primary" href={`${a.assets}/report.pdf`}>
              <FileText size={15} /> Forensic report (PDF)
            </a>
            <a className="btn btn-quiet" href={`${a.assets}/evidence.zip`}>
              <ArrowDownToLine size={15} /> Export dossier (ZIP)
            </a>
          </>
        }
      />
      <article className="document">
        <header>
          <span className="eyebrow">Investigation record · {a.data_label}</span>
          <h2>{a.name}</h2>
          <p className="mono small">Run {a.run_id} · analysis hash {a.analysis_hash}</p>
        </header>
        <section>
          <h3>1 · Observation</h3>
          <p>
            {a.spill.sensor} acquired {time(a.observation_time)}. A {a.spill.area_km2.toFixed(2)} km² dark-region candidate was segmented at{' '}
            {coordinate(a.spill.centroid)}. <b>Oil candidate — classification pending</b>; pollutant identity unknown.
          </p>
        </section>
        <section>
          <h3>2 · Modeled origin</h3>
          <p>
            Hindcast under an assumed release window ({shortTime(a.origin.release_window[0])} – {shortTime(a.origin.release_window[1])})
            gives a modeled origin region centred {coordinate(a.origin.centroid)} with a 90% radius of {a.origin.radius90_km} km.
          </p>
        </section>
        <section>
          <h3>3 · Vessel screening</h3>
          <p>
            {a.vessels.length} AIS-tracked vessels were screened. {lead ? `${lead.name} (MMSI ${lead.mmsi}) has the highest investigative relevance score, ${lead.score}/100. ` : ''}
            The score is not a probability of culpability.
          </p>
        </section>
        <section>
          <h3>4 · Adversarial challenge</h3>
          <p>
            {truth?.challenged
              ? `TruthLoop ran ${truth.stability.challenges_run} challenges on ${time(truth.challenged_at)}; conclusion stability ${truth.stability.state.toLowerCase()}. ${truth.stability.reasons[0]}`
              : 'The leading conclusion has not been challenged for this run. Run TruthLoop before relying on it.'}
          </p>
        </section>
        <section>
          <h3>5 · Potential exposure</h3>
          <p>
            {exposed.length
              ? `${exposed.length} loaded receptor(s) overlap the conditional forecast envelope, first ${exposed[0].name} at +${exposed[0].first_overlap_h} h. Overlap is potential exposure, not confirmed damage.`
              : 'No loaded receptor overlaps the forecast envelope within the sampled horizons.'}
          </p>
        </section>
        <section>
          <h3>6 · Limitations</h3>
          <ul className="limitations">
            {a.limitations.map((l: string) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </section>
        <footer>
          <a className="link" href={`/api/v1/cases/${a.case_id}/response-evidence?run_id=${a.run_id}`}>Evidence + response scenarios (ZIP)</a>
          <a className="link" href={`${a.assets}/checksums.sha256`}>SHA-256 manifest</a>
          <a className="link" href={`${a.assets}/analysis.json`}>Analysis JSON</a>
        </footer>
      </article>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Provenance                                                          */
/* ------------------------------------------------------------------ */

export function Provenance({ a }: { a: Json }) {
  const [audit, setAudit] = useState<Json[] | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    api<Json[]>(`/cases/${a.case_id}/audit`).then(setAudit).catch((e) => setError(e.message));
  }, [a.run_id]);
  return (
    <>
      <PageHeader eyebrow="Evidence" title="Provenance" question="How can this investigation be proven and reproduced?">
        <p className="mono small wrap">Analysis hash {a.analysis_hash}</p>
      </PageHeader>
      <Section title="Inputs" note="Every input is snapshotted into the run folder and hashed before analysis.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Role</th>
                <th>File</th>
                <th>Source type</th>
                <th>SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {a.provenance.inputs.map((i: Json) => (
                <tr key={i.role}>
                  <td><b>{i.role.replace('_', ' ')}</b></td>
                  <td>{i.name}</td>
                  <td><Tag tone={i.source_type === 'REAL' ? 'ok' : 'demo'}>{i.source_type.toLowerCase()}</Tag></td>
                  <td className="mono small wrap">{i.sha256}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      <div className="split even">
        <Section title="Algorithm manifest" note="Hashes of the exact backend code that produced this run.">
          <dl className="kv compact">
            {Object.entries(a.provenance.code_hashes).map(([k, v]) => (
              <div className="kv-row" key={k}>
                <dt className="mono">{k}</dt>
                <dd className="mono small">{String(v).slice(0, 20)}…</dd>
              </div>
            ))}
          </dl>
        </Section>
        <Section title="Model parameters">
          <pre>{JSON.stringify({ hindcast: a.provenance.parameters, forecast: a.provenance.forecast_parameters }, null, 2)}</pre>
        </Section>
      </div>
      <Section title="Chain of custody" note="Hash-linked audit events: each hash covers the previous hash, time, action and details.">
        {error ? (
          <StateBlock kind="error" compact title="Audit trail unavailable">{error}</StateBlock>
        ) : !audit ? (
          <StateBlock kind="loading" compact title="Reading audit trail" />
        ) : (
          <ol className="audit">
            {audit.map((e) => (
              <li key={e.seq}>
                <time>{shortTime(e.time)}</time>
                <span>{e.action.replaceAll('_', ' ')}</span>
                <code title={e.hash}>{e.hash.slice(0, 16)}</code>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */

export function Settings({
  health,
  live,
  windage,
  setWindage,
  busy,
  canRun,
  onRun,
}: {
  health: Json | null;
  live: Json | null;
  windage: number;
  setWindage: (n: number) => void;
  busy: boolean;
  canRun: boolean;
  onRun: () => void;
}) {
  const cat = provider(live, 'sentinel1_catalogue');
  const imagery = provider(live, 'sentinel1_imagery');
  const ocean = provider(live, 'ocean_model');
  return (
    <>
      <PageHeader eyebrow="System" title="Settings" question="How is the analysis configured, and which live sources are connected?" />
      <div className="split even">
        <Section title="Analysis">
          <div className="sensitivity">
            <label>
              Windage coefficient <b className="mono">{(windage * 100).toFixed(1)}%</b>
              <input type="range" min="0" max="0.1" step="0.005" value={windage} onChange={(e) => setWindage(+e.target.value)} />
            </label>
            <button className="btn btn-quiet" disabled={!canRun || busy} onClick={onRun}>Apply to a new run</button>
          </div>
          <p className="fine">Changes take effect on the next run; previous runs stay versioned and hashed.</p>
        </Section>
        <Section title="Live data sources">
          <dl className="kv">
            <dt>Sentinel-1 catalogue</dt>
            <dd><Status state={cat?.status} size="sm" /> public CDSE STAC · polling {cat?.poll_interval_minutes ?? 15} min (OCEANEYE_CATALOGUE_POLL_MINUTES)</dd>
            <dt>Calibrated SAR</dt>
            <dd><Status state={imagery?.status} size="sm" /> CDSE_CLIENT_ID / CDSE_CLIENT_SECRET</dd>
            <dt>Ocean currents</dt>
            <dd><Status state={ocean?.status} size="sm" /> copernicusmarine toolbox + COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD</dd>
            <dt>Scheduler</dt>
            <dd>{live?.scheduler?.running ? 'Running' : 'Stopped'} · tick every {live?.scheduler?.granularity_seconds ?? 30} s</dd>
          </dl>
          <p className="fine">Credentials are read from the backend environment (.env). They are never sent to the browser.</p>
        </Section>
      </div>
      <Section title="System">
        <dl className="kv compact">
          {health &&
            Object.entries(health).map(([k, v]) => (
              <div className="kv-row" key={k}>
                <dt>{k.replaceAll('_', ' ')}</dt>
                <dd>{String(v)}</dd>
              </div>
            ))}
        </dl>
        <p className="fine">Local single-user application. Evidence stays on this computer.</p>
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Forms                                                               */
/* ------------------------------------------------------------------ */

export function ImportForm({ onDone }: { onDone: (id: string) => Promise<void> }) {
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
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  return (
    <form className="form-grid-2" onSubmit={submit}>
      <p className="fine span-2">Calibrated, georeferenced SAR GeoTIFF · MarineCadastre-compatible AIS CSV · time-indexed forcing JSON. All times UTC.</p>
      <label className="span-2">
        Investigation name
        <input name="name" required defaultValue="Imported maritime investigation" />
      </label>
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
          <option value="sigma0_db">σ⁰ in decibels</option>
          <option value="sigma0_linear">σ⁰ linear</option>
        </select>
      </label>
      <label className="span-2">
        SAR observation (UTC)
        <input type="datetime-local" name="observation" required defaultValue="2025-08-12T14:20" />
      </label>
      <label>
        Assumed release from
        <input type="datetime-local" name="start" required defaultValue="2025-08-11T20:00" />
      </label>
      <label>
        Assumed release until
        <input type="datetime-local" name="end" required defaultValue="2025-08-11T23:00" />
      </label>
      <label className="span-2">
        SAR GeoTIFF
        <input type="file" name="satellite" accept=".tif,.tiff" required />
      </label>
      <label>
        AIS CSV
        <input type="file" name="ais_file" accept=".csv" required />
      </label>
      <label>
        Forcing JSON
        <input type="file" name="environment" accept=".json" required />
      </label>
      {error && <p className="form-error span-2" role="alert">{error}</p>}
      <button className="btn btn-primary span-2" disabled={working}>{working ? 'Validating inputs…' : 'Create investigation'}</button>
    </form>
  );
}

export function GeographyForm({ onDone }: { onDone: () => Promise<void> }) {
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  return (
    <form
      className="form-grid-2"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
          await api('/geography/import', { method: 'POST', body: new FormData(e.currentTarget) });
          await onDone();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className="span-2">
        WGS84 GeoJSON
        <input type="file" name="file" accept=".json,.geojson" required />
      </label>
      <label>
        Publisher
        <input name="source" required />
      </label>
      <label>
        Dataset version
        <input name="version" required />
      </label>
      <label>
        Layer kind
        <select name="kind">
          {['country', 'sea', 'ocean', 'port', 'eez', 'coastline', 'protected_area', 'coral', 'mangrove', 'seagrass', 'habitat', 'species_range', 'sensitive_ecosystem', 'fishery', 'infrastructure', 'shipping_corridor'].map((k) => (
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
      {error && <p className="form-error span-2" role="alert">{error}</p>}
      <button className="btn btn-primary span-2" disabled={busy}>{busy ? 'Importing…' : 'Validate & import'}</button>
    </form>
  );
}
