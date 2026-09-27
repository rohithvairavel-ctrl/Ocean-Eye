import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import { Crosshair, Layers, Minus, Pause, Play, Plus, X } from 'lucide-react';
import { Json, coordinate, time, shortTime } from './api';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';

// Bundle Leaflet's default marker assets instead of letting it guess a URL path.
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({ iconUrl: markerIcon, iconRetinaUrl: markerIcon2x, shadowUrl: markerShadow });

/** Faint lat/lon reference lines, spaced for the current zoom. */
function drawGraticule(m: L.Map, layer: L.LayerGroup) {
  layer.clearLayers();
  const z = m.getZoom();
  const step = z >= 11 ? 0.05 : z >= 9 ? 0.1 : z >= 8 ? 0.25 : z >= 6 ? 1 : z >= 4 ? 5 : 15;
  const b = m.getBounds().pad(0.2);
  const style = { color: '#F2F4F3', weight: 0.5, opacity: 0.07, interactive: false };
  for (let lon = Math.floor(b.getWest() / step) * step; lon <= b.getEast(); lon += step)
    L.polyline([[Math.max(b.getSouth(), -85), lon], [Math.min(b.getNorth(), 85), lon]], style).addTo(layer);
  for (let lat = Math.floor(b.getSouth() / step) * step; lat <= b.getNorth(); lat += step)
    L.polyline([[lat, b.getWest()], [lat, b.getEast()]], style).addTo(layer);
}

/* Semantic palette — must match styles.css tokens */
export const C = {
  observed: '#F06A6A',
  modeled: '#9C8FE8',
  predicted: '#6EA8C9',
  assumed: '#E6AD55',
  selection: '#48C9B0',
  lead: '#D9DEDC',
  vessel: '#818B89',
  success: '#63C58F',
};

type LayerKey =
  | 'slick'
  | 'sar'
  | 'mask'
  | 'ais'
  | 'returns'
  | 'origin'
  | 'hindcast'
  | 'forecast'
  | 'particles'
  | 'gaps'
  | 'currents'
  | 'response'
  | 'lead'
  | 'selected'
  | 'receptors'
  | 'aoi';

const GROUPS: [string, string, [LayerKey, string][]][] = [
  ['OBSERVED', C.observed, [
    ['slick', 'Oil candidate (dark region)'],
    ['sar', 'SAR backscatter image'],
    ['mask', 'Segmentation mask'],
    ['ais', 'All AIS positions at map time'],
    ['returns', 'SAR bright returns (screening)'],
  ]],
  ['MODELED', C.modeled, [
    ['origin', 'Origin uncertainty region'],
    ['hindcast', 'Hindcast particles'],
  ]],
  ['PREDICTED', C.predicted, [
    ['forecast', 'Forecast envelope'],
    ['particles', 'Forecast particles'],
  ]],
  ['ASSUMED', C.assumed, [
    ['gaps', 'AIS-gap travel envelopes'],
    ['currents', 'Forcing input (current vector)'],
    ['response', 'Response plan & route'],
  ]],
  ['INVESTIGATIVE', C.selection, [
    ['lead', 'Leading investigative vessel'],
    ['selected', 'Selected vessel track'],
    ['receptors', 'Exposure receptors'],
    ['aoi', 'Next-observation AOIs'],
  ]],
];

const DEFAULT_LAYERS: Record<LayerKey, boolean> = {
  slick: true,
  sar: false,
  mask: false,
  ais: false,
  returns: false,
  origin: true,
  hindcast: false,
  forecast: true,
  particles: false,
  gaps: false,
  currents: false,
  response: true,
  lead: true,
  selected: true,
  receptors: false,
  aoi: true,
};

type Inspect = { type: string; data: Json } | null;

type Props = {
  analysis: Json | null;
  geography: Json | null;
  selected?: string | null;
  onSelect?: (id: string) => void;
  focus?: number[] | null;
  global?: boolean;
  forecastHour?: number;
  incidents?: Json[];
  onCase?: (id: string) => void;
  response?: Json | null;
  onPlanningPoint?: (point: number[]) => void;
  candidates?: Json[];
  truth?: Json | null;
  layers?: Partial<Record<LayerKey, boolean>>;
  height?: number | string;
  timeline?: boolean;
  onOpenVessel?: (mmsi: string) => void;
  label?: string;
};

