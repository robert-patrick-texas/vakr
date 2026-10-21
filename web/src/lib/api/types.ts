/**
 * Short names for the API v1 answer shapes, taken from the generated OpenAPI types.
 *
 * Every type here is an alias of `components['schemas'][...]` in
 * `schema.d.ts`, which `npm run gen:api` generates from
 * `docs/api/openapi.json` and CI checks for drift (ADR-035 decision 5). A
 * missing value is `null` in these shapes, never `0` (invariant 1).
 *
 * @example
 * ```ts
 * import type { DeviceRow } from '#lib/api/types.ts';
 * const name = (row: DeviceRow) => row.name;
 * ```
 *
 * @packageDocumentation
 */
import type { components } from './schema.d.ts';

type Schemas = components['schemas'];

export type BucketCounts = Schemas['BucketCounts'];
export type CollectorHealth = Schemas['CollectorHealthView'];
export type CollectorOut = Schemas['CollectorOut'];
export type CollectorPage = Schemas['CollectorPage'];
export type Coverage = Schemas['CoverageView'];
export type DeviceDetail = Schemas['DeviceDetailView'];
export type DeviceListPage = Schemas['DeviceListPage'];
export type DeviceRow = Schemas['DeviceRow'];
export type DeviceSort = Schemas['DeviceSort'];
export type DeviceSummary = Schemas['DeviceSummary'];
export type ErrorEnvelope = Schemas['ErrorEnvelope'];
export type ErrorRow = Schemas['ErrorRow'];
export type EventFeed = Schemas['EventFeed'];
export type ExcludedRow = Schemas['ExcludedRow'];
export type GraphKind = Schemas['GraphKind'];
export type GraphView = Schemas['GraphView'];
export type HealthInfo = Schemas['HealthInfo'];
export type HealthView = Schemas['HealthView'];
export type IdentityEntry = Schemas['IdentityEntryOut'];
export type IdentityHistoryPage = Schemas['IdentityHistoryPage'];
export type InterfaceDetail = Schemas['InterfaceDetail'];
export type InterfaceListPage = Schemas['InterfaceListPage'];
export type InterfaceListRow = Schemas['InterfaceListRow'];
export type InterfaceListView = Schemas['InterfaceListView'];
export type InterfaceOut = Schemas['InterfaceOut'];
export type InterfaceRef = Schemas['InterfaceRef'];
export type InterfaceRow = Schemas['InterfaceRow'];
export type InterfaceSort = Schemas['InterfaceSort'];
export type KioskIssued = Schemas['KioskIssued'];
export type KioskTokenOut = Schemas['KioskTokenOut'];
export type KioskTokenPage = Schemas['KioskTokenPage'];
export type LabelPage = Schemas['LabelPage'];
export type LatencyView = Schemas['LatencyView'];
export type Marker = Schemas['MarkerView'];
export type MarkerKind = Schemas['MarkerKind'];
export type OverviewView = Schemas['OverviewView'];
export type PageInfo = Schemas['PageInfo'];
export type Point = Schemas['PointView'];
export type PositionLatency = Schemas['PositionLatency'];
export type RangePreset = Schemas['RangePreset'];
export type RankingWindow = Schemas['RankingWindow'];
export type ErrorRanking = Schemas['RankingView_ErrorRow_'];
export type UtilizationRanking = Schemas['RankingView_UtilizationRow_'];
export type Reading = Schemas['Reading'];
export type Reference = Schemas['ReferenceView'];
export type Resolution = Schemas['ResolutionView'];
export type ReviewOut = Schemas['ReviewOut'];
export type ReviewPage = Schemas['ReviewPage'];
export type SearchHit = Schemas['SearchHit'];
export type SearchKind = Schemas['SearchKind'];
export type SearchView = Schemas['SearchView'];
export type Series = Schemas['SeriesView'];
export type SessionInfo = Schemas['SessionInfo'];
export type SitePage = Schemas['SitePage'];
export type SmokePoint = Schemas['SmokePoint'];
export type SourceChoice = Schemas['SourceChoice'];
export type StateSegment = Schemas['StateSegment'];
export type StatusBucket = Schemas['StatusBucket'];
export type StatusColor = Schemas['StatusColor'];
export type StatusView = Schemas['StatusView'];
export type TimelineView = Schemas['TimelineView'];
export type Unit = Schemas['UnitView'];
export type UtilizationRow = Schemas['UtilizationRow'];
export type VersionInfo = Schemas['VersionInfo'];
export type WindowView = Schemas['WindowView'];
