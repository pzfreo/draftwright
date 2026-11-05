export class Engine {
  [Symbol.dispose](): void;
  apply(ops: string, commit: boolean): string;
  applyFragments(ops: string): string;
  calloutOffset(address: string): string;
  constructor(name: string, bytes: Uint8Array);
  faceCount(): number;
  featureCount(): number;
  fragments(): string;
  free(): void;
  labelOffset(address: string): string;
  numberedCharacteristics(): string;
  openSpec(json: string): string;
  placementOptions(): string;
  plan(): string;
  planCount(): number;
  pmi(): string;
  preview(ops: string): string;
  redo(): string;
  renderPdf(): Uint8Array;
  renderSvg(): Uint8Array;
  setPmi(source: string, bytes: Uint8Array): void;
  setPmiOps(source: string, bytes: Uint8Array, ops: string): void;
  setSpec(json: string): void;
  sheetFragments(sheet: number): string;
  sheetSvg(sheet: number): Uint8Array;
  snapshot(): string;
  spec(): string;
  static openPart(name: string, bytes: Uint8Array, part: number): Engine;
  undo(): string;
}

export class Pack {
  [Symbol.dispose](): void;
  constructor(name: string, bytes: Uint8Array, specJson?: string);
  csv(): string;
  free(): void;
  pageCount(): number;
  pageSvg(page: number): string;
  pdf(): Uint8Array;
  reportJson(): string;
  setSpec(specJson: string): void;
  specJson(): string;
}

export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;

export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

export function listParts(name: string, bytes: Uint8Array): string;

export function specOps(before: string, after: string): string;

export function start(): void;

export interface CalloutMember {
  centre: [number, number];
  radius: number;
  span?: CalloutSpan;
}

export interface CalloutOffset {
  bend: [number, number];
  members: CalloutMember[];
  offset: [number, number];
  tip: [number, number];
  view: string;
}

export interface CalloutSpan {
  start: number;
  sweep: number;
}

export interface InitOutput {
  readonly __externref_table_dealloc: (a: number) => void;
  readonly __wbg_engine_free: (a: number, b: number) => void;
  readonly __wbg_pack_free: (a: number, b: number) => void;
  readonly __wbindgen_externrefs: WebAssembly.Table;
  readonly __wbindgen_free: (a: number, b: number, c: number) => void;
  readonly __wbindgen_malloc: (a: number, b: number) => number;
  readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
  readonly __wbindgen_start: () => void;
  readonly engine_apply: (a: number, b: number, c: number, d: number) => [number, number, number, number];
  readonly engine_applyFragments: (a: number, b: number, c: number) => [number, number, number, number];
  readonly engine_calloutOffset: (a: number, b: number, c: number) => [number, number, number, number];
  readonly engine_faceCount: (a: number) => number;
  readonly engine_featureCount: (a: number) => number;
  readonly engine_fragments: (a: number) => [number, number];
  readonly engine_labelOffset: (a: number, b: number, c: number) => [number, number, number, number];
  readonly engine_numberedCharacteristics: (a: number) => [number, number];
  readonly engine_open: (a: number, b: number, c: number, d: number) => [number, number, number];
  readonly engine_openPart: (a: number, b: number, c: number, d: number, e: number) => [number, number, number];
  readonly engine_openSpec: (a: number, b: number, c: number) => [number, number, number, number];
  readonly engine_placementOptions: (a: number) => [number, number, number, number];
  readonly engine_plan: (a: number) => [number, number, number, number];
  readonly engine_planCount: (a: number) => number;
  readonly engine_pmi: (a: number) => [number, number];
  readonly engine_preview: (a: number, b: number, c: number) => [number, number, number, number];
  readonly engine_redo: (a: number) => [number, number, number, number];
  readonly engine_renderPdf: (a: number) => [number, number, number, number];
  readonly engine_renderSvg: (a: number) => [number, number, number, number];
  readonly engine_setPmi: (a: number, b: number, c: number, d: number, e: number) => [number, number];
  readonly engine_setPmiOps: (a: number, b: number, c: number, d: number, e: number, f: number, g: number) => [number, number];
  readonly engine_setSpec: (a: number, b: number, c: number) => [number, number];
  readonly engine_sheetFragments: (a: number, b: number) => [number, number, number, number];
  readonly engine_sheetSvg: (a: number, b: number) => [number, number, number, number];
  readonly engine_snapshot: (a: number) => [number, number, number, number];
  readonly engine_spec: (a: number) => [number, number];
  readonly engine_undo: (a: number) => [number, number, number, number];
  readonly listParts: (a: number, b: number, c: number, d: number) => [number, number, number, number];
  readonly memory: WebAssembly.Memory;
  readonly pack_csv: (a: number) => [number, number];
  readonly pack_new: (a: number, b: number, c: number, d: number, e: number, f: number) => [number, number, number];
  readonly pack_pageCount: (a: number) => number;
  readonly pack_pageSvg: (a: number, b: number) => [number, number, number, number];
  readonly pack_pdf: (a: number) => [number, number];
  readonly pack_reportJson: (a: number) => [number, number];
  readonly pack_setSpec: (a: number, b: number, c: number) => [number, number];
  readonly pack_specJson: (a: number) => [number, number];
  readonly specOps: (a: number, b: number, c: number, d: number) => [number, number, number, number];
  readonly start: () => void;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export type SyncInitInput = BufferSource | WebAssembly.Module;
