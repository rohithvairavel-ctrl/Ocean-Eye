import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import { Crosshair, Globe2, Layers, Minus, Plus } from 'lucide-react';
import { Json, time } from './api';
type Props = {
  analysis: Json | null;
  geography: Json | null;
  selected: string | null;
  onSelect: (id: string) => void;
  focus: number[] | null;
  global?: boolean;
  forecastHour?: number;
  incidents?: Json[];
  onCase?: (id: string) => void;
  response?: Json | null;
  onPlanningPoint?: (point: number[]) => void;
};
const layerNames: Record<string, string> = {
  slick: 'Observed dark-region candidate',
  satellite: 'SAR image',
  segmentation: 'Segmentation mask',
  hindcast: 'Hindcast particles / envelope',
  origin: 'Modeled origin REGION',
  forecast: 'Forecast envelope',
  particles: 'Forecast particles',
  ais: 'Observed AIS markers',
  track: 'Selected vessel track',
  gaps: 'AIS gap assumptions',
  sar: 'SAR–AIS return screening',
  currents: 'Input environmental forcing',
  receptors: 'Loaded ecological receptors',
  assets: 'Assumed response asset',
  intervention: 'Intervention planning route / zone',
};
export default function MaritimeMap({
  analysis: a,
  geography,
  selected,
  onSelect,
  focus,
  global = false,
  forecastHour = 24,
  incidents = [],
  onCase,
  response,
  onPlanningPoint,
}: Props) {
  const element = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    group = useRef<L.LayerGroup | null>(null),
    base = useRef<L.GeoJSON | null>(null);
  const [show, setShow] = useState<Record<string, boolean>>({
    slick: true,
    satellite: false,
    segmentation: false,
    hindcast: false,
    origin: true,
    forecast: false,
    particles: false,
    ais: true,
    track: true,
    gaps: false,
    sar: false,
    currents: false,
    receptors: false,
    assets: true,
    intervention: true,
  });
  const [controls, setControls] = useState(false),
    [playing, setPlaying] = useState(false),
    [frame, setFrame] = useState(0);
  const selectRef = useRef(onSelect),
    caseRef = useRef(onCase),
    pointRef = useRef(onPlanningPoint);
  selectRef.current = onSelect;
  caseRef.current = onCase;
  pointRef.current = onPlanningPoint;
  const frames: Json[] = a
    ? [
        ...a.origin.snapshots
          .map((s: Json) => ({ ...s, hours: -s.hours_before, kind: 'MODELED' }))
          .sort((x: Json, y: Json) => x.hours - y.hours),
        { hours: 0, kind: 'OBSERVED', geometry: a.spill.geometry },
        ...a.forecast.steps.map((s: Json) => ({ ...s, kind: 'PREDICTED' })),
      ]
    : [];
  const current = frames[frame] || frames.find((s) => s.hours === 0);
  useEffect(() => {
    setFrame(a?.origin.snapshots.length || 0);
    setPlaying(false);
  }, [a?.run_id]);
  useEffect(() => {
    if (!playing || !frames.length) return;
    const timer = setInterval(() => setFrame((f) => (f + 1) % frames.length), 1200);
    return () => clearInterval(timer);
  }, [playing, frames.length]);
  useEffect(() => {
    if (!element.current) return;
    const m = L.map(element.current, {
      zoomControl: false,
      minZoom: 2,
      maxZoom: 14,
      preferCanvas: false,
    }).setView([15, 45], 2);
    map.current = m;
    group.current = L.layerGroup().addTo(m);
    m.attributionControl.addAttribution('Natural Earth · approximate boundaries');
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
    m.on('click', (e: L.LeafletMouseEvent) => pointRef.current?.([e.latlng.lng, e.latlng.lat]));
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
      style: { color: '#2e6269', weight: 1, fillColor: '#173c40', fillOpacity: 0.85 },
      onEachFeature: (f, l) => {
        const text = document.createElement('span');
        text.textContent = `${f.properties?.name} · approximate cartographic boundary`;
        l.bindTooltip(text);
      },
    }).addTo(m);
    base.current.bringToBack();
  }, [geography]);
  useEffect(() => {
    if (global) map.current?.setView([15, 45], 2);
    else if (a)
      map.current?.fitBounds(
        [
          [a.spill.bounds[1], a.spill.bounds[0]],
          [a.spill.bounds[3], a.spill.bounds[2]],
        ],
        { padding: [35, 35] },
      );
  }, [a?.run_id, global]);
  useEffect(() => {
    if (focus) map.current?.flyTo([focus[1], focus[0]], 8, { duration: 0.5 });
  }, [focus]);
  useEffect(() => {
    const g = group.current;
    if (!g) return;
    g.clearLayers();
    const geo = (value: Json, color: string, opacity = 0.12, dash?: string) =>
      L.geoJSON(value as any, {
        style: { color, weight: 1.7, fillOpacity: opacity, dashArray: dash },
      }).addTo(g);
    const label = (text: string) => {
      const el = document.createElement('span');
      el.textContent = text;
      return el;
    };
    const dot = (p: number[], color: string, radius = 3) =>
      L.circleMarker([p[1], p[0]], { color, radius, weight: 1, fillOpacity: 0.7 }).addTo(g);
    for (const c of incidents) {
      if (!c.coordinates) continue;
      dot(c.coordinates, c.source_type === 'SYNTHETIC' ? '#efb85c' : '#56c8f4', 7)
        .bindTooltip(
          label(`${c.name} · ${c.status} · ${time(c.observation_time)} · ${c.source_type}`),
        )
        .on('click', () => caseRef.current?.(c.id));
    }
    if (!a) return;
    if (show.satellite || show.segmentation) {
      const b = a.spill.bounds;
      for (const key of ['satellite', 'segmentation'])
        if (show[key])
          L.imageOverlay(
            `${a.assets}/${key === 'satellite' ? 'satellite.png' : 'mask.png'}`,
            [
              [b[1], b[0]],
              [b[3], b[2]],
            ],
            { opacity: key === 'satellite' ? 0.3 : 0.45, interactive: false },
          ).addTo(g);
    }
    if (show.slick)
      geo(a.spill.geometry, '#FF5B69', 0.25).bindTooltip(
        'OBSERVED dark-region candidate · pollutant UNKNOWN',
      );
    if (show.origin)
      geo(a.origin.geometry, '#9B8AFB', 0.09, '4 6').bindTooltip(
        'MODELED origin region · conditional uncertainty, not a known release point',
      );
    if (show.hindcast) {
      const s = current?.hours < 0 ? current : null;
      if (s) {
        geo(s.geometry, '#9B8AFB', 0.08, '2 4');
        for (const p of s.particles || []) dot(p, '#9B8AFB', 2);
      } else for (const p of a.origin.particles) dot(p, '#9B8AFB', 2);
    }
    const predicted =
      current?.hours < 0
        ? null
        : current?.hours > 0
          ? current
          : a.forecast.steps.find((s: Json) => s.hours === forecastHour);
    if (predicted && show.forecast)
      geo(predicted.geometry, '#5BA7FF', 0.12, '5 6').bindTooltip(
        `PREDICTED conditional envelope +${predicted.hours} h`,
      );
    if (predicted && show.particles)
      for (const p of predicted.particles || []) dot(p, '#5BA7FF', 2);
    if (show.receptors)
      for (const f of a.receptors.features)
        geo(f, '#baa2ff', 0.08, '3 5').bindTooltip(
          label(
            `${f.properties.name} · ${f.properties.source_type} · ${f.properties.source} / ${f.properties.version}`,
          ),
        );
    if (show.sar)
      for (const r of a.dark.sar_returns)
        dot(r.coordinates, r.matched ? '#5DD39E' : '#FFB547', 5).bindTooltip(
          label(
            `SCREENING SAR return · ${r.matched ? 'AIS match within tolerance' : 'unmatched; not a confirmed dark vessel'}`,
          ),
        );
    const sampleTime = Date.parse(a.observation_time) + (current?.hours || 0) * 3600000;
    if (show.currents) {
      const records = a.environment.records;
      const row = records.reduce((b: Json, r: Json) =>
        Math.abs(Date.parse(r.time) - sampleTime) < Math.abs(Date.parse(b.time) - sampleTime)
          ? r
          : b,
      );
      const p = a.spill.centroid;
      const angle = (Math.atan2(row.current_east_ms, -row.current_north_ms) * 180) / Math.PI - 90;
      L.marker([p[1], p[0]], {
        icon: L.divIcon({
          className: 'current-arrow',
          html: `<span style="display:block;transform:rotate(${angle}deg)">➤</span>`,
        }),
      })
        .addTo(g)
        .bindTooltip(
          label(
            `INPUT uniform forcing · nearest record ${time(row.time)} · E ${row.current_east_ms} / N ${row.current_north_ms} m/s`,
          ),
        );
    }
    for (const v of a.vessels) {
      const active = v.mmsi === selected,
        color = active ? '#35D3D1' : '#5BA7FF';
      if (active && show.track)
        L.geoJSON(v.geometry, { style: { color, weight: 2.5, opacity: 0.9, dashArray: '5 6' } })
          .addTo(g)
          .bindTooltip(
            'OBSERVED AIS positions · connecting segments do not establish the intervening route',
          );
      if (active && show.gaps)
        for (const gap of v.gaps)
          geo(gap.corridor, '#FFB547', 0.08, '2 4').bindTooltip(
            'ASSUMED possible travel envelope during missing AIS interval',
          );
      if (!show.ais || !v.track.length) continue;
      const nearest = v.track.reduce((b: Json, p: Json) =>
        Math.abs(Date.parse(p.time) - sampleTime) < Math.abs(Date.parse(b.time) - sampleTime)
          ? p
          : b,
      );
      if (Math.abs(Date.parse(nearest.time) - sampleTime) > 30 * 60000) continue;
      const marker = dot(nearest.coordinates, color, active ? 6 : 3);
      marker.setStyle({
        opacity: active || !selected ? 1 : 0.4,
        fillOpacity: active || !selected ? 0.8 : 0.25,
      });
      marker.bindTooltip(
        label(`${v.name} · OBSERVED ${time(nearest.time)} · nearest within 30 min`),
      );
      marker.on('click', () => selectRef.current(v.mmsi));
    }
    if (response) {
      if (show.assets && response.asset)
        dot(response.asset.coordinates, '#FFB547', 7).bindTooltip(
          label(`ASSUMED ${response.asset.name} · availability unverified`),
        );
      if (show.intervention) {
        geo(response.candidate_interception_zone, '#FFB547', 0.04, '6 6');
        if (response.route)
          geo(response.route, '#FFB547', 0, '4 4').bindTooltip(
            'ASSUMED geodesic planning route · navigation not modeled',
          );
        dot(response.target, '#FFB547', 5).bindTooltip(
          'Planning target · not guaranteed interception',
        );
      }
    }
  }, [a, show, selected, global, forecastHour, frame, incidents, response]);
  return (
    <div className="map-wrap">
      <div
        ref={element}
        className="map-canvas"
        aria-label="Interactive maritime investigation map"
      />
      <div className="map-top">
        <span className="map-chip">
          {global ? 'SAVED INCIDENTS' : 'INVESTIGATION AREA'}
          <small>WGS 84</small>
        </span>
      </div>
      <div className="map-tools">
        <button title="Zoom in" onClick={() => map.current?.zoomIn()}>
          <Plus size={17} />
        </button>
        <button title="Zoom out" onClick={() => map.current?.zoomOut()}>
          <Minus size={17} />
        </button>
        <button
          title="Fit investigation"
          onClick={() =>
            a &&
            map.current?.fitBounds([
              [a.spill.bounds[1], a.spill.bounds[0]],
              [a.spill.bounds[3], a.spill.bounds[2]],
            ])
          }
        >
          <Crosshair size={17} />
        </button>
        <button title="Global view" onClick={() => map.current?.setView([15, 45], 2)}>
          <Globe2 size={17} />
        </button>
        <button title="Map layers" onClick={() => setControls(!controls)}>
          <Layers size={17} />
        </button>
      </div>
      {controls && (
        <div className="layer-menu">
          {Object.entries(show).map(([key, value]) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={value}
                onChange={() => setShow({ ...show, [key]: !value })}
              />
              {layerNames[key]}
            </label>
          ))}
        </div>
      )}
      {a && current && (
        <div className="map-time">
          <button
            onClick={() => {
              setShow((s) => ({ ...s, hindcast: true, forecast: true }));
              setPlaying(!playing);
            }}
          >
            {playing ? 'Pause' : 'Play'}
          </button>
          <input
            aria-label="Hindcast observation forecast time"
            type="range"
            min="0"
            max={frames.length - 1}
            value={frame}
            onChange={(e) => {
              setFrame(Number(e.target.value));
              setPlaying(false);
              setShow((s) => ({ ...s, hindcast: true, forecast: true }));
            }}
          />
          <span>
            {current.kind} · {current.hours > 0 ? '+' : ''}
            {current.hours} h<br />
            {time(new Date(Date.parse(a.observation_time) + current.hours * 3600000).toISOString())}
          </span>
          <small>AIS: observed reports within 30 min only. No predicted vessel motion.</small>
        </div>
      )}
      <div className="map-legend">
        <span>
          <i style={{ background: '#FF5B69' }} />
          OBSERVED candidate
        </span>
        <span>
          <i style={{ background: '#9B8AFB' }} />
          MODELED region
        </span>
        <span>
          <i style={{ background: '#5BA7FF' }} />
          PREDICTED envelope
        </span>
        <span>
          <i style={{ background: '#FFB547' }} />
          ASSUMED response
        </span>
      </div>
    </div>
  );
}
