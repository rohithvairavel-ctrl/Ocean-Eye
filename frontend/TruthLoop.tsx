import { useEffect, useState } from 'react';
import { FlaskConical, ShieldAlert, ArrowRight } from 'lucide-react';
import { api, post, Json } from './api';

const STABILITY_COPY: Record<string, { badge: string; note: string }> = {
  ROBUST: {
    badge: 'green',
    note: 'The top-ranked candidate survives every challenge that could be run against this run.',
  },
  MODERATE: {
    badge: 'amber',
    note: 'The top-ranked candidate survives every challenge, but the evidence margin is thin.',
  },
  FRAGILE: {
    badge: 'red',
    note: 'At least one challenge reverses the top-ranked candidate.',
  },
  INDETERMINATE: {
    badge: 'purple',
    note: 'This run does not have enough evidence to run a meaningful challenge.',
  },
};

function RankingList({ ranking, highlight }: { ranking: Json[] | null; highlight?: string }) {
  if (!ranking) return <p className="muted micro">Not available for this run.</p>;
  return (
    <ol className="truthloop-ranking">
      {ranking.map((v, i) => (
        <li key={v.mmsi} className={v.mmsi === highlight ? 'lead' : ''}>
          <span>{i + 1}.</span> {v.name} <strong>{v.score}/100</strong>
        </li>
      ))}
    </ol>
  );
}

