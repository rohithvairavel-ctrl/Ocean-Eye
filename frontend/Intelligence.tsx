import { useState } from 'react';
import { Json, coordinate, post, shortTime, time } from './api';
import MaritimeMap from './MaritimeMap';
import { Metric, PageHeader, Section, StateBlock, Status, Tag } from './ui';

/* ------------------------------------------------------------------ */
/* Alerts                                                             */
/* ------------------------------------------------------------------ */

export function Alerts({ a, data, error, navigate }: { a: Json; data: Json | null; error: string; navigate: (p: string) => void }) {
  const header = (
    <PageHeader
      eyebrow="Command"
      title="Alerts"
      question="What needs a decision — and on what evidence?"
    >
      <p className="notice">Rule-based findings anchored to the observation at {time(a.observation_time)}. They are not live warnings; external notifications are not configured.</p>
    </PageHeader>
  );
  if (error) return <>{header}<StateBlock kind="error" title="Alerts unavailable">{error}</StateBlock></>;
  if (!data) return <>{header}<StateBlock kind="loading" title="Evaluating alert rules" /></>;
  const tone: Record<string, any> = { CRITICAL: 'hazard', HIGH: 'hazard', WATCH: 'warn', INFORMATIONAL: 'neutral' };
  return (
    <>
      {header}
      <ol className="alert-list">
        {data.alerts.map((e: Json) => (
          <li key={e.id} className={`alert-item sev-${e.severity.toLowerCase()}`}>
            <div className="alert-sev">
              <Tag tone={tone[e.severity]}>{e.severity.toLowerCase()}</Tag>
            </div>
            <div className="alert-body">
              <h3>{e.title}</h3>
              <p>{e.why}</p>
              <p className="alert-next">
                <b>Next action</b> {e.next_action}
              </p>
              <span className="fine">
                {shortTime(e.event_time)} · {coordinate(e.location)} · rule {e.rule_id}
              </span>
              <details>
                <summary>Data, assumptions and uncertainty</summary>
                <pre>{JSON.stringify({ data_used: e.data_used, assumptions: e.assumptions, uncertainty: e.uncertainty }, null, 2)}</pre>
              </details>
            </div>
          </li>
        ))}
      </ol>
      <p className="fine">
        Re-observation targets live in <button className="link" onClick={() => navigate('Next Observation')}>Next Observation</button>.
      </p>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Ecological exposure                                                */
/* ------------------------------------------------------------------ */

export function Ecology({ a, data, error, geography }: { a: Json; data: Json | null; error: string; geography: Json | null }) {
  const header = (
    <PageHeader
      eyebrow="Protect"
      title="Ecological exposure"
      question="What may be exposed, and when would the forecast envelope first reach it?"
    >
      <p className="notice">Potential ecological exposure = overlap between the conditional forecast envelope and loaded receptor layers. It is not confirmed ecological damage and says nothing about species impact.</p>
    </PageHeader>
  );
  if (error) return <>{header}<StateBlock kind="error" title="Exposure screening unavailable">{error}</StateBlock></>;
  if (!data) return <>{header}<StateBlock kind="loading" title="Intersecting forecast with receptor layers" /></>;
  const receptors: Json[] = data.ecology.receptors;
  const horizon = Math.max(...a.forecast.steps.map((s: Json) => s.hours), 48);
  const exposed = receptors.filter((r) => r.first_overlap_h !== null);
  return (
    <>
      {header}
      <div className="split">
        <div>
          <div className="facts">
            <Metric label="Receptors screened" value={receptors.length} />
            <Metric label="Potential exposure" value={exposed.length} tone={exposed.length ? 'warn' : undefined} />
            <Metric label="Earliest onset" value={exposed.length ? `+${Math.min(...exposed.map((r) => r.first_overlap_h))}` : '—'} unit={exposed.length ? 'h' : undefined} hint="first overlapping forecast sample" />
          </div>
          {!receptors.length ? (
            <StateBlock kind="empty" title="No receptor layer loaded for this AOI">
              Import documented protected-area, habitat or fishery layers in Cases & Data, then re-run the investigation.
            </StateBlock>
          ) : (
            <Section title="Receptor timeline" note={`Forecast samples at ${a.forecast.steps.map((s: Json) => '+' + s.hours + ' h').join(', ')}. Onset may fall between samples.`}>
              <div className="receptor-timeline">
                <div className="rt-row rt-head" aria-hidden>
                  <span />
                  <span className="rt-axis">
                    {[0, 0.25, 0.5, 0.75, 1].map((f) => (
                      <span key={f} style={{ left: f * 100 + '%' }}>+{Math.round(f * horizon)} h</span>
                    ))}
                  </span>
                  <span />
                </div>
                {receptors.map((r, i) => (
                  <div className="rt-row" key={i}>
                    <span className="rt-name">
                      {r.name}
                      <span className="cell-note">{String(r.kind).replace('_', ' ')} · {r.source_type.toLowerCase()}</span>
                    </span>
                    <span className="rt-track">
                      {r.first_overlap_h !== null ? (
                        <i
                          className="rt-bar"
                          style={{ left: `min(${(r.first_overlap_h / horizon) * 100}%, calc(100% - 8px))` }}
                          title={`First overlap +${r.first_overlap_h} h`}
                        />
                      ) : null}
                    </span>
                    <span className="rt-when">
                      {r.first_overlap_h !== null ? <>+{r.first_overlap_h} h<span className="cell-note">{shortTime(r.first_overlap_time)}</span></> : <span className="muted">No sampled overlap</span>}
                    </span>
                  </div>
                ))}
              </div>
            </Section>
          )}
          <StateBlock kind={data.ecology.species_assessment.startsWith('UNAVAILABLE') ? 'empty' : 'waiting'} compact title="Species-level assessment">
            {data.ecology.species_assessment.startsWith('UNAVAILABLE')
              ? 'No authoritative ecological layer is loaded for this AOI, so no species are named or inferred.'
              : data.ecology.species_assessment}{' '}
            {data.ecology.biodiversity_coverage}.
          </StateBlock>
          <p className="fine">{data.ecology.limitations}</p>
        </div>
        <MaritimeMap analysis={a} geography={geography} layers={{ receptors: true, forecast: true, lead: false, selected: false, aoi: false }} height={560} forecastHour={48} label="Forecast × receptors" />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Response planning                                                   */
/* ------------------------------------------------------------------ */

export function ResponsePlanning({
  a,
  geography,
  scenarios,
  reload,
}: {
  a: Json;
  geography: Json | null;
  scenarios: Json[] | null;
  reload: () => Promise<unknown>;
}) {
  const [kind, setKind] = useState('dispatch'),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<string>(''),
    [point, setPoint] = useState<number[] | null>(null);
  const list = scenarios || [];
  const scenario = list.find((s) => s.id === selected) || list[0] || null;
  const baselineExposed = (a.impact.receptors as Json[]).filter((r) => r.first_overlap_h !== null);

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
      await reload();
      setSelected(created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Protect"
        title="Response planning"
        question="What could operators potentially do, and how does timing compare with no action?"
      >
        <p className="notice">Geospatial response-planning scenarios compare travel and arrival timing against the conditional forecast. Interception, removal and cleanup effectiveness are not modeled or guaranteed.</p>
      </PageHeader>
      <div className="split planning">
        <Section title="New planning scenario" surface>
          <form onSubmit={submit} className="form-grid-2">
            <label className="span-2">
              Scenario name
              <input name="name" required maxLength={100} defaultValue="Dispatch from nearest port" />
            </label>
            <label className="span-2">
              Intervention
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {[
                  ['no_action', 'No action (baseline)'],
                  ['dispatch', 'Dispatch response vessel'],
                  ['boom', 'Containment boom'],
                  ['interception', 'Potential interception'],
                  ['delayed_response', 'Delayed response'],
                ].map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
            <label className="span-2">
              Asset
              <input name="asset" required defaultValue="Operator-assumed response vessel" />
            </label>
            <label className="span-2">
              Availability basis
              <input name="source" required defaultValue="Operator assumption; availability unverified" />
            </label>
            <label>
              Departure longitude
              <input name="lon" type="number" step="any" min="-180" max="180" defaultValue="80.32" required={kind !== 'no_action'} disabled={kind === 'no_action'} />
            </label>
            <label>
              Departure latitude
              <input name="lat" type="number" step="any" min="-90" max="90" defaultValue="13.08" required={kind !== 'no_action'} disabled={kind === 'no_action'} />
            </label>
            <label>
              Speed (kn)
              <input name="speed" type="number" min="0.1" max="60" step="0.1" defaultValue="12" required />
            </label>
            <label>
              Speed uncertainty (%)
              <input name="uncertainty" type="number" min="0" max="80" defaultValue="20" required />
            </label>
            <label>
              Departure delay (h)
              <input name="delay" type="number" min="0" max="96" step="0.1" defaultValue="0" required />
            </label>
            <label>
              Setup time (h)
              <input name="setup" type="number" min="0" max="48" step="0.1" defaultValue="1" required />
            </label>
            <label className="span-2">
              Forecast target horizon
              <select name="horizon" defaultValue="24">
                {[6, 12, 24, 48].map((h) => (
                  <option key={h} value={h}>+{h} h</option>
                ))}
              </select>
            </label>
            <p className="fine span-2">
              Target: {point ? coordinate(point) : 'forecast envelope centroid (modeled)'} — click the map to choose a point.
              {point && (
                <button type="button" className="link" onClick={() => setPoint(null)}> Use centroid</button>
              )}
            </p>
            {error && <p className="form-error span-2" role="alert">{error}</p>}
            <button className="btn btn-primary span-2" disabled={busy}>{busy ? 'Comparing response scenarios…' : 'Compare response'}</button>
          </form>
        </Section>
        <MaritimeMap
          analysis={a}
          geography={geography}
          response={scenario}
          onPlanningPoint={setPoint}
          focus={point}
          layers={{ lead: false, selected: false, aoi: false, receptors: true }}
          height={640}
          label="Planning map · click to set target"
        />
      </div>

      <Section title="Scenario comparison" note="Each scenario is an immutable, hashed planning record bound to this run.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Travel</th>
                <th>Modeled arrival window</th>
                <th>Ready margin to target</th>
                <th>Potential exposure</th>
                <th>After intervention</th>
              </tr>
            </thead>
            <tbody>
              <tr className="baseline-row">
                <td><strong>No action</strong><span className="cell-note">Conditional forecast baseline</span></td>
                <td>—</td>
                <td>—</td>
                <td>—</td>
                <td>{baselineExposed.length} receptor{baselineExposed.length === 1 ? '' : 's'}{baselineExposed[0] ? `, first +${baselineExposed[0].first_overlap_h} h` : ''}</td>
                <td>—</td>
              </tr>
              {list.map((s) => (
                <tr key={s.id} className={scenario?.id === s.id ? 'on' : ''} onClick={() => setSelected(s.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setSelected(s.id)}>
                  <td><strong>{s.input.name}</strong><span className="cell-note">{s.input.kind.replace('_', ' ')} · {s.asset?.availability ? 'availability unverified' : 'baseline'}</span></td>
                  <td className="mono">{s.travel_km === null ? '—' : `${s.travel_km.toFixed(1)} km`}</td>
                  <td className="mono">{s.arrival_window_h ? `${s.arrival_window_h[0].toFixed(1)}–${s.arrival_window_h[1].toFixed(1)} h` : '—'}</td>
                  <td>{s.margin_to_target_h === null ? '—' : <Tag tone={s.margin_to_target_h <= 2 ? 'warn' : 'ok'}>{s.margin_to_target_h.toFixed(1)} h</Tag>}</td>
                  <td>{s.baseline.receptor_exposure.filter((r: Json) => r.first_overlap_h !== null).length} receptors (baseline)</td>
                  <td><Status state="UNAVAILABLE" text="Not modeled" size="sm" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!list.length && <StateBlock kind="empty" compact title="No planning scenario yet">Create one above to compare timing against the no-action baseline.</StateBlock>}
        {scenario && (
          <div className="scenario-detail enter" key={scenario.id}>
            <p>{scenario.comparison}</p>
            {scenario.opportunities?.length > 0 && (
              <ul className="plain">
                {scenario.opportunities.map((o: Json, i: number) => (
                  <li key={i}>+{o.hours} h: {o.status.replaceAll('_', ' ').toLowerCase()}{o.conservative_lead_h !== null && o.conservative_lead_h !== undefined ? ` · lead ${o.conservative_lead_h} h` : ''}</li>
                ))}
              </ul>
            )}
            <p className="fine">Assumptions: {scenario.assumptions.join('; ')}. Record <span className="mono">{scenario.sha256?.slice(0, 16)}</span>.</p>
            <a className="btn btn-quiet btn-sm" href={`/api/v1/scenarios/${scenario.id}/export`}>Export scenario record</a>
          </div>
        )}
      </Section>
    </>
  );
}
