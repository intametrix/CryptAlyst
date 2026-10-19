import { useEffect, useRef } from 'react';
import {
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  PriceScaleMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { FlowBin } from '../hooks/useFlowHistory';
import { resolvePalette } from '../palette';

export interface FlowStripProps {
  /** The bins to draw. */
  bins: readonly FlowBin[];
  /** The chart whose time scale this strip follows. */
  main: IChartApi | null;
  /** Called once the strip's own chart exists, so a caller can mirror into it. */
  onReady?: (chart: IChartApi) => void;
  height: number;
}

/**
 * The flow pane as its own chart instance.
 *
 * The library floors every pane inside one `IChartApi` at 30px, and nothing
 * outside that instance can override it — so a strip the user wants to collapse
 * to nothing cannot live in the same chart. A `div` has no floor, so this is a
 * separate instance in a CSS-sized container, with its time scale mirrored from
 * the price chart.
 *
 * The mirroring is the part that has to be right: a strip whose bars do not line
 * up with the candles is worse than the floor it replaces.
 */
export function FlowStrip({ bins, main, onReady, height }: FlowStripProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  // The instance is created once and disposed on unmount.
  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return;
    const palette = resolvePalette();
    const chart = createChart(host, {
      autoSize: false,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8492a9',
        fontFamily: getComputedStyle(document.body).fontFamily,
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#161d2b' } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: 'rgba(132, 146, 169, 0.45)', width: 1, style: LineStyle.Dashed, labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
      // No axes at all: the strip is aligned to the candles by the shared time
      // scale, and its own axis would only take width away from the plot.
      leftPriceScale: { visible: false },
      rightPriceScale: {
        visible: true,
        borderColor: '#1e2636',
        // A fixed width, so the plot origins of the two instances line up. Without
        // it the strip's axis width depends on its own values and the bars drift
        // horizontally against the candles.
        minimumWidth: 64,
        mode: PriceScaleMode.Logarithmic,
      },
      timeScale: {
        borderColor: '#1e2636',
        visible: false,
        rightOffset: 2,
        shiftVisibleRangeOnNewBar: false,
      },
      handleScroll: { vertTouchDrag: false },
    });
    const series = chart.addSeries(HistogramSeries, {
      priceScaleId: 'right',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      priceLineVisible: false,
      lastValueVisible: true,
      base: 0,
      title: 'flow burst',
    });
    series.createPriceLine({
      price: 1,
      color: '#8492a9',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: '1.0x',
    });
    chartRef.current = chart;
    seriesRef.current = series;
    void palette;
    onReady?.(chart);
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [onReady]);

  // The strip follows the price chart's logical range, one way for now.
  useEffect(() => {
    const chart = chartRef.current;
    if (chart === null || main === null) return;
    let applying = false;
    const mirror = (): void => {
      if (applying) return;
      const range = main.timeScale().getVisibleLogicalRange();
      if (range === null) return;
      applying = true;
      chart.timeScale().setVisibleLogicalRange(range);
      applying = false;
    };
    mirror();
    main.timeScale().subscribeVisibleLogicalRangeChange(mirror);
    return () => {
      main.timeScale().unsubscribeVisibleLogicalRangeChange(mirror);
    };
  }, [main]);

  // Data.
  useEffect(() => {
    const series = seriesRef.current;
    if (series === null) return;
    const palette = resolvePalette();
    series.setData(
      bins.map((bin) => ({
        time: Math.floor(bin.t / 1000) as UTCTimestamp,
        value: bin.burstMultiple,
        color: bin.burstMultiple >= 1 ? palette.up : palette.down,
      })),
    );
  }, [bins]);

  // Size follows the CSS box, not the library's pane manager.
  useEffect(() => {
    const chart = chartRef.current;
    const host = hostRef.current;
    if (chart === null || host === null) return;
    chart.resize(host.clientWidth, host.clientHeight);
    const observer = new ResizeObserver(() => {
      chart.resize(host.clientWidth, host.clientHeight);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, [height]);

  return <div className="flow-strip" ref={hostRef} style={{ height: `${height}px` }} data-strip-height={height} />;
}
