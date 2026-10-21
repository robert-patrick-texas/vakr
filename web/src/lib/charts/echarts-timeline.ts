/**
 * The interface state timeline drawn with ECharts, loaded on demand (12.5; ADR-035 #9, E.31).
 *
 * uPlot draws series of values on a time axis; it has no series type for
 * spans of discrete states (a Gantt-like row per variable), which is what
 * an admin/oper status timeline is. ECharts' `custom` series draws exactly
 * that. This module is imported only with `import()` from
 * `StatusTimeline.svelte`, so it and the ECharts code it pulls in form a
 * separate chunk that pages without a timeline never download. Only the
 * parts used are registered (core, custom series, grid, canvas renderer),
 * so the bundler drops the rest.
 *
 * The chart is canvas only: no ECharts tooltip, whose HTML would carry
 * inline styles the CSP refuses. The segment table beside the chart gives
 * every value in text (12.8).
 *
 * @packageDocumentation
 */
import * as echarts from 'echarts/core';
import { CustomChart } from 'echarts/charts';
import { GridComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { StateSegment } from '#lib/api/types.ts';

echarts.use([CustomChart, GridComponent, CanvasRenderer]);

/** Lets the build find this chunk and prove it is not in the initial bundle (E.31). */
export const CHUNK_MARK = 'vakr-lazy-chunk:echarts';

/** Colours and the time formatter the page supplies. */
export interface TimelineStyle {
	up: string;
	down: string;
	degraded: string;
	unknown: string;
	text: string;
	grid: string;
	formatTime: (ms: number) => string;
}

const ROWS = ['Admin', 'Oper'];

/**
 * Pick the colour of one state.
 *
 * @param state - `up`, `down`, `testing`, `lower-layer-down`, ..., or `null` (no samples).
 * @param style - The palette.
 * @returns A CSS colour.
 */
export function stateColor(state: string | null, style: TimelineStyle): string {
	if (state === 'up') return style.up;
	if (state === 'down' || state === 'lower-layer-down') return style.down;
	if (state === null || state === 'unknown' || state === 'not-present') return style.unknown;
	return style.degraded;
}

type Item = [row: number, start: number, end: number, label: string];

/**
 * Turn segments into custom-series items: one per row (admin, oper) per segment.
 *
 * @param segments - The API's segments.
 * @returns Items with times in epoch milliseconds.
 */
export function timelineItems(segments: StateSegment[]): Item[] {
	return segments.flatMap((s) => {
		const start = Date.parse(s.start);
		const end = Date.parse(s.end);
		return [
			[0, start, end, s.admin ?? 'no data'],
			[1, start, end, s.oper ?? 'no data']
		] satisfies Item[];
	});
}

/**
 * The chart options for a set of segments.
 *
 * @param items - Custom-series items.
 * @param style - Colours and the time formatter.
 * @returns ECharts options.
 */
function timelineOption(items: Item[], style: TimelineStyle): echarts.EChartsCoreOption {
	return {
		animation: false,
		grid: { left: 60, right: 16, top: 8, bottom: 28 },
		xAxis: {
			type: 'time',
			axisLabel: { color: style.text, formatter: (v: number) => style.formatTime(v) },
			splitLine: { lineStyle: { color: style.grid } }
		},
		yAxis: { type: 'category', data: ROWS, axisLabel: { color: style.text } },
		series: [
			{
				type: 'custom',
				data: items,
				encode: { x: [1, 2], y: 0 },
				renderItem: (params: { dataIndex: number }, api: RenderApi) => {
					const label = String(items[params.dataIndex]?.[3] ?? '');
					return segmentRect(api, label, style);
				}
			}
		]
	};
}

/** The parts of ECharts' custom-series API the renderer uses. */
interface RenderApi {
	value: (dim: number) => number;
	coord: (point: number[]) => number[];
	size?: (size: number[]) => number[] | number;
}

/**
 * Draw one segment as a rectangle with its state written inside when it fits.
 *
 * @param api - ECharts' render API for the item.
 * @param label - The state word.
 * @param style - Colours.
 * @returns The rectangle element.
 */
function segmentRect(api: RenderApi, label: string, style: TimelineStyle): object {
	const row = api.value(0);
	const start = api.coord([api.value(1), row]);
	const end = api.coord([api.value(2), row]);
	const size = api.size?.([0, 1]);
	const height = (Array.isArray(size) ? (size[1] ?? 20) : 20) * 0.6;
	const width = Math.max((end[0] ?? 0) - (start[0] ?? 0), 1);
	return {
		type: 'rect',
		shape: { x: start[0], y: (start[1] ?? 0) - height / 2, width, height },
		style: {
			fill: stateColor(label === 'no data' ? null : label, style),
			text: width > 40 ? label : '',
			textFill: '#ffffff',
			fontSize: 11
		}
	};
}

/** A mounted timeline chart. */
export interface Timeline {
	update: (segments: StateSegment[], style: TimelineStyle) => void;
	dispose: () => void;
}

/**
 * Mount a timeline chart in an element; it follows the element's size.
 *
 * @param element - The container; it needs a height.
 * @returns The chart's update and dispose functions.
 */
export function createTimeline(element: HTMLElement): Timeline {
	const chart = echarts.init(element, null, { renderer: 'canvas' });
	element.dataset['chunk'] = CHUNK_MARK;
	const resize = new ResizeObserver(() => chart.resize());
	resize.observe(element);
	return {
		update: (segments, style) =>
			chart.setOption(timelineOption(timelineItems(segments), style), { notMerge: true }),
		dispose: () => {
			resize.disconnect();
			chart.dispose();
		}
	};
}