function HypothesisCard({ h }: { h: Json }) {
  const open = h.current_position?.toUpperCase().startsWith('OPEN');
  return (
    <article className={'truthloop-hypothesis' + (open ? '' : ' unevaluated')}>
      <header>
        <span className="badge">{h.id}</span>
        <strong>{h.label}</strong>
      </header>
      {h.reference?.name && <p className="micro">{h.reference.name}{h.reference.mmsi ? ` · MMSI ${h.reference.mmsi}` : ''}</p>}
      {h.reference?.distance_km !== undefined && (
        <p className="micro">{h.reference.distance_km} km from candidate</p>
      )}
      {h.supporting_evidence?.length > 0 && (
        <div>
          <span className="truthloop-tag support">Supporting</span>
          <ul>{h.supporting_evidence.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
        </div>
      )}
      {h.contradicting_evidence?.length > 0 && (
        <div>
          <span className="truthloop-tag contradict">Contradicting</span>
          <ul>{h.contradicting_evidence.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
        </div>
      )}
      {h.missing_evidence?.length > 0 && (
        <div>
          <span className="truthloop-tag missing">Missing</span>
          <ul>{h.missing_evidence.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
        </div>
      )}
      {h.assumptions?.length > 0 && <p className="micro">Assumes: {h.assumptions.join('; ')}</p>}
      {h.uncertainties?.length > 0 && <p className="micro">Uncertainty: {h.uncertainties.join('; ')}</p>}
      <p className="truthloop-position">{h.current_position}</p>
    </article>
  );
}

export default function TruthLoop({
  caseId,
  runId,
  onVessel,
}: {
  caseId: string;
  runId: string;
  onVessel: (mmsi: string) => void;
}) {
  const [data, setData] = useState<Json | null>(null);
  const [error, setError] = useState('');
  const [challenging, setChallenging] = useState(false);
  const [watchBusy, setWatchBusy] = useState<string | null>(null);
  const [watchNotice, setWatchNotice] = useState('');

  const load = () => api(`/cases/${caseId}/truthloop?run_id=${runId}`).then(setData).catch((e) => setError(e.message));

  useEffect(() => {
    setData(null);
    setError('');
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, runId]);

  const challenge = async () => {
    setChallenging(true);
    setError('');
    try {
      const result = await post(`/cases/${caseId}/truthloop/challenge?run_id=${runId}`);
      setData(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChallenging(false);
    }
  };

  const addToWatch = async (candidateId: string) => {
    setWatchBusy(candidateId);
    try {
      const result = await post(`/cases/${caseId}/next-observations/${candidateId}/watch?run_id=${runId}`);
      setWatchNotice(`Added "${result.candidate.target_type}" to the Copernicus watch list.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWatchBusy(null);
    }
  };

  if (error) return <p className="limitation">{error}</p>;
  if (!data) return <p className="muted">SYNCING TRUTHLOOP — recomputing from this run's own evidence…</p>;

  const stability = STABILITY_COPY[data.stability.state] || STABILITY_COPY.INDETERMINATE;
  const leadMmsi = data.hypotheses?.[0]?.reference?.mmsi;

  return (
    <div className="truthloop">
      <div className="truthloop-header">
        <div>
          <h2>
            <FlaskConical size={18} /> OCEAN-EYE TRUTHLOOP
          </h2>
          <p className="micro">ADVERSARIAL EVIDENCE &amp; FALSIFICATION ENGINE — {data.method}</p>
        </div>
        <button className="primary" disabled={challenging} onClick={challenge}>
          <ShieldAlert size={15} /> {challenging ? 'Challenging…' : 'Challenge This Conclusion'}
        </button>
      </div>
      {watchNotice && <div className="banner">{watchNotice}</div>}

      <div className="truthloop-layout">
        <section className="intelligence-block truthloop-hypotheses">
          <h3>Competing hypotheses</h3>
          {data.hypotheses.map((h: Json) => (
            <HypothesisCard key={h.id} h={h} />
          ))}
        </section>

        <section className="intelligence-block truthloop-center">
          <h3>Conclusion stability</h3>
          <p>
            <span className={'badge ' + stability.badge}>CONCLUSION STABILITY — {data.stability.state}</span>
          </p>
          <p className="micro">{stability.note}</p>
          <ul className="truthloop-reasons">
            {data.stability.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>

          <h3>Challenges run</h3>
          <div className="truthloop-challenges">
            {data.challenges.map((c: Json) => (
              <div key={c.id} className={'truthloop-challenge' + (c.ranking_changed ? ' reversed' : '')}>
                <header>
                  <strong>{c.label}</strong>
                  {c.available ? (
                    <span className={'badge ' + (c.ranking_changed ? 'red' : 'green')}>
                      {c.ranking_changed ? 'RANKING REVERSES' : 'UNCHANGED'}
                    </span>
                  ) : (
                    <span className="badge">NOT RUN</span>
                  )}
                </header>
                <p className="micro">{c.description}</p>
                {c.available ? (
                  <div className="truthloop-compare">
                    <div>
                      <span className="micro">BASELINE</span>
                      <RankingList ranking={c.baseline_ranking} highlight={leadMmsi} />
                    </div>
                    <ArrowRight size={16} />
                    <div>
                      <span className="micro">AFTER CHALLENGE</span>
                      <RankingList ranking={c.after_ranking} highlight={leadMmsi} />
                    </div>
                  </div>
                ) : (
                  <p className="micro">{c.explanation}</p>
                )}
                <p className="truthloop-explain">{c.explanation}</p>
                <details>
                  <summary>Method</summary>
                  <p className="micro">{c.method}</p>
                </details>
              </div>
            ))}
          </div>
        </section>

        <section className="intelligence-block truthloop-fragility">
          <h3>Evidence fragility</h3>
          <p className="micro">
            Most dependent on: <strong>{data.most_dependent_factor || 'n/a'}</strong>
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Factor</th>
                  <th>Influence</th>
                  <th>Contribution</th>
                </tr>
              </thead>
              <tbody>
                {data.fragility.map((f: Json) => (
                  <tr key={f.factor}>
                    <td>{f.factor}</td>
                    <td>
                      <span className={'badge ' + (f.influence === 'HIGH' ? 'red' : f.influence === 'MEDIUM' ? 'amber' : '')}>
                        {f.influence}
                      </span>
                    </td>
                    <td>{f.contribution}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {leadMmsi && (
            <button className="text-button" onClick={() => onVessel(leadMmsi)}>
              Inspect leading candidate <ArrowRight size={12} />
            </button>
          )}
        </section>
      </div>

      <section className="intelligence-block truthloop-next">
        <h3>Evidence needed next</h3>
        <p className="micro">What would most help distinguish the leading competing hypotheses.</p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Evidence needed</th>
                <th>Why</th>
                <th>Distinguishes</th>
                <th>Source</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.discriminating_evidence.map((e: Json) => (
                <tr key={e.candidate_id}>
                  <td>
                    <strong>{e.evidence_needed}</strong>
                    <br />
                    <small className="muted">{e.uncertainty_it_may_reduce}</small>
                  </td>
                  <td style={{ maxWidth: 280 }}>{e.why}</td>
                  <td>{e.distinguishes_hypotheses.join(', ') || '—'}</td>
                  <td>{e.recommended_data_source}</td>
                  <td>
                    <button
                      className="secondary"
                      disabled={watchBusy === e.candidate_id}
                      onClick={() => addToWatch(e.candidate_id)}
                    >
                      {watchBusy === e.candidate_id ? 'Adding…' : 'Add to Copernicus Watch'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
