/**
 * uPlot options for vakr's time-series graphs (12.6, UI-010; ADR-035 decisions 8, 10, 11).
 *
 * - The time axis and cursor use the display zone the API names
 *   (`uPlot.tzDate`), never the browser's zone silently.
 * - Inbound and outbound differ by line style as well as colour: inbound is
 *   a solid line over a light fill, outbound a dashed line without fill,
 *   so the two read apart in grayscale (12.6, WEB-T09).
 * - `null` values are gaps: `spanGaps` is off, so a line never crosses
 *   missing data and never drops to zero (WEB-T04).
 * - Quality markers are drawn as hatched or tinted bands with a letter
 *   (G gap, B baseline, R restart, D discontinuity, P partial, C collector,
 *   O over capacity) under the series; the page lists them in text too.
 *
 * Only `src/lib/charts/` imports uPlot (ADR-035 #10).
 *
 * @packageDocumentation
 */
import uPlot from 'uplot';
import type { Series, Unit } from '#lib/api/types.ts';
import type { Band } from './series.ts';
import { formatValue } from './series.ts';
import { formatInstant } from '#lib/format/time.ts';

/** The letter drawn on each kind of marker band. */
export const MARKER_LETTER: Record<Band['kind'], string> = {
	gap: 'G',
	baseline: 'B',
	restart: 'R',
	discontinuity: 'D',
	partial: 'P',
	collector: 'C',
	over_capacity: 'O'
};

/** Dash patterns per position, so two collectors' lines differ without colour. */
const POSITION_DASH: number[][] = [[], [2, 3], [10, 3, 2, 3]];

/** Colours read from the stylesheet's tokens (light or dark theme). */
export interface Palette {
	inbound: string;
	outbound: string;
	other: string[];
	fg: string;
	muted: string;
	grid: string;
	band: string;
	gap: string;
	smoke: string;
}

/**
 * Read the chart colours from CSS custom properties.
 *
 * @param element - An element inside the page.
 * @returns The palette.
 */
export function readPalette(element: Element): Palette {
	const css = getComputedStyle(element);
	const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
	return {
		inbound: v('--series-in', '#1f5fae'),
		outbound: v('--series-out', '#a14a00'),
		other: [v('--series-3', '#2e7d4f'), v('--series-4', '#7b3fa0')],
		fg: v('--fg', '#16191f'),
		muted: v('--fg-muted', '#4a5160'),
		grid: v('--border', '#c9ced8'),
		band: v('--band', 'rgba(122,74,0,0.12)'),
		gap: v('--band-gap', 'rgba(74,81,96,0.14)'),
		smoke: v('--smoke', 'rgba(60,70,90,0.35)')
	};
}

/**
 * Style one series: direction decides solid or dashed, position decides the dash rhythm.
 *
 * @param s - The API series.
 * @param index - Its index in the answer.
 * @param positions - Position names in order of first appearance.
 * @param palette - Colours.
 * @param unit - The display unit, for legend values.
 * @returns The uPlot series options.
 */
export function seriesStyle(
	s: Series,
	index: number,
	positions: string[],
	palette: Palette,
	unit: Unit
): uPlot.Series {
	const inbound = s.metric.startsWith('in_');
	const outbound = s.metric.startsWith('out_');
	const dash = outbound ? [7, 4] : (POSITION_DASH[positions.indexOf(s.position)] ?? []);
	const stroke = inbound
		? palette.inbound
		: outbound
			? palette.outbound
			: (palette.other[index % palette.other.length] ?? palette.fg);
	return {
		label: s.label,
		stroke,
		width: 2,
		dash,
		fill: inbound ? withAlpha(stroke, 0.12) : undefined,
		spanGaps: false,
		value: (_u, v) => formatValue(v, unit.display)
	};
}

/**
 * Make a translucent version of a colour for fills.
 *
 * @param color - `#rrggbb` or any CSS colour.
 * @param alpha - Opacity, 0 to 1.
 * @returns An `rgba()` colour, or the colour unchanged when it is not `#rrggbb`.
 */
