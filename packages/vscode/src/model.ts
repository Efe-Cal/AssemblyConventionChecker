export interface SourceLocation { filename: string; line: number; column: number }
export type Category = 'error' | 'warning' | 'analysis_gap';
export interface Finding {
  category: Category; rule_id: string; message: string; location: SourceLocation;
  function: string | null; suggestion: string; related_locations: SourceLocation[];
}
export interface FunctionCoverage {
  name: string; aliases: string[]; location: SourceLocation; boundaries_inferred: boolean;
  total_instructions: number; reachable_instructions: number; analyzed_instructions: number;
  complete: boolean; incomplete_checks: string[];
}
export interface AnalysisReport {
  filename: string; schema_version: 1; complete: boolean; input_errors: boolean;
  diagnostics: Finding[]; functions: FunctionCoverage[];
}
export type ReportState = 'empty' | 'loading' | 'ready' | 'error' | 'disabled' | 'untrusted';
export interface Snapshot {
  state: ReportState; uri?: string; filename?: string; version?: number; source?: string;
  report?: AnalysisReport; error?: string; duration?: number; interpreter?: string;
  sources?: Record<string, string>;
}

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const location = (value: unknown): value is SourceLocation => object(value) && typeof value.filename === 'string' && count(value.line) && value.line > 0 && count(value.column) && value.column > 0;

export function parseReport(output: string): AnalysisReport {
  const invalid = () => new Error('The checker returned an invalid or unsupported report. Open Output for details.');
  let payload: unknown;
  try { payload = JSON.parse(output); } catch { throw invalid(); }
  if (!object(payload) || payload.schema_version !== 1 || !Array.isArray(payload.reports) || payload.reports.length !== 1) { throw invalid(); }
  const report: unknown = payload.reports[0];
  if (!object(report) || report.schema_version !== 1 || typeof report.filename !== 'string' || typeof report.complete !== 'boolean' || typeof report.input_errors !== 'boolean' || !Array.isArray(report.diagnostics) || !Array.isArray(report.functions)) { throw invalid(); }
  for (const d of report.diagnostics) {
    if (!object(d) || !['error', 'warning', 'analysis_gap'].includes(String(d.category)) || typeof d.rule_id !== 'string' || typeof d.message !== 'string' || !location(d.location) || !(d.function === null || typeof d.function === 'string') || typeof d.suggestion !== 'string' || !Array.isArray(d.related_locations) || !d.related_locations.every(location)) { throw invalid(); }
  }
  for (const f of report.functions) {
    if (!object(f) || typeof f.name !== 'string' || !strings(f.aliases) || !location(f.location) || typeof f.boundaries_inferred !== 'boolean' || typeof f.complete !== 'boolean' || !strings(f.incomplete_checks) || !count(f.total_instructions) || !count(f.reachable_instructions) || !count(f.analyzed_instructions) || f.analyzed_instructions > f.reachable_instructions || f.reachable_instructions > f.total_instructions) { throw invalid(); }
  }
  return report as unknown as AnalysisReport;
}

export function sourceRange(source: string, location: SourceLocation) {
  const lines = source.split('\n').map(line => line.replace(/\r$/, ''));
  const line = Math.max(0, Math.min(location.line - 1, lines.length - 1));
  const text = lines[line];
  const characters = Array.from(text);
  const offset = Math.min(location.column - 1, characters.length);
  const start = characters.slice(0, offset).join('').length;
  const token = /^[^\s,;()]+/.exec(text.slice(start))?.[0];
  const end = Math.min(text.length, start + (token?.length ?? (characters[offset]?.length ?? 0)));
  return {line, start, end};
}

export const ruleTitles: Record<string, string> = {
  ABI_STACK_ALIGNMENT: 'Stack misaligned at transfer', ABI_STACK_RESTORE: 'Stack pointer not restored',
  ABI_RETURN_ADDRESS: 'Return address not preserved', ABI_CALLEE_SAVED: 'Callee-saved register not preserved',
  ABI_DIRECTION_FLAG: 'Direction flag must be clear', ABI_RED_ZONE_BOUNDS: 'Access outside protected stack storage',
  ABI_RED_ZONE_LIVE: 'Stack data may be clobbered by a call', ABI_RET_CLEANUP: 'Unexpected stack cleanup on return',
  INPUT_IO: 'Cannot read source file', INPUT_SYNTAX: 'Malformed assembly source', INPUT_INSTRUCTION: 'Invalid instruction',
  INPUT_INCLUDE: 'Cannot expand included source',
  ANALYSIS_UNKNOWN: 'Analysis requires manual review',
  ANALYSIS_UNSUPPORTED: 'Unsupported instruction', ANALYSIS_DIRECTIVE: 'Directive prevents complete analysis',
  ANALYSIS_EXPANSION: 'Source requires expansion', ANALYSIS_MODE: 'Unsupported assembly mode',
  ANALYSIS_NO_ENTRY: 'No callable function entry', ANALYSIS_BOUNDARY: 'Function boundaries require review',
  ANALYSIS_EMPTY_FUNCTION: 'No instructions in function', ANALYSIS_ENTRY_CONVENTION: 'Entry uses an unsupported convention',
  ANALYSIS_FALLTHROUGH: 'Reachable fallthrough outside function', ANALYSIS_LIMIT: 'Analysis work limit reached',
  ANALYSIS_UNMARKED_CALLEE: 'Call targets an unmarked helper',
};

// A generation also guards reruns of the same document version (e.g. settings changes).
export class RequestLedger {
  private sequence = 0;
  private current = new Map<string, {generation: number; version: number}>();
  begin(uri: string, version: number) {
    const generation = ++this.sequence;
    this.current.set(uri, {generation, version});
    return generation;
  }
  accepts(uri: string, generation: number, version: number) {
    const request = this.current.get(uri);
    return request?.generation === generation && request.version === version;
  }
  invalidate(uri: string) { this.current.delete(uri); }
}
