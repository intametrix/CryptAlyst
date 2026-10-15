import { useEffect, useState } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import type { ChartMarker } from '../chartData';
import type { ColorCanon } from '../colors';
import { EventGlyph } from './EventGlyph';

export interface MarkerLayerProps {
  chart: IChartApi | null;
  series: ISeriesApi<'Candlestick'> | null;
  markers: readonly ChartMarker[];
  canon: ColorCanon | null;
  selectedEvent: number | null;
  onSelect: (timeMs: number) => void;
  /** Bumped when the chart repaints, so positions are recomputed. */
  repaintKey: number;
}

interface Placed {
  marker: ChartMarker;
  x: number;
  y: number;
}

/**
 * The event badges, drawn as HTML over the chart.
 *
 * Why not the library's own markers: **`SeriesMarker` has no text-colour option.**
 * Its fields are `time`, `position`, `shape`, `color`, `id`, `text` and `size`
 * (verified in the installed `typings.d.ts` — `textColor` exists only at
 * `LayoutOptions` level, i.e. for the whole chart). The library therefore renders
 * marker text white, which would give a bright-green fill with white text — not
 * the black-on-green badge the toolbar uses, so the requirement could not be met
 * with the marker API.
 *
 * This layer instead anchors the *same* `EventGlyph` component to each bar's time
 * via the time scale's own coordinate conversion, so the chart badge and the
 * toolbar badge are literally the same component and cannot drift. It is also
 * clickable, which the library's markers are not.
 */
export function MarkerLayer({
  chart,
  series,
  markers,
  canon,
  selectedEvent,
  onSelect,
  repaintKey,
}: MarkerLayerProps) {
  const [placed, setPlaced] = useState<Placed[]>([]);

  useEffect(() => {
    if (chart === null || series === null || markers.length === 0) {
      setPlaced([]);
      return;
    }
    const compute = (): void => {
      const next: Placed[] = [];
      for (const marker of markers) {
        const x = chart.timeScale().timeToCoordinate(marker.time as Time);
        // `timeToCoordinate` returns null for a bar outside the visible range,
        // which is the natural way to hide off-screen badges.
        if (x === null) continue;
        // Vertically: below the bar's low for a pump, above its high for a dump,
        // so the badge is anchored to the event's own bar rather than floating in
        // the pane. The candle series converts price to a pixel.
        const price = marker.direction === 1 ? marker.low : marker.high;
        const anchor = price === undefined ? null : series.priceToCoordinate(price);
        const y = anchor === null ? chart.paneSize(0).height / 2 : marker.direction === 1 ? anchor + 6 : anchor - 26;
        next.push({ marker, x, y });
      }
      setPlaced(next);
    };
    compute();
    // Recompute whenever the time scale moves: a pan or a zoom invalidates every
    // pixel position, and a stale badge would point at the wrong bar.
    const onRangeChange = (): void => compute();
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange);
    window.addEventListener('resize', onRangeChange);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRangeChange);
      window.removeEventListener('resize', onRangeChange);
    };
  }, [chart, series, markers, repaintKey]);

  if (placed.length === 0) return null;

  return (
    <div className="marker-layer" aria-hidden="false">
      {placed.map(({ marker, x, y }) => {
        const timeMs = marker.time * 1000;
        const isSelected = selectedEvent !== null && Math.floor(selectedEvent / 1000) === marker.time;
        return (
          <button
            key={`${marker.time}-${marker.letter}`}
            type="button"
            className={`marker-badge${isSelected ? ' selected' : ''}${marker.direction === 1 ? ' below' : ' above'}`}
            style={{ left: `${x}px`, top: `${y}px` }}
            onClick={() => onSelect(timeMs)}
            title={`${marker.label} · ${marker.kind} · ${marker.severity}`}
            aria-label={`${marker.letter} ${marker.kind} at ${new Date(timeMs).toISOString()}`}
          >
            <EventGlyph
              kind={marker.kind}
              severity={marker.severity}
              color={marker.color}
              letter={marker.letter}
              direction={marker.direction}
              canon={canon}
              size="sm"
              arrow={false}
            />
          </button>
        );
      })}
    </div>
  );
}
