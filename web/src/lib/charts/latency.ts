/**
 * SmokePing-style latency data for one collector position (12.6, ICMP-009, WEB-T05).
 *
 * The API sends, per position, every poll's per-probe RTTs (`probes`, the
 * "smoke"), the median series, the loss series, and jitter. `latencyData`
 * lays them on one time axis in the RTT display unit:
 *
 * - `smoke[i]` holds every probe RTT of poll `i`; a lost probe is left out
 *   (it has no RTT), and the loss series counts it.
 * - `median` and `loss` keep `null` for polls without a value, so the
 *   median line breaks where every probe was lost instead of dropping to 0.
 * - In `median_envelope` mode (long windows) there are no probes; `low`
 *   and `high` bound the per-poll medians in each bucket.
 *
 * Positions are never merged (MPOS-008): one call per position.
 *
 * @example
 * ```ts
 * const d = latencyData(view.positions[0], view.rtt_unit);
 * ```
 *
 * @packageDocumentation
 */
import type { PositionLatency, Unit } from '#lib/api/types.ts';
import { epoch } from './series.ts';

/** One position's latency on one time axis. */
export interface LatencyData {
	xs: number[];
	smoke: number[][];
	median: (number | null | undefined)[];
	loss: (number | null | undefined)[];
	low: (number | null | undefined)[];
	high: (number | null | undefined)[];
	lostProbes: number;
	probes: number;
}

/**
 * Lay out one position's smoke, median, loss, and envelope.
 *
 * @param position - One `positions[]` entry of a `LatencyView`.
 * @param rtt - The RTT unit (`us` shown as `ms`).
 * @returns The arrays, times ascending.
 */
export function latencyData(position: PositionLatency, rtt: Unit): LatencyData {
	const times = new Set<number>();
	position.probes.forEach((p) => times.add(epoch(p.t)));
	position.median.points.forEach((p) => times.add(epoch(p.t)));
	position.loss.points.forEach((p) => times.add(epoch(p.t)));
	const xs = [...times].sort((a, b) => a - b);
	const at = new Map(xs.map((x, i) => [x, i]));
	const empty = () => new Array<number | null | undefined>(xs.length);
	const out: LatencyData = {
		xs,
		smoke: xs.map(() => []),
		median: empty(),
		loss: empty(),
		low: empty(),
		high: empty(),
		lostProbes: 0,
		probes: 0
	};
	for (const p of position.probes) {
		const i = at.get(epoch(p.t));
		if (i === undefined) continue;
		for (const r of p.rtts_us) {
			out.probes += 1;
			if (r === null) out.lostProbes += 1;
			else out.smoke[i]?.push(r / rtt.divisor);
		}
	}
	for (const p of position.median.points) {
		const i = at.get(epoch(p.t));
		if (i === undefined) continue;
		out.median[i] = p.value === null ? null : p.value / rtt.divisor;
		out.low[i] = p.low === null || p.low === undefined ? null : p.low / rtt.divisor;
		out.high[i] = p.high === null || p.high === undefined ? null : p.high / rtt.divisor;
	}
	for (const p of position.loss.points) {
		const i = at.get(epoch(p.t));
		if (i !== undefined) out.loss[i] = p.value;
	}
	return out;
}

/**
 * Count polls by outcome, for the latency summary.
 *
 * @param data - One position's data.
 * @returns Polls with a loss value, polls with some loss, and polls with total loss.
 */
export function lossCounts(data: LatencyData): { polls: number; lossy: number; dark: number } {
	let polls = 0;
	let lossy = 0;
	let dark = 0;
	for (const v of data.loss) {
		if (v === null || v === undefined) continue;
		polls += 1;
		if (v > 0) lossy += 1;
		if (v >= 100) dark += 1;
	}
	return { polls, lossy, dark };
}