export function withAlpha(color: string, alpha: number): string {
	const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
	if (!m) return color;
	const [r, g, b] = [m[1], m[2], m[3]].map((h) => parseInt(h ?? '0', 16));
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Build the draw hook that paints marker bands under the series.
 *
 * @param bands - Marker stretches.
 * @param palette - Colours.
 * @returns A `drawClear` hook.
 */
export function bandHook(bands: Band[], palette: Palette): (u: uPlot) => void {
	return (u: uPlot) => {
		const ctx = u.ctx;
		const { top, height } = u.bbox;
		ctx.save();
		ctx.font = `${Math.round(11 * devicePixelRatio)}px ${getComputedStyle(u.root).fontFamily}`;
		for (const band of bands) {
			const x0 = u.valToPos(band.start, 'x', true);
			const x1 = u.valToPos(band.end, 'x', true);
			if (!Number.isFinite(x0) || !Number.isFinite(x1) || x1 <= x0) continue;
			ctx.fillStyle = band.kind === 'gap' ? palette.gap : palette.band;
			ctx.fillRect(x0, top, Math.max(x1 - x0, 1), height);
			ctx.fillStyle = palette.muted;
			ctx.fillText(
				MARKER_LETTER[band.kind],
				x0 + 2 * devicePixelRatio,
				top + 12 * devicePixelRatio
			);
		}
		ctx.restore();
	};
}

/**
 * Build the draw hook for reference lines such as capacity and contracted rate.
 *
 * @param refs - Values in the display unit, with labels.
 * @param palette - Colours.
 * @returns A `draw` hook.
 */
export function referenceHook(
	refs: { value: number; label: string }[],
	palette: Palette
): (u: uPlot) => void {
	return (u: uPlot) => {
		const ctx = u.ctx;
		const { left, width } = u.bbox;
		ctx.save();
		ctx.strokeStyle = palette.muted;
		ctx.fillStyle = palette.muted;
		ctx.setLineDash([2 * devicePixelRatio, 4 * devicePixelRatio]);
		ctx.font = `${Math.round(11 * devicePixelRatio)}px ${getComputedStyle(u.root).fontFamily}`;
		for (const ref of refs) {
			const y = u.valToPos(ref.value, 'y', true);
			if (!Number.isFinite(y)) continue;
			ctx.beginPath();
			ctx.moveTo(left, y);
			ctx.lineTo(left + width, y);
			ctx.stroke();
			ctx.fillText(ref.label, left + 4 * devicePixelRatio, y - 3 * devicePixelRatio);
		}
		ctx.restore();
	};
}

/**
 * Tick labels in the display zone, 24-hour: `HH:MM`, with the date where the day changes.
 *
 * @param zone - The display zone.
 * @returns uPlot's `values` function for the time axis.
 */
export function timeTicks(zone: string): uPlot.Axis.DynamicValues {
	return (_u, splits, _ax, _space, incr) => {
		let day = '';
		return splits.map((s) => {
			const text = formatInstant(new Date(s * 1000), zone);
			if (incr >= 86400) return text.slice(0, 10);
			const date = text.slice(5, 10);
			const label = date === day ? text.slice(11, 16) : `${date} ${text.slice(11, 16)}`;
			day = date;
			return label;
		});
	};
}

/**
 * The time axis: 24-hour ticks in the display zone.
 *
 * @param palette - Colours.
 * @param zone - The display zone.
 * @returns The x axis.
 */
export function timeAxis(palette: Palette, zone = 'UTC'): uPlot.Axis {
	return {
		stroke: palette.muted,
		grid: { stroke: palette.grid, width: 1 },
		ticks: { show: false },
		values: timeTicks(zone),
		space: 80
	};
}

/**
 * Convert Unix seconds to a `Date` whose fields read as wall time in `zone`.
 *
 * @param zone - The display zone.
 * @returns The converter uPlot calls for axes and the cursor.
 */
export function zonedDate(zone: string): (ts: number) => Date {
	return (ts: number) => uPlot.tzDate(new Date(ts * 1000), zone);
}
