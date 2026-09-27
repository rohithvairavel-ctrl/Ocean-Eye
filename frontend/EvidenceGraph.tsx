import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Json, time } from './api';
import { PageHeader, Section, StateBlock, Status, Tag } from './ui';

const STAGES: [string, string][] = [
  ['observe', 'Observation'],
  ['measure', 'SAR candidate'],
  ['reconstruct', 'Origin reconstruction'],
  ['attribute', 'Vessel evidence'],
  ['challenge', 'TruthLoop'],
  ['consequence', 'Consequences'],
  ['next', 'Next observation'],
  ['prove', 'Evidence package'],
];

const EPISTEMIC_TONE: Record<string, any> = {
  OBSERVED: 'hazard',
  DERIVED: 'neutral',
  SCREENING: 'active',
  ASSUMED: 'warn',
  MODELED: 'model',
  PREDICTED: 'forecast',
  HEURISTIC: 'neutral',
  RECORDED: 'ok',
  'TESTED ON DEMAND': 'active',
};

function Value({ value }: { value: any }) {
  if (value === null || value === undefined || value === '') return <span className="muted">—</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">none</span>;
    if (value.every((v) => typeof v !== 'object'))
      return <ul className="plain">{value.map((v, i) => <li key={i}>{String(v)}</li>)}</ul>;
    return <span className="muted">{value.length} records</span>;
  }
  if (typeof value === 'object') return <span className="muted">{Object.keys(value).length} fields</span>;
  if (typeof value === 'number') return <span className="mono">{value}</span>;
  return <span>{String(value)}</span>;
}

