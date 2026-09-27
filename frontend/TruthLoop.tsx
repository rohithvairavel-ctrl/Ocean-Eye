import { useEffect, useState } from 'react';
import { ArrowRight, Check, FlaskConical, Plus, RotateCcw } from 'lucide-react';
import { Json, post, shortTime, time, sentence } from './api';
import { Bar, PageHeader, Section, StateBlock, Status, Tag } from './ui';

const STABILITY_NOTE: Record<string, string> = {
  ROBUST: 'The leading candidate survived every challenge that could be run, with a clear score margin.',
  MODERATE: 'The leading candidate survived every challenge, but its margin over the next candidate is thin.',
  FRAGILE: 'At least one challenge displaces the leading candidate. Treat the lead as provisional.',
  INDETERMINATE: 'This run lacks the evidence needed to run a meaningful challenge.',
  NOT_CHALLENGED: 'No challenge has been run for this analysis yet. Stability is unknown, not assumed.',
};

function Ranking({ rows, lead, title }: { rows: Json[] | null; lead?: string; title: string }) {
  return (
    <div className="ranking">
      <span className="ranking-title">{title}</span>
      {!rows ? (
        <p className="muted">Not available for this challenge.</p>
      ) : (
        <ol>
          {rows.slice(0, 4).map((r) => (
            <li key={r.mmsi} className={r.mmsi === lead ? 'is-lead' : ''}>
              <span className="ranking-name">{r.name}</span>
              <span className="mono">{r.score.toFixed(1)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default function TruthLoop({
  a,
  truth,
  truthError,
  setTruth,
  live,
  refreshLive,
  navigate,
  onOpenVessel,
}: {
  a: Json;
  truth: Json | null;
  truthError: string;
  setTruth: (t: Json) => void;
  live: Json | null;
  refreshLive: () => void;
  navigate: (p: string) => void;
  onOpenVessel: (mmsi: string) => void;
}) {
  const [challenging, setChallenging] = useState(false),
    [error, setError] = useState(''),
    [hyp, setHyp] = useState('H1'),
    [chosen, setChosen] = useState<string | null>(null),
    [adding, setAdding] = useState<string | null>(null),
    [added, setAdded] = useState<Record<string, string>>({});

  useEffect(() => {
    if (truth?.challenged && !chosen) {
      const first = truth.challenges.find((c: Json) => c.ranking_changed && c.id !== 'exclude_strongest_candidate');
      setChosen((first || truth.challenges[0])?.id || null);
    }
  }, [truth?.sha256]);

  const challenge = async () => {
    setChallenging(true);
    setError('');
    try {
      const result = await post(`/cases/${a.case_id}/truthloop/challenge?run_id=${a.run_id}`);
      setChosen(null);
      setTruth(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChallenging(false);
    }
  };

  const addToWatch = async (candidateId: string) => {
    setAdding(candidateId);
    setError('');
    try {
      const result = await post(`/cases/${a.case_id}/next-observations/${candidateId}/watch?run_id=${a.run_id}`);
      setAdded((m) => ({ ...m, [candidateId]: result.already_watching ? 'Already watching' : 'Added to watch' }));
      refreshLive();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(null);
    }
  };

  const header = (
    <PageHeader
      eyebrow="TruthLoop · adversarial evidence & falsification"
      title="How stable is our current explanation?"
      question="TruthLoop tries to break the leading conclusion: it removes evidence, excludes the strongest candidate and varies assumptions, then recomputes the real ranking each time."
      actions={
        <button
          className={`btn btn-primary btn-lg ${challenging ? 'is-busy' : ''}`}
          onClick={challenge}
          disabled={challenging || !a.vessels.length}
        >
          {truth?.challenged ? <RotateCcw size={16} /> : <FlaskConical size={16} />}
          {challenging ? 'Challenging conclusion…' : truth?.challenged ? 'Challenge again' : 'Challenge this conclusion'}
        </button>
      }
    />
  );

  if (truthError && !truth)
    return (
      <>
        {header}
        <StateBlock kind="error" title="TruthLoop could not load" action={<button className="btn btn-quiet" onClick={() => location.reload()}>Retry</button>}>
          {truthError} The investigation itself is unaffected.
        </StateBlock>
      </>
    );
  if (!truth)
    return (
      <>
        {header}
        <StateBlock kind="loading" title="Assembling competing hypotheses">Reading this run's evidence…</StateBlock>
      </>
    );

  const lead = truth.baseline_ranking?.[0];
  const hypothesis = truth.hypotheses.find((h: Json) => h.id === hyp) || truth.hypotheses[0];
  const selected = truth.challenges.find((c: Json) => c.id === chosen);
  const state = truth.stability.state;
  const watched = new Set((live?.watch_areas || []).map((w: Json) => w.origin_ref).filter(Boolean));

  return (
    <>
      {header}
      {error && <StateBlock kind="error" compact title="Challenge failed">{error} Nothing was recorded; retrying is safe.</StateBlock>}

      <div className={`verdict tone-band-${state.toLowerCase()} ${challenging ? 'is-busy' : ''}`} aria-live="polite">
        <div className="verdict-lead">
          <span className="eyebrow">Leading explanation · H1</span>
          <strong>{lead ? lead.name : 'No AIS-tracked vessel'}</strong>
          {lead && <span className="muted">Investigative relevance {lead.score}/100 — not a probability of culpability</span>}
        </div>
        <div className="verdict-state">
          <span className="eyebrow">Conclusion stability</span>
          {challenging ? <Status state="RUNNING" text="Recomputing challenges" /> : <Status state={state} />}
          <p>{STABILITY_NOTE[state]}</p>
          {truth.challenged && (
            <span className="fine">
              Challenged {time(truth.challenged_at)} · {truth.stability.challenges_run} of {truth.stability.challenges_available} challenges run ·
              record <span className="mono">{truth.sha256.slice(0, 12)}</span>
            </span>
          )}
        </div>
        <div className="verdict-note">Deterministic qualitative class from explicit rules — not statistical confidence.</div>
      </div>

      <div className="truth-grid">
        <Section title="Competing hypotheses" className="truth-hypotheses">
          <ul className="hyp-list" role="listbox" aria-label="Competing hypotheses">
            {truth.hypotheses.map((h: Json) => (
              <li key={h.id}>
                <button
                  role="option"
                  aria-selected={h.id === hyp}
                  className={h.id === hyp ? 'on' : ''}
                  onClick={() => setHyp(h.id)}
                >
                  <span className="hyp-id">{h.id}</span>
                  <span className="hyp-text">
                    <strong>{h.label}</strong>
                    {h.reference?.name && <span className="muted">{h.reference.name}</span>}
                    <span className={`hyp-position ${h.current_position.startsWith('NOT') ? 'dim' : ''}`}>
                      {h.current_position.split('--')[0].trim().toLowerCase().replace(/^\w/, (c: string) => c.toUpperCase())}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          title="Challenges"
          note={truth.challenged ? 'Select a challenge to compare the baseline ranking with the recomputed one.' : undefined}
          className="truth-challenges"
        >
          {!truth.challenged ? (
            <StateBlock kind="waiting" title="The conclusion has not been challenged">
              Eight challenges are ready: remove proximity, AIS-gap, speed-change and course-change evidence; exclude the strongest
              candidate; expand origin uncertainty; shift the release time; vary forcing within its stated uncertainty.
            </StateBlock>
          ) : (
            <>
              <ul className="challenge-list">
                {truth.challenges.map((c: Json) => (
                  <li key={c.id}>
                    <button className={c.id === chosen ? 'on' : ''} onClick={() => setChosen(c.id)}>
                      <span>{c.label}</span>
                      {!c.available ? (
                        <Tag>Not run</Tag>
                      ) : c.id === 'exclude_strongest_candidate' ? (
                        <Tag tone="neutral">Counterfactual</Tag>
                      ) : c.ranking_changed ? (
                        <Tag tone="hazard">Lead changes</Tag>
                      ) : (
                        <Tag tone="ok">Lead holds</Tag>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
              {selected && (
                <div className="compare enter" key={selected.id}>
                  <p className="compare-desc">{selected.description}</p>
                  {selected.available ? (
                    <div className="compare-cols">
                      <Ranking rows={selected.baseline_ranking} lead={lead?.mmsi} title="Baseline" />
                      <ArrowRight size={18} className="compare-arrow" aria-hidden />
                      <Ranking rows={selected.after_ranking} lead={lead?.mmsi} title="After challenge" />
                    </div>
                  ) : null}
                  <p className={`compare-explain ${selected.ranking_changed && selected.id !== 'exclude_strongest_candidate' ? 'reversal' : ''}`}>
                    {selected.explanation}
                  </p>
                  <details>
                    <summary>Method</summary>
                    <p className="fine">{selected.method}</p>
                  </details>
                </div>
              )}
              <ul className="reasons">
                {truth.stability.reasons.map((r: string, i: number) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </>
          )}
        </Section>

        <Section title={`Evidence · ${hypothesis.id}`} className="truth-evidence">
          <div className="evidence-lists">
            {[
              ['Supporting', hypothesis.supporting_evidence, 'ok'],
              ['Contradicting', hypothesis.contradicting_evidence, 'hazard'],
              ['Missing', hypothesis.missing_evidence, 'warn'],
            ].map(([name, items, tone]: any) => (
              <div key={name}>
                <h3 className={`tone-text-${tone}`}>{name}</h3>
                {items.length ? (
                  <ul>
                    {items.map((s: string, i: number) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">None recorded in this run.</p>
                )}
              </div>
            ))}
            {hypothesis.assumptions.length > 0 && (
              <p className="fine">Assumes: {hypothesis.assumptions.join('; ')}.</p>
            )}
            {hypothesis.uncertainties.length > 0 && <p className="fine">Uncertainty: {hypothesis.uncertainties.join('; ')}.</p>}
            {hypothesis.reference?.mmsi && (
              <button className="btn btn-quiet btn-sm" onClick={() => onOpenVessel(hypothesis.reference.mmsi)}>
                Inspect evidence
              </button>
            )}
          </div>
        </Section>
      </div>

      <Section
        title="Evidence fragility"
        note={
          truth.most_dependent_factor
            ? `The leading candidate's score depends most on ${truth.most_dependent_factor.toLowerCase()}.`
            : undefined
        }
      >
        <div className="fragility">
          {truth.fragility.map((f: Json) => (
            <div key={f.factor} className="fragility-row">
              <span className="fragility-name">{f.factor}</span>
              <Bar value={f.share_of_score} tone={f.influence === 'HIGH' ? 'hazard' : f.influence === 'MEDIUM' ? 'warn' : 'neutral'} />
              <span className="mono">{f.share_of_score}%</span>
              <Tag tone={f.influence === 'HIGH' ? 'hazard' : f.influence === 'MEDIUM' ? 'warn' : 'neutral'}>{f.influence.toLowerCase()} influence</Tag>
            </div>
          ))}
          <p className="fine">Share of the leading candidate's relevance score contributed by each factor (value × weight). Derived, not estimated.</p>
        </div>
      </Section>

      <Section
        title="Evidence needed to distinguish the hypotheses"
        note="Each gap maps to a Next-Best-Observation target. Adding it to Copernicus Watch closes the loop: new SAR → auto-analyze → updated investigation."
        actions={<button className="btn btn-quiet btn-sm" onClick={() => navigate('Next Observation')}>Open Next Observation</button>}
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Evidence needed</th>
                <th>Why</th>
                <th>Distinguishes</th>
                <th>AOI</th>
                <th>Source · window</th>
                <th aria-label="Action" />
              </tr>
            </thead>
            <tbody>
              {truth.discriminating_evidence.map((e: Json) => {
                const ref = `nbo:${a.case_id}:${e.candidate_id}`;
                const already = watched.has(ref) || added[e.candidate_id];
                return (
                  <tr key={e.candidate_id}>
                    <td>
                      <strong>{sentence(e.evidence_needed)}</strong>
                      <span className="cell-note">{e.uncertainty_it_may_reduce}</span>
                    </td>
                    <td className="wide">{e.why}</td>
                    <td>{e.distinguishes_hypotheses.join(' · ') || '—'}</td>
                    <td className="mono small">
                      {e.aoi.center[1].toFixed(2)}, {e.aoi.center[0].toFixed(2)}
                      <span className="cell-note">r {e.aoi.radius_km} km</span>
                    </td>
                    <td className="small">
                      {e.recommended_data_source}
                      <span className="cell-note">next acquisition after {shortTime(a.observation_time)}</span>
                    </td>
                    <td>
                      {already ? (
                        <span className="done">
                          <Check size={13} /> {added[e.candidate_id] || 'Watching'}
                        </span>
                      ) : (
                        <button className="btn btn-quiet btn-sm" disabled={adding === e.candidate_id} onClick={() => addToWatch(e.candidate_id)}>
                          <Plus size={13} /> {adding === e.candidate_id ? 'Adding…' : 'Add to watch'}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}
