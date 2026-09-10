import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import { Crosshair, Globe2, Layers, Minus, Plus } from 'lucide-react';
import { Json } from './api';

type Props = {
  analysis: Json | null;
  geography: Json | null;
  selected: string | null;
  onSelect: (id: string) => void;
  focus: number[] | null;
  global?: boolean;
  forecastHour?: number;
};
export default function MaritimeMap({
  analysis: a,
  geography,
  selected,
  onSelect,
  focus,
  global = false,
  forecastHour = 24,
}: Props) {
  const element = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    layers = useRef<L.LayerGroup | null>(null),
    base = useRef<L.GeoJSON | null>(null);
  const [show, setShow] = useState({
    satellite: true,
    ais: true,
    origin: true,
    forecast: true,
    receptors: true,
    currents: true,
  });
  const [controls, setControls] = useState(false);
  const [aisMoment, setAisMoment] = useState('release');
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  useEffect(() => {
    if (!element.current) return;
    const m = L.map(element.current, {
      zoomControl: false,
      attributionControl: true,
      minZoom: 2,
      maxZoom: 14,
      preferCanvas: true,
    }).setView([12.3, 80.5], 8);
    map.current = m;
    layers.current = L.layerGroup().addTo(m);
    m.attributionControl.addAttribution('Natural Earth · approximate boundaries');
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
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
        const el = document.createElement('div');
        el.textContent = `${f.properties?.name} · ${f.properties?.status} · ${f.properties?.source}`;
        l.bindTooltip(el);
      },
    }).addTo(m);
    base.current.bringToBack();
  }, [geography]);
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (global) m.setView([15, 45], 2);
    else if (a)
      m.fitBounds(
        [
          [a.spill.bounds[1], a.spill.bounds[0]],
          [a.spill.bounds[3], a.spill.bounds[2]],
        ],
        { padding: [25, 25] },
      );
  }, [a?.run_id, global]);
  useEffect(() => {
    if (focus) map.current?.flyTo([focus[1], focus[0]], 8, { duration: 1 });
  }, [focus]);
  useEffect(() => {
    const group = layers.current;
    const m = map.current;
    if (!group || !m) return;
    group.clearLayers();
    if (!a) return;
    const addGeo = (geometry: Json, color: string, fillOpacity = 0.1, dashArray?: string) =>
      L.geoJSON(geometry as any, {
        style: { color, weight: 1.6, fillColor: color, fillOpacity, dashArray },
      }).addTo(group);
    if (show.satellite && !global) {
      const b = a.spill.bounds;
      L.imageOverlay(
        a.assets + '/satellite.png',
        [
          [b[1], b[0]],
          [b[3], b[2]],
        ],
        { opacity: 0.16, interactive: false, className: 'sar-layer' },
      ).addTo(group);
    }
    if (show.forecast) {
      const step = a.forecast.steps.find((s: Json) => s.hours === forecastHour);
      if (step) addGeo(step.geometry, '#eeaa4d', 0.16, '5 6');
    }
    if (show.origin) {
      addGeo(a.origin.geometry, '#4fe3ca', 0.16, '4 6');
      for (const p of a.origin.particles)
        L.circleMarker([p[1], p[0]], {
          radius: 2,
          color: '#55eac6',
          weight: 0,
          fillOpacity: 0.22,
        }).addTo(group);
      L.marker([a.origin.centroid[1], a.origin.centroid[0]], {
        icon: L.divIcon({
          className: 'origin-star',
          html: '✦',
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        }),
      })
        .bindTooltip('Modeled origin · conditional 90% region')
        .addTo(group);
    }
    addGeo(a.spill.geometry, '#ff5b65', 0.3);
    L.marker([a.spill.centroid[1], a.spill.centroid[0]], {
      icon: L.divIcon({
        className: 'spill-label',
        html: '<span>● OIL CANDIDATE</span>',
        iconSize: [145, 24],
        iconAnchor: [65, 45],
      }),
    }).addTo(group);
    if (show.receptors) for (const f of a.receptors.features) addGeo(f, '#baa2ff', 0.08, '3 5');
    if (show.currents && !global) {
      const c = a.spill.centroid;
      for (let i = -2; i <= 2; i++)
        for (let j = -2; j <= 2; j++) {
          L.marker([c[1] + i * 0.14, c[0] + j * 0.18], {
            icon: L.divIcon({
              className: 'current-arrow',
              html: `<span style="display:block;transform:rotate(${(Math.atan2(a.environment.records[0].current_east_ms, -a.environment.records[0].current_north_ms) * 180) / Math.PI - 90}deg)">➤</span>`,
              iconSize: [18, 18],
            }),
          })
            .bindTooltip('Input current direction · uniform forcing adapter')
            .addTo(group);
        }
    }
    if (show.ais)
      for (const v of a.vessels) {
        const active = v.mmsi === selected,
          top = v.mmsi === a.vessels[0].mmsi,
          color = top ? '#ff6572' : active ? '#f8d788' : '#38bdf8';
        L.geoJSON(v.geometry, {
          style: { color, weight: active ? 2 : 1, opacity: active ? 0.9 : 0.35, dashArray: '5 7' },
        }).addTo(group);
        const targetTime =
          aisMoment === 'release'
            ? (Date.parse(a.origin.release_window[0]) + Date.parse(a.origin.release_window[1])) / 2
            : Date.parse(a.observation_time);
        const nearest = v.track.reduce((best: Json, p: Json) =>
          Math.abs(Date.parse(p.time) - targetTime) < Math.abs(Date.parse(best.time) - targetTime)
            ? p
            : best,
        );
        const p = nearest.coordinates;
        const marker = L.marker([p[1], p[0]], {
          icon: L.divIcon({
            className: 'vessel-marker',
            html: `<span style="color:${color};font-size:${active ? 23 : 17}px">➤</span>`,
            iconSize: [20, 20],
          }),
        }).addTo(group);
        const label = document.createElement('span');
        label.textContent =
          v.name + ' · observed ' + new Date(nearest.time).toISOString().slice(11, 16) + ' UTC';
        marker.bindTooltip(label);
        marker.on('click', () => selectRef.current(v.mmsi));
        if (active) for (const gap of v.gaps) addGeo(gap.corridor, '#ecac58', 0.07, '2 4');
      }
  }, [a, show, selected, global, forecastHour, aisMoment]);
  return (
    <div className="map-wrap">
      <div
        ref={element}
        className="map-canvas"
        aria-label="Interactive maritime investigation map"
      />
      <div className="map-top">
        <span className="map-chip">
          <span className="live-dot" />
          {global ? 'GLOBAL MARITIME MAP' : 'INVESTIGATION AREA'}
          <small>WGS 84</small>
        </span>
      </div>
      <div className="ais-moment">
        <button
          className={aisMoment === 'release' ? 'chosen' : ''}
          onClick={() => setAisMoment('release')}
        >
          AIS · release window
        </button>
        <button
          className={aisMoment === 'observation' ? 'chosen' : ''}
          onClick={() => setAisMoment('observation')}
        >
          At SAR observation
        </button>
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
              {key}
            </label>
          ))}
        </div>
      )}
      <div className="map-legend">
        <span>
          <i style={{ background: '#ff5b65' }} />
          Detected candidate
        </span>
        <span>
          <i style={{ background: '#4fe3ca' }} />
          Origin uncertainty
        </span>
        <span>
          <i style={{ background: '#38bdf8' }} />
          AIS vessel / track
        </span>
        <span>
          <i style={{ background: '#eeaa4d' }} />
          Gap / forecast
        </span>
      </div>
      <div className="map-coordinates">
        {a
          ? `${a.spill.centroid[1].toFixed(3)}° N  ${a.spill.centroid[0].toFixed(3)}° E`
          : 'Ready for investigation'}
      </div>
    </div>
  );
}