export default function EvidenceGraph({
  a,
  truth,
  navigate,
  onOpenVessel,
  data,
  error,
}: {
  a: Json;
  truth: Json | null;
  navigate: (p: string) => void;
  onOpenVessel: (m: string) => void;
  data: Json | null;
  error: string;
}) {
  const [nodeId, setNodeId] = useState('truthloop');
  const wrap = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<{ d: string; hot: boolean; key: string }[]>([]);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const graph = data?.evidence;

  const measure = () => {
    const root = wrap.current;
    if (!root || !graph) return;
    const box = root.getBoundingClientRect();
    const pos: Record<string, DOMRect> = {};
    root.querySelectorAll<HTMLElement>('[data-node]').forEach((el) => {
      pos[el.dataset.node!] = el.getBoundingClientRect();
    });
    const out: { d: string; hot: boolean; key: string }[] = [];
    for (const e of graph.edges) {
      const s = pos[e.source],
        t = pos[e.target];
      if (!s || !t) continue;
      const sx = s.left + s.width / 2 - box.left,
        tx = t.left + t.width / 2 - box.left;
      let d: string;
      if (Math.abs(s.top - t.top) < 8) {
        // same stage: a shallow loop under both nodes
        const y = s.bottom - box.top;
        d = `M${sx},${y} C${sx},${y + 26} ${tx},${y + 26} ${tx},${y}`;
      } else if (t.top > s.top) {
        const y1 = s.bottom - box.top,
          y2 = t.top - box.top,
          mid = Math.max(18, (y2 - y1) / 2);
        d = `M${sx},${y1} C${sx},${y1 + mid} ${tx},${y2 - mid} ${tx},${y2}`;
      } else {
        const y1 = s.top - box.top,
          y2 = t.bottom - box.top,
          mid = Math.max(18, (y1 - y2) / 2);
        d = `M${sx},${y1} C${sx},${y1 - mid} ${tx},${y2 + mid} ${tx},${y2}`;
      }
      out.push({ key: e.source + '>' + e.target, d, hot: e.source === nodeId || e.target === nodeId });
    }
    setPaths(out);
    setDims({ w: root.scrollWidth, h: root.scrollHeight });
  };
  useLayoutEffect(measure, [graph, nodeId]);
  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(measure);
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, [graph, nodeId]);

  const header = (
    <PageHeader
      eyebrow="Investigate"
      title="Evidence graph"
      question="How does each conclusion trace back to an observation — and where is it challenged?"
    />
  );
  if (error) return <>{header}<StateBlock kind="error" title="Evidence graph unavailable">{error}</StateBlock></>;
  if (!graph) return <>{header}<StateBlock kind="loading" title="Linking evidence">Reading run-bound derivations…</StateBlock></>;

  const node = graph.nodes.find((n: Json) => n.id === nodeId) || graph.nodes[0];
  const name = (id: string) => graph.nodes.find((n: Json) => n.id === id)?.label || id;
  const from = graph.edges.filter((e: Json) => e.target === node.id).map((e: Json) => e.source);
  const to = graph.edges.filter((e: Json) => e.source === node.id).map((e: Json) => e.target);
  const meta = node.meta || {};
  const truthState = truth?.challenged ? truth.stability.state : 'NOT_CHALLENGED';

  return (
    <>
      {header}
      <div className="graph-layout">
        <div className="graph" ref={wrap}>
          <svg className="graph-edges" aria-hidden width={dims.w} height={dims.h}>
            {paths.map((p) => (
              <path key={p.key} d={p.d} className={p.hot ? 'hot' : ''} />
            ))}
          </svg>
          {STAGES.map(([stage, title]) => {
            const nodes = graph.nodes.filter((n: Json) => (n.stage || 'consequence') === stage);
            if (!nodes.length) return null;
            return (
              <div className="graph-row" key={stage}>
                <span className="graph-row-title">{title}</span>
                <div className="graph-row-nodes">
                {nodes.map((n: Json) => (
                  <button
                    key={n.id}
                    data-node={n.id}
                    className={`graph-node ep-${String(n.meta?.epistemic_state || '').toLowerCase().replace(/\s/g, '-')} ${n.id === node.id ? 'on' : ''} ${from.includes(n.id) || to.includes(n.id) ? 'linked' : ''}`}
                    onClick={() => setNodeId(n.id)}
                    aria-pressed={n.id === node.id}
                  >
                    <span className="graph-node-kind">{n.meta?.epistemic_state || n.kind}</span>
                    <span className="graph-node-label">{n.label}</span>
                    {n.id === 'truthloop' && <Status state={truthState} size="sm" />}
                  </button>
                ))}
                </div>
              </div>
            );
          })}
        </div>

        <aside className="graph-inspector" aria-label="Node inspector">
          <span className="eyebrow">{node.kind}</span>
          <h2>{node.label}</h2>
          <Tag tone={EPISTEMIC_TONE[meta.epistemic_state] || 'neutral'}>{meta.epistemic_state || 'Unclassified'}</Tag>
          <dl className="kv">
            <dt>Source</dt>
            <dd>{meta.source || '—'}</dd>
            <dt>Timestamp</dt>
            <dd>{meta.timestamp ? time(meta.timestamp) : '—'}</dd>
            <dt>Derived from</dt>
            <dd>{from.length ? from.map(name).join(' · ') : 'Primary observation'}</dd>
            <dt>Informs</dt>
            <dd>{to.length ? to.map(name).join(' · ') : 'Terminal evidence'}</dd>
            <dt>Assumptions</dt>
            <dd>{meta.assumptions?.length ? meta.assumptions.join('; ') : 'None declared'}</dd>
            <dt>Uncertainty</dt>
            <dd>{meta.uncertainty || '—'}</dd>
            <dt>Artifact</dt>
            <dd>
              {meta.artifact ? (
                <a className="link" href={meta.artifact} target="_blank" rel="noreferrer">
                  {meta.artifact.split('/').pop()}
                </a>
              ) : (
                '—'
              )}
            </dd>
            <dt>SHA-256</dt>
            <dd className="mono small wrap">{meta.sha256 || '—'}</dd>
          </dl>
          {node.id === 'truthloop' && (
            <div className="inspector-callout">
              {truth?.challenged ? (
                <>
                  <Status state={truthState} /> {truth.stability.reasons[0]}
                </>
              ) : (
                'The leading conclusion has not been challenged for this run.'
              )}
              <button className="link" onClick={() => navigate('TruthLoop')}>Open TruthLoop</button>
            </div>
          )}
          {node.mmsi && (
            <button className="btn btn-quiet btn-sm" onClick={() => onOpenVessel(node.mmsi)}>
              Open vessel dossier
            </button>
          )}
          {node.id === 'next_observation' && (
            <button className="btn btn-quiet btn-sm" onClick={() => navigate('Next Observation')}>
              Open next observation
            </button>
          )}
          <Section title="Node data">
            <dl className="kv compact">
              {Object.entries(node.details || {})
                .slice(0, 8)
                .map(([k, v]) => (
                  <div key={k} className="kv-row">
                    <dt>{k.replaceAll('_', ' ')}</dt>
                    <dd>
                      <Value value={v} />
                    </dd>
                  </div>
                ))}
            </dl>
            <details>
              <summary>Full record</summary>
              <pre>{JSON.stringify(node.details, null, 2)}</pre>
            </details>
          </Section>
          <p className="fine">Run {a.run_id.slice(0, 8)} · analysis hash <span className="mono">{a.analysis_hash.slice(0, 16)}</span></p>
        </aside>
      </div>
    </>
  );
}