export default function MaritimeMap({
  analysis: a,
  geography,
  selected = null,
  onSelect,
  focus = null,
  global = false,
  forecastHour = 24,
  incidents = [],
  onCase,
  response,
  onPlanningPoint,
  candidates,
  truth,
  layers,
  height = 520,
  timeline = true,
  onOpenVessel,
  label,
}: Props) {
  const element = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    group = useRef<L.LayerGroup | null>(null),
    base = useRef<L.GeoJSON | null>(null);
  const [show, setShow] = useState<Record<LayerKey, boolean>>({ ...DEFAULT_LAYERS, ...layers });
  const [menu, setMenu] = useState(false),
    [playing, setPlaying] = useState(false),
    [frame, setFrame] = useState(0),
    [inspect, setInspect] = useState<Inspect>(null);
  const handlers = useRef({ onSelect, onCase, onPlanningPoint });
  handlers.current = { onSelect, onCase, onPlanningPoint };

  const frames: Json[] = useMemo(
    () =>
      a
        ? [
            ...a.origin.snapshots
              .map((s: Json) => ({ ...s, hours: -s.hours_before, kind: 'MODELED' }))
              .sort((x: Json, y: Json) => x.hours - y.hours),
            { hours: 0, kind: 'OBSERVED', geometry: a.spill.geometry },
            ...a.forecast.steps.map((s: Json) => ({ ...s, kind: 'PREDICTED' })),
          ]
        : [],
    [a?.run_id],
  );
  const defaultFrame = frames.findIndex((f) => f.kind === 'PREDICTED' && f.hours === forecastHour);
  const current = frames[frame];
  const lead = a?.vessels?.[0];

  useEffect(() => {
    setFrame(defaultFrame >= 0 ? defaultFrame : frames.findIndex((f) => f.hours === 0));
    setPlaying(false);
    setInspect(null);
  }, [a?.run_id, forecastHour]);

  useEffect(() => {
    if (!playing || !frames.length) return;
    const timer = setInterval(() => setFrame((f) => (f + 1) % frames.length), 1400);
    return () => clearInterval(timer);
  }, [playing, frames.length]);

  useEffect(() => {
    if (!element.current) return;
    const m = L.map(element.current, {
      zoomControl: false,
      minZoom: 2,
      maxZoom: 14,
      worldCopyJump: true,
      attributionControl: true,
    }).setView([15, 45], 2);
    map.current = m;
    const grid = L.layerGroup().addTo(m);
    const redraw = () => drawGraticule(m, grid);
    m.on('moveend zoomend', redraw);
    redraw();
    group.current = L.layerGroup().addTo(m);
    m.attributionControl.setPrefix(false);
    m.attributionControl.addAttribution('Natural Earth · approximate boundaries');
    L.control.scale({ imperial: false, position: 'bottomright' }).addTo(m);
    m.on('click', (e: L.LeafletMouseEvent) =>
      handlers.current.onPlanningPoint?.([e.latlng.lng, e.latlng.lat]),
    );
    const observer = new ResizeObserver(() => m.invalidateSize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !geography) return;
    base.current?.remove();
    base.current = L.geoJSON(geography as any, {
      filter: (f) => f.properties?.kind === 'country',
      style: { color: '#2B3235', weight: 0.8, fillColor: '#1A1F21', fillOpacity: 1 },
      interactive: false,
    }).addTo(m);
    base.current.bringToBack();
  }, [geography]);

  const fit = () => {
    const m = map.current;
    if (!m) return;
    if (global || !a) {
      m.setView([15, 45], 2);
      return;
    }
    const bounds = L.geoJSON({
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: a.spill.geometry, properties: {} },
        { type: 'Feature', geometry: a.origin.geometry, properties: {} },
        ...a.forecast.steps
          .filter((s: Json) => s.hours <= Math.max(forecastHour, 24))
          .map((s: Json) => ({ type: 'Feature', geometry: s.geometry, properties: {} })),
      ],
    } as any).getBounds();
    m.fitBounds(bounds, { padding: [40, 40], maxZoom: 11 });
  };

  useEffect(() => {
    fit();
  }, [a?.run_id, global]);

  useEffect(() => {
    if (focus) map.current?.flyTo([focus[1], focus[0]], 9, { duration: 0.45 });
  }, [focus]);

  useEffect(() => {
    const g = group.current;
    if (!g) return;
    g.clearLayers();
    const tip = (text: string) => {
      const el = document.createElement('span');
      el.textContent = text;
      return el;
    };
    const shape = (value: Json, style: L.PathOptions, onClick?: () => void, hover?: string) => {
      const layer = L.geoJSON(value as any, { style: () => style }).addTo(g);
      if (hover) layer.bindTooltip(tip(hover), { sticky: true, className: 'map-tip' });
      if (onClick)
        layer.on('click', (e: L.LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e);
          onClick();
        });
      return layer;
    };
    const dot = (p: number[], color: string, radius = 3, fill = 0.8) =>
      L.circleMarker([p[1], p[0]], {
        color,
        radius,
        weight: 1,
        fillColor: color,
        fillOpacity: fill,
      }).addTo(g);

    for (const c of incidents) {
      if (!c.coordinates) continue;
      dot(c.coordinates, c.source_type === 'SYNTHETIC' ? C.assumed : C.selection, 7)
        .bindTooltip(tip(`${c.name} · ${c.source_type === 'SYNTHETIC' ? 'demo' : 'real'} · ${shortTime(c.observation_time)}`), { className: 'map-tip' })
        .on('click', () => handlers.current.onCase?.(c.id));
    }
    if (!a) return;
    const b = a.spill.bounds;
    const imageBounds: L.LatLngBoundsExpression = [
      [b[1], b[0]],
      [b[3], b[2]],
    ];
    if (show.sar)
      L.imageOverlay(`${a.assets}/satellite.png`, imageBounds, { opacity: 0.55, interactive: false }).addTo(g);
    if (show.mask)
      L.imageOverlay(`${a.assets}/mask.png`, imageBounds, { opacity: 0.5, interactive: false }).addTo(g);

    if (show.receptors)
      for (const f of a.receptors.features) {
        const r = a.impact.receptors.find((x: Json) => x.name === f.properties.name);
        shape(
          f,
          { color: C.success, weight: 1.2, fillOpacity: 0.06, dashArray: '2 4' },
          () => setInspect({ type: 'receptor', data: { ...f.properties, ...r } }),
          `${f.properties.name} · ${f.properties.kind.replace('_', ' ')}`,
        );
      }

    if (show.origin)
      shape(
        a.origin.geometry,
        { color: C.modeled, weight: 1.5, fillColor: C.modeled, fillOpacity: 0.08, dashArray: '6 6' },
        () => setInspect({ type: 'origin', data: a.origin }),
        'Modeled origin region · uncertainty, not a release point',
      );

    const hind = current && current.hours < 0 ? current : null;
    if (show.hindcast) {
      if (hind) shape(hind.geometry, { color: C.modeled, weight: 1, fillOpacity: 0.05, dashArray: '2 5' });
      for (const p of (hind ? hind.particles : a.origin.particles) || []) dot(p, C.modeled, 1.6, 0.6);
    }

    const predicted =
      current && current.hours > 0
        ? current
        : a.forecast.steps.find((s: Json) => s.hours === forecastHour) || a.forecast.steps[0];
    if (predicted && show.forecast && !(current && current.hours < 0))
      shape(
        predicted.geometry,
        { color: C.predicted, weight: 1.5, fillColor: C.predicted, fillOpacity: 0.07, dashArray: '2 6' },
        () => setInspect({ type: 'forecast', data: predicted }),
        `Forecast envelope · +${predicted.hours} h`,
      );
    if (predicted && show.particles && !(current && current.hours < 0))
      for (const p of predicted.particles || []) dot(p, C.predicted, 1.6, 0.6);

    if (show.slick)
      shape(
        a.spill.geometry,
        { color: C.observed, weight: 1.6, fillColor: C.observed, fillOpacity: 0.32 },
        () => setInspect({ type: 'slick', data: a.spill }),
        'Observed oil candidate · classification pending',
      );

    if (show.returns)
      for (const r of a.dark.sar_returns)
        dot(r.coordinates, r.matched ? C.success : C.assumed, 4.5)
          .bindTooltip(tip(r.matched ? 'SAR return · AIS match within tolerance' : 'SAR return · unmatched (screening only)'), { className: 'map-tip' });

    const sampleTime = Date.parse(a.observation_time) + (current?.hours || 0) * 3600000;
    if (show.currents) {
      const rows = a.environment.records;
      const row = rows.reduce((best: Json, r: Json) =>
        Math.abs(Date.parse(r.time) - sampleTime) < Math.abs(Date.parse(best.time) - sampleTime) ? r : best,
      );
      const p = a.spill.centroid;
      const angle = (Math.atan2(row.current_east_ms, row.current_north_ms) * 180) / Math.PI;
      L.marker([p[1], p[0]], {
        icon: L.divIcon({
          className: 'current-arrow',
          html: `<span style="transform:rotate(${angle}deg)">↑</span>`,
        }),
        interactive: true,
      })
        .addTo(g)
        .bindTooltip(tip(`Forcing input · ${time(row.time)} · E ${row.current_east_ms} / N ${row.current_north_ms} m/s`), { className: 'map-tip' });
    }

    const nearestPoint = (v: Json) => {
      if (!v.track.length) return null;
      const p = v.track.reduce((best: Json, q: Json) =>
        Math.abs(Date.parse(q.time) - sampleTime) < Math.abs(Date.parse(best.time) - sampleTime) ? q : best,
      );
      return Math.abs(Date.parse(p.time) - sampleTime) <= 30 * 60000 ? p : null;
    };
    const drawVessel = (v: Json, color: string, emphasis: boolean) => {
      shape(
        v.geometry,
        { color, weight: emphasis ? 2.4 : 1.6, opacity: emphasis ? 0.95 : 0.7, dashArray: emphasis ? undefined : '4 5', fill: false },
        () => {
          handlers.current.onSelect?.(v.mmsi);
          setInspect({ type: 'vessel', data: v });
        },
        `${v.name} · observed AIS positions (segments do not prove the route)`,
      );
      if (show.gaps)
        for (const gap of v.gaps)
          shape(gap.corridor, { color: C.assumed, weight: 1, fillOpacity: 0.06, dashArray: '2 4' }, undefined, 'Assumed travel envelope during AIS gap');
      const p = nearestPoint(v) || v.track[v.track.length - 1];
      if (p)
        dot(p.coordinates, color, emphasis ? 6 : 4.5, 0.95)
          .on('click', (e: L.LeafletMouseEvent) => {
            L.DomEvent.stopPropagation(e);
            handlers.current.onSelect?.(v.mmsi);
            setInspect({ type: 'vessel', data: v });
          })
          .bindTooltip(tip(`${v.name} · ${p === nearestPoint(v) ? 'observed ' + shortTime(p.time) : 'last observed position'}`), { className: 'map-tip' });
    };
    const sel = a.vessels.find((v: Json) => v.mmsi === selected);
    if (sel && lead && sel.mmsi !== lead.mmsi) {
      if (show.lead) drawVessel(lead, C.lead, false);
      if (show.selected) drawVessel(sel, C.selection, true);
    } else if (lead && (show.lead || show.selected)) {
      drawVessel(lead, C.selection, true);
    }

    if (show.ais)
      for (const v of a.vessels) {
        if (v.mmsi === sel?.mmsi || v.mmsi === lead?.mmsi) continue;
        const p = nearestPoint(v);
        if (!p) continue;
        dot(p.coordinates, C.vessel, 3.2, 0.7)
          .on('click', (e: L.LeafletMouseEvent) => {
            L.DomEvent.stopPropagation(e);
            handlers.current.onSelect?.(v.mmsi);
            setInspect({ type: 'vessel', data: v });
          })
          .bindTooltip(tip(`${v.name} · observed ${shortTime(p.time)}`), { className: 'map-tip' });
      }

    if (show.aoi && candidates)
      for (const c of candidates)
        L.circle([c.center[1], c.center[0]], {
          radius: c.suggested_radius_km * 1000,
          color: C.selection,
          weight: 1.2,
          dashArray: '1 5',
          fillOpacity: 0.03,
        })
          .addTo(g)
          .bindTooltip(tip(`${c.target_type.toLowerCase()} · priority ${c.score}`), { className: 'map-tip' });

    if (response && show.response) {
      if (response.candidate_interception_zone)
        shape(response.candidate_interception_zone, { color: C.assumed, weight: 1, fillOpacity: 0.04, dashArray: '6 6' }, undefined, 'Potential interception zone (planning)');
      if (response.route) shape(response.route, { color: C.assumed, weight: 1.6, dashArray: '4 4' }, undefined, 'Assumed straight-line planning route');
      if (response.asset) dot(response.asset.coordinates, C.assumed, 6).bindTooltip(tip(`Assumed asset · ${response.asset.name}`), { className: 'map-tip' });
      if (response.target) dot(response.target, C.assumed, 4).bindTooltip(tip('Planning target · interception not guaranteed'), { className: 'map-tip' });
    }
  }, [a, show, selected, forecastHour, frame, incidents, response, candidates]);

  const toggle = (key: LayerKey) => setShow((s) => ({ ...s, [key]: !s[key] }));
  const truthFor = (mmsi: string) => {
    if (!truth?.challenged) return null;
    const ran = truth.challenges.filter((c: Json) => c.available && c.id !== 'exclude_strongest_candidate');
    const top = ran.filter((c: Json) => c.after_ranking?.[0]?.mmsi === mmsi);
    const displaced = ran.filter(
      (c: Json) => c.baseline_ranking?.[0]?.mmsi === mmsi && c.after_ranking?.[0]?.mmsi !== mmsi,
    );
    return { total: ran.length, top: top.length, displaced };
  };

  return (
    <div className="map-wrap" style={{ height }}>
      <div ref={element} className="map-canvas" role="region" aria-label="Investigation map" />
      <div className="map-chip">
        <strong>{label || (global ? 'Saved investigations' : 'Investigation area')}</strong>
        {a && !global && current && (
          <span>
            {current.kind === 'OBSERVED'
              ? 'Observation'
              : current.kind === 'MODELED'
                ? `Hindcast T${current.hours} h`
                : `Forecast T+${current.hours} h`}{' '}
            · {shortTime(new Date(Date.parse(a.observation_time) + current.hours * 3600000).toISOString())}
          </span>
        )}
      </div>
      <div className="map-tools" role="toolbar" aria-label="Map tools">
        <button aria-label="Zoom in" title="Zoom in" onClick={() => map.current?.zoomIn()}>
          <Plus size={16} />
        </button>
        <button aria-label="Zoom out" title="Zoom out" onClick={() => map.current?.zoomOut()}>
          <Minus size={16} />
        </button>
        <button aria-label="Fit to investigation" title="Fit to investigation" onClick={fit}>
          <Crosshair size={16} />
        </button>
        {!global && (
          <button
            aria-label="Layers"
            aria-expanded={menu}
            title="Layers"
            className={menu ? 'on' : ''}
            onClick={() => setMenu(!menu)}
          >
            <Layers size={16} />
          </button>
        )}
      </div>
      {menu && !global && (
        <div className="layer-menu" role="dialog" aria-label="Map layers">
          <div className="layer-menu-head">
            <strong>Layers</strong>
            <button className="link" onClick={() => setShow({ ...DEFAULT_LAYERS, ...layers })}>
              Reset
            </button>
          </div>
          {GROUPS.map(([name, color, items]) => (
            <fieldset key={name}>
              <legend>
                <i style={{ background: color }} />
                {name}
              </legend>
              {items.map(([key, text]) => (
                <label key={key}>
                  <input type="checkbox" checked={show[key]} onChange={() => toggle(key)} />
                  {text}
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      )}
      {inspect && a && (
        <aside className="inspector" aria-label="Feature inspector">
          <button className="inspector-close" aria-label="Close inspector" onClick={() => setInspect(null)}>
            <X size={14} />
          </button>
          {inspect.type === 'slick' && (
            <>
              <span className="inspector-kind" style={{ color: C.observed }}>Observed</span>
              <h3>Oil candidate</h3>
              <dl>
                <dt>Area</dt><dd>{inspect.data.area_km2.toFixed(2)} km²</dd>
                <dt>Perimeter</dt><dd>{inspect.data.perimeter_km.toFixed(1)} km</dd>
                <dt>Contrast</dt><dd>{inspect.data.contrast_db} dB</dd>
                <dt>Acquired</dt><dd>{time(a.observation_time)}</dd>
                <dt>Source</dt><dd>{inspect.data.sensor}</dd>
                <dt>Classification</dt><dd>Pending — pollutant unknown</dd>
              </dl>
            </>
          )}
          {inspect.type === 'origin' && (
            <>
              <span className="inspector-kind" style={{ color: C.modeled }}>Modeled</span>
              <h3>Origin uncertainty region</h3>
              <dl>
                <dt>Centre</dt><dd>{coordinate(inspect.data.centroid)}</dd>
                <dt>90% radius</dt><dd>{inspect.data.radius90_km} km</dd>
                <dt>Release window</dt>
                <dd>{shortTime(inspect.data.release_window[0])} – {shortTime(inspect.data.release_window[1])} <em>(assumed)</em></dd>
                <dt>Method</dt><dd>{inspect.data.method}</dd>
              </dl>
              <p className="inspector-note">{inspect.data.uncertainty}</p>
            </>
          )}
          {inspect.type === 'forecast' && (
            <>
              <span className="inspector-kind" style={{ color: C.predicted }}>Predicted</span>
              <h3>Forecast envelope +{inspect.data.hours} h</h3>
              <dl>
                <dt>Valid time</dt><dd>{time(inspect.data.time)}</dd>
                <dt>90% spread</dt><dd>{inspect.data.spread90_km} km</dd>
                <dt>Receptor overlap</dt>
                <dd>
                  {a.impact.receptors.filter((r: Json) => r.first_overlap_h !== null && r.first_overlap_h <= inspect.data.hours).map((r: Json) => r.name).join(', ') ||
                    'None sampled by this horizon'}
                </dd>
              </dl>
              <p className="inspector-note">{a.forecast.uncertainty}</p>
            </>
          )}
          {inspect.type === 'receptor' && (
            <>
              <span className="inspector-kind" style={{ color: C.success }}>Reference layer</span>
              <h3>{inspect.data.name}</h3>
              <dl>
                <dt>Category</dt><dd>{String(inspect.data.kind).replace('_', ' ')}</dd>
                <dt>First potential exposure</dt>
                <dd>{inspect.data.first_overlap_time ? `${time(inspect.data.first_overlap_time)} (+${inspect.data.first_overlap_h} h)` : 'No sampled overlap'}</dd>
                <dt>Source</dt><dd>{inspect.data.source} · {inspect.data.source_type}</dd>
              </dl>
              <p className="inspector-note">{inspect.data.uncertainty || 'Overlap is potential exposure, not confirmed damage.'}</p>
            </>
          )}
          {inspect.type === 'vessel' && (() => {
            const v = inspect.data;
            const t = truthFor(v.mmsi);
            return (
              <>
                <span className="inspector-kind" style={{ color: C.selection }}>Investigative lead</span>
                <h3>{v.name}</h3>
                <dl>
                  <dt>MMSI</dt><dd className="mono">{v.mmsi}</dd>
                  <dt>Closest approach</dt><dd>{v.nearest_km} km to modeled origin</dd>
                  <dt>AIS gaps</dt><dd>{v.gaps.length ? `${v.gaps.length} (${Math.round(v.gaps.reduce((s: number, g: Json) => s + g.minutes, 0))} min)` : 'None'}</dd>
                  <dt>Relevance score</dt><dd>{v.score} / 100 <em>not a probability</em></dd>
                  <dt>TruthLoop</dt>
                  <dd>
                    {!t ? 'Not challenged yet' : t.displaced.length
                      ? `Displaced by ${t.displaced.map((c: Json) => c.label.toLowerCase()).join(', ')}`
                      : `Ranks #1 in ${t.top} of ${t.total} challenges`}
                  </dd>
                </dl>
                <div className="inspector-factors">
                  {v.components.slice().sort((x: Json, y: Json) => y.contribution - x.contribution).slice(0, 3).map((c: Json) => (
                    <span key={c.name}>{c.name} <b>{c.contribution.toFixed(1)}</b></span>
                  ))}
                </div>
                {onOpenVessel && (
                  <button className="btn btn-quiet btn-sm" onClick={() => onOpenVessel(v.mmsi)}>
                    Open vessel dossier
                  </button>
                )}
              </>
            );
          })()}
        </aside>
      )}
      {!global && (
      <div className="map-legend" aria-label="Map legend">
        <span><i className="sw observed" />Observed</span>
        <span><i className="sw modeled" />Modeled</span>
        <span><i className="sw predicted" />Predicted</span>
        <span><i className="sw assumed" />Assumed</span>
        <span><i className="sw selection" />Selection</span>
      </div>
      )}
      {a && !global && timeline && frames.length > 0 && (
        <div className="map-time">
          <button
            className="icon-btn"
            aria-label={playing ? 'Pause time animation' : 'Play time animation'}
            onClick={() => {
              if (!playing) setShow((s) => ({ ...s, hindcast: true }));
              setPlaying(!playing);
            }}
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <input
            aria-label="Map time: hindcast, observation, forecast"
            type="range"
            min="0"
            max={frames.length - 1}
            value={Math.max(frame, 0)}
            onChange={(e) => {
              setFrame(Number(e.target.value));
              setPlaying(false);
              if (frames[Number(e.target.value)]?.hours < 0) setShow((s) => ({ ...s, hindcast: true }));
            }}
          />
          <div className="map-time-scale">
            <span>Hindcast</span>
            <span>Observed</span>
            <span>Forecast</span>
          </div>
        </div>
      )}
    </div>
  );
}
