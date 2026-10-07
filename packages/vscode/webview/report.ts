interface ViewLocation {filename: string; line: number; column: number}
interface ViewFinding {category: 'error' | 'warning' | 'analysis_gap'; rule_id: string; message: string; location: ViewLocation; function: string | null; suggestion: string; related_locations: ViewLocation[]}
interface ViewFunction {name: string; aliases: string[]; location: ViewLocation; boundaries_inferred: boolean; total_instructions: number; reachable_instructions: number; analyzed_instructions: number; complete: boolean; incomplete_checks: string[]}
interface ViewReport {filename: string; complete: boolean; input_errors: boolean; diagnostics: ViewFinding[]; functions: ViewFunction[]}
interface ViewSnapshot {state: string; uri?: string; filename?: string; version?: number; source?: string; sources?: Record<string, string>; duration?: number; interpreter?: string; error?: string; report?: ViewReport; titles?: Record<string, string>}
declare function acquireVsCodeApi(): {postMessage(message: unknown): void; getState(): any; setState(state: unknown): void};

const vscode = acquireVsCodeApi();
const content = document.getElementById('content')!;
let snapshot: ViewSnapshot = {state: 'empty'};
const saved = vscode.getState();
let selectedFunction: string = saved?.function ?? '';
let severity: string = saved?.severity ?? 'all';
let currentUri: string = saved?.uri ?? '';
const categoryLabels = {error: 'Error', warning: 'Warning', analysis_gap: 'Analysis gap'};
const glyphs = {error: '×', warning: '!', analysis_gap: '?'};

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag); node.className = className;
  if (text !== undefined) { node.textContent = text; }
  return node;
}
function append(parent: HTMLElement, ...nodes: (Node | string)[]) { parent.append(...nodes); return parent; }
function action(label: string, className: string, callback: () => void) {
  const button = el('button', className, label); button.type = 'button'; button.addEventListener('click', callback); return button;
}
function post(type: string) { vscode.postMessage({type}); }
function navigate(kind: 'finding' | 'function' | 'related', index: number, relatedIndex?: number) {
  if (snapshot.state !== 'ready') { return; }
  vscode.postMessage({type: 'navigate', kind, index, relatedIndex, uri: snapshot.uri, version: snapshot.version});
}
function remember() { vscode.setState({function: selectedFunction, severity, uri: currentUri}); }
function labeledBadge(text: string, className = '') { return el('span', `badge ${className}`, text); }
function coverage(functions: ViewFunction[]) {
  return functions.reduce((sum, f) => ({analyzed: sum.analyzed + f.analyzed_instructions, reachable: sum.reachable + f.reachable_instructions, total: sum.total + f.total_instructions}), {analyzed: 0, reachable: 0, total: 0});
}
function progress(analyzed: number, reachable: number, label: string) {
  const bar = el('progress', 'coverage-bar'); bar.max = Math.max(1, reachable); bar.value = analyzed;
  bar.setAttribute('aria-label', label); return bar;
}

function stateView() {
  const states: Record<string, {symbol: string; eyebrow: string; title: string; message: string}> = {
    empty: {symbol: '⌁', eyebrow: 'READY WHEN YOU ARE', title: snapshot.filename ? 'Your next check starts here.' : 'Every instruction has a contract.', message: snapshot.error ?? 'Open a GNU AT&T assembly file to inspect its calling convention, stack discipline, and analysis coverage.'},
    loading: {symbol: '↻', eyebrow: 'ANALYSIS IN PROGRESS', title: 'Following the instructions.', message: 'Tracing control flow, stack operations, register preservation, and the direction flag.'},
    error: {symbol: '!', eyebrow: 'CHECKER UNAVAILABLE', title: 'Let’s reconnect the checker.', message: snapshot.error ?? 'Analysis could not complete. Open Output for details.'},
    disabled: {symbol: 'Ⅱ', eyebrow: 'ANALYSIS PAUSED', title: 'The checker is disabled.', message: 'Enable Assembly Convention Checker in VS Code settings to resume live analysis.'},
    untrusted: {symbol: '◇', eyebrow: 'WORKSPACE TRUST', title: 'A trusted place to analyze.', message: 'Trust this workspace through VS Code to run the Python checker. Assembly is analyzed as source and is never assembled or executed.'},
  };
  const state = states[snapshot.state] ?? states.empty;
  const section = el('section', `state-view state-${snapshot.state}`);
  append(section, el('div', 'state-symbol', state.symbol), el('div', 'eyebrow', state.eyebrow), el('h1', 'state-title', state.title), el('p', 'state-message', state.message));
  if (snapshot.state === 'error') {
    append(section, append(el('div', 'state-actions'), action('Select Python ↗', 'button-solid', () => post('python')), action('View output', 'button-quiet', () => post('output'))));
  } else if (snapshot.state === 'empty') {
    append(section, action('Analyze current file ↗', 'button-solid', () => post('analyze')));
  }
  const scope = el('div', 'scope-preview');
  for (const [number, name, detail] of [['01', 'Stack discipline', 'Alignment · restoration · return address'], ['02', 'Register contract', 'Callee-saved registers · direction flag'], ['03', 'Protected storage', 'Red-zone bounds · data live across calls']]) {
    append(scope, append(el('div', 'scope-item'), el('span', 'scope-number', number), append(el('div'), el('h2', '', name), el('p', '', detail))));
  }
  append(section, scope); return section;
}

function syntax(text: string) {
  const fragment = document.createDocumentFragment();
  const regex = /#[^\n]*|%[a-zA-Z][a-zA-Z0-9]*|\$[\w+-]+|\.[a-zA-Z_][\w.]*|\b(?:callq?|retq?|pushq?|popq?|mov[a-z]*|add[a-z]*|sub[a-z]*|std|cld|leave|jmp|nop)\b/g;
  let last = 0;
  for (const match of text.matchAll(regex)) {
    const index = match.index!;
    fragment.append(document.createTextNode(text.slice(last, index)));
    const token = match[0];
    fragment.append(el('span', token.startsWith('#') ? 'syntax-comment' : token.startsWith('%') ? 'syntax-register' : token.startsWith('$') ? 'syntax-number' : token.startsWith('.') ? 'syntax-directive' : 'syntax-op', token));
    last = index + token.length;
  }
  fragment.append(document.createTextNode(text.slice(last))); return fragment;
}
function excerpt(finding: ViewFinding) {
  const box = el('div', 'source-excerpt');
  const source = finding.location.filename === snapshot.report?.filename ? snapshot.source : snapshot.sources?.[finding.location.filename];
  const lines = (source ?? '').split('\n').map(line => line.replace(/\r$/, ''));
  const line = finding.location.line;
  if (line < 1 || line > lines.length) { return append(box, el('div', 'source-unavailable', `Source unavailable at line ${line}.`)); }
  for (let number = Math.max(1, line - 1); number <= Math.min(lines.length, line + 1); number++) {
    const row = el('div', number === line ? `source-row source-target ${finding.category}` : 'source-row');
    const code = el('code', 'source-code');
    if (number === line) {
      const text = lines[number - 1];
      const start = Array.from(text).slice(0, finding.location.column - 1).join('').length;
      const token = /^[^\s,;()]+/.exec(text.slice(start))?.[0] ?? text.slice(start, start + 1);
      const mark = el('mark', 'source-highlight'); mark.append(syntax(token));
      code.append(syntax(text.slice(0, start)), mark, syntax(text.slice(start + token.length)));
    } else { code.append(syntax(lines[number - 1])); }
    append(row, el('span', 'source-gutter', String(number)), el('span', 'source-indicator', number === line ? '›' : ''), code);
    box.append(row);
  }
  return box;
}

function findingCard(finding: ViewFinding, index: number) {
  const card = el('article', `finding-card ${finding.category}`);
  const heading = el('div', 'finding-heading');
  append(heading, el('span', `severity-icon ${finding.category}`, glyphs[finding.category]), append(el('div', 'finding-title-group'), append(el('div', 'finding-meta'), el('span', 'severity-label', categoryLabels[finding.category]), el('code', 'rule-id', finding.rule_id)), append(el('h3'), action(snapshot.titles?.[finding.rule_id] ?? finding.rule_id.replace(/_/g, ' ').toLowerCase(), 'finding-title', () => navigate('finding', index)))));
  const file = finding.location.filename === snapshot.report?.filename ? '' : `${finding.location.filename.split(/[\\/]/).pop()} · `;
  const location = action(`${file}L${finding.location.line}:${finding.location.column} ↗`, 'location-button', () => navigate('finding', index)); location.setAttribute('aria-label', `Go to ${file}line ${finding.location.line}, column ${finding.location.column}`);
  append(heading, location); card.append(heading);
  card.append(excerpt(finding));
  const details = el('div', 'finding-details');
  append(details, el('p', 'finding-message', finding.message));
  if (finding.suggestion) { append(details, append(el('div', 'suggestion'), el('span', 'eyebrow', 'CORRECTION'), el('p', '', finding.suggestion))); }
  if (finding.related_locations.length || finding.function) {
    const related = el('div', 'related-operations');
    if (finding.function) { append(related, el('span', 'function-tag', `ƒ ${finding.function}`)); }
    finding.related_locations.forEach((loc, relatedIndex) => {
      if (loc.filename === snapshot.report?.filename || snapshot.sources?.[loc.filename] !== undefined) { append(related, action(`Related · ${loc.filename === snapshot.report?.filename ? '' : loc.filename.split(/[\\/]/).pop() + ' · '}L${loc.line}:${loc.column} ↗`, 'related-link', () => navigate('related', index, relatedIndex))); }
      else { append(related, el('span', 'related-link', `Related · ${loc.filename}:${loc.line}:${loc.column}`)); }
    });
    append(details, related);
  }
  append(card, details); return card;
}

function readyView() {
  const report = snapshot.report!;
  if (selectedFunction && !report.functions.some(f => f.name === selectedFunction)) { selectedFunction = ''; }
  const counts = {error: 0, warning: 0, analysis_gap: 0};
  report.diagnostics.forEach(d => counts[d.category]++);
  const c = coverage(report.functions);
  const section = el('section', 'report');
  const filename = snapshot.filename ?? report.filename;
  const parts = filename.split(/[\\/]/);
  const file = parts.pop() ?? filename;
  const hero = el('div', 'report-hero');
  const updating = snapshot.state === 'loading';
  append(hero, append(el('div', 'hero-topline'), el('span', 'eyebrow', 'ASSEMBLY INSPECTION'), labeledBadge(updating ? 'Updating…' : report.input_errors ? 'Input error' : counts.error ? 'Issues detected' : !report.complete || counts.warning ? 'Review required' : 'Checks passed', updating ? '' : counts.error || report.input_errors ? 'error' : !report.complete || counts.warning ? 'warning' : 'healthy')));
  append(hero, el('h1', 'file-title', file), el('p', 'file-path', parts.join('/') || 'Unsaved buffer'));
  const metadata = el('div', 'run-metadata');
  append(metadata, el('span', '', updating ? '○ Updating source…' : '● Live source'), el('span', '', snapshot.duration === undefined ? '' : `${snapshot.duration.toLocaleString()} ms`), el('span', 'interpreter', snapshot.interpreter ?? ''));
  append(hero, metadata); append(section, hero);

  const metrics = el('div', 'metrics');
  for (const category of ['error', 'warning', 'analysis_gap'] as const) {
    const metric = action('', `metric ${category}`, () => { severity = severity === category ? 'all' : category; remember(); render(); });
    metric.dataset.focus = `metric-${category}`; metric.setAttribute('aria-label', `Filter ${categoryLabels[category].toLowerCase()} findings: ${counts[category]}`); metric.setAttribute('aria-pressed', String(severity === category));
    append(metric, append(el('div', 'metric-top'), el('span', 'metric-label', category === 'error' ? 'Errors' : category === 'warning' ? 'Warnings' : 'Analysis gaps'), el('span', 'metric-symbol', glyphs[category])), el('span', 'metric-value', String(counts[category]).padStart(2, '0')));
    append(metrics, metric);
  }
  const coverageCard = el('div', `coverage-card ${report.complete ? 'complete' : 'incomplete'}`);
  append(coverageCard, append(el('div', 'coverage-heading'), el('span', 'metric-label', 'Analysis coverage'), labeledBadge(report.complete ? 'Complete' : 'Incomplete', report.complete ? 'healthy' : 'warning')),
    append(el('div', 'coverage-value'), el('span', 'coverage-number', `${c.analyzed}`), el('span', 'coverage-denominator', `/ ${c.reachable}`), el('span', 'coverage-unit', 'reachable instructions')),
    progress(c.analyzed, c.reachable, `${c.analyzed} of ${c.reachable} reachable instructions analyzed`), el('p', 'coverage-footnote', `${report.functions.length} function${report.functions.length === 1 ? '' : 's'} · ${c.total} total instructions`));
  append(metrics, coverageCard); append(section, metrics);

  const layout = el('div', 'report-layout');
  const sidebar = el('aside', 'function-sidebar'); sidebar.setAttribute('aria-label', 'Function navigator');
  append(sidebar, append(el('div', 'section-heading'), el('h2', '', 'Functions'), el('span', 'section-count', String(report.functions.length).padStart(2, '0'))));
  const all = action('', `function-button all-functions ${selectedFunction ? '' : 'selected'}`, () => { selectedFunction = ''; remember(); render(); });
  all.dataset.focus = 'function-all'; all.setAttribute('aria-pressed', String(!selectedFunction));
  append(all, el('span', 'function-symbol', '≡'), el('span', 'function-name', 'All functions'), el('span', 'function-findings', String(report.diagnostics.length))); sidebar.append(all);
  report.functions.forEach((f, index) => {
    const count = report.diagnostics.filter(d => d.function === f.name || f.aliases.includes(d.function ?? '')).length;
    const button = action('', `function-button ${selectedFunction === f.name ? 'selected' : ''}`, () => { selectedFunction = f.name; remember(); render(); navigate('function', index); });
    button.dataset.focus = `function-${index}`; button.setAttribute('aria-pressed', String(selectedFunction === f.name));
    append(button, append(el('div', 'function-topline'), el('span', 'function-symbol', 'ƒ'), el('span', 'function-name', f.name), el('span', 'function-findings', String(count))), append(el('div', 'function-coverage'), el('span', '', `${f.analyzed_instructions}/${f.reachable_instructions} analyzed`), el('span', f.complete ? 'health-dot' : 'health-dot incomplete', f.complete ? 'Complete' : 'Incomplete')), progress(f.analyzed_instructions, f.reachable_instructions, `${f.name}: ${f.analyzed_instructions} of ${f.reachable_instructions} analyzed`));
    if (f.boundaries_inferred) { append(button, el('span', 'inferred-label', '◇ Inferred boundaries')); }
    sidebar.append(button);
  });
  append(sidebar, append(el('div', 'scope-note'), el('span', 'eyebrow', 'KNOW THE BOUNDARY'), el('p', '', 'Coverage describes what was analyzed. It does not certify the entire ABI.')));
  layout.append(sidebar);

  const findings = el('div', 'findings');
  const visible = report.diagnostics.map((d, index) => ({d, index})).filter(({d}) => (!selectedFunction || d.function === selectedFunction || report.functions.find(f => f.name === selectedFunction)?.aliases.includes(d.function ?? '')) && (severity === 'all' || d.category === severity));
  append(findings, append(el('div', 'section-heading findings-heading'), append(el('div', 'heading-label'), el('h2', '', selectedFunction || 'Findings'), el('span', 'section-count', String(visible.length).padStart(2, '0'))), el('span', 'sort-label', 'SOURCE ORDER ↓')));
  const filters = el('div', 'filters'); filters.setAttribute('role', 'group'); filters.setAttribute('aria-label', 'Severity filters');
  for (const [value, label] of [['all', 'All findings'], ['error', 'Errors'], ['warning', 'Warnings'], ['analysis_gap', 'Analysis gaps']]) {
    const filter = action(label, `filter-chip ${severity === value ? 'active' : ''}`, () => { severity = value; remember(); render(); });
    filter.dataset.focus = `filter-${value}`; filter.setAttribute('aria-pressed', String(severity === value)); filters.append(filter);
  }
  findings.append(filters);
  const selected = report.functions.find(f => f.name === selectedFunction);
  if (selected) {
    const detail = el('div', 'selected-function-detail');
    append(detail, el('span', 'eyebrow', 'FUNCTION COVERAGE'), el('p', '', `${selected.analyzed_instructions} / ${selected.reachable_instructions} reachable instructions analyzed · ${selected.total_instructions} total`));
    if (selected.aliases.length) { append(detail, el('p', 'muted', `Labels: ${selected.aliases.join(', ')}`)); }
    if (!selected.complete) { append(detail, el('p', 'incomplete-reason', selected.incomplete_checks.map(rule => snapshot.titles?.[rule] ?? rule).join(' · ') || 'See the analysis gaps below.')); }
    findings.append(detail);
  }
  if (visible.length) { visible.forEach(({d, index}) => findings.append(findingCard(d, index))); }
  else {
    const clean = !report.diagnostics.length && report.complete;
    const empty = el('div', clean ? 'findings-empty clean' : 'findings-empty');
    append(empty, el('span', 'empty-mark', clean ? '✓' : '○'), el('h3', '', clean ? 'No issues found in the supported checks.' : report.complete && report.diagnostics.length ? 'No findings match these filters.' : 'There is more to review.'), el('p', '', clean ? 'The analyzed functions satisfy the supported calling-convention checks.' : report.diagnostics.length ? 'Choose another function or severity to inspect the remaining findings.' : 'Analysis is incomplete. Review the function coverage and supported-source limits.'));
    findings.append(empty);
  }
  layout.append(findings); section.append(layout);
  append(section, append(el('div', 'report-note'), el('span', 'note-icon', 'i'), el('p', '', 'A source-level check of ordinary System V AMD64 functions. Submitted assembly is never assembled or executed.')));
  return section;
}

function render() {
  const focus = (document.activeElement as HTMLElement | null)?.dataset.focus;
  content.replaceChildren((snapshot.state === 'ready' || snapshot.state === 'loading') && snapshot.report ? readyView() : stateView());
  if (snapshot.state === 'loading') {
    content.querySelectorAll<HTMLButtonElement>('.finding-title, .location-button, button.related-link').forEach(button => button.disabled = true);
    content.setAttribute('aria-busy', 'true');
  } else { content.removeAttribute('aria-busy'); }
  if (focus) { Array.from(content.querySelectorAll<HTMLElement>('[data-focus]')).find(node => node.dataset.focus === focus)?.focus(); }
  const blocked = snapshot.state === 'untrusted' || snapshot.state === 'disabled';
  (document.getElementById('refresh') as HTMLButtonElement).disabled = blocked || snapshot.state === 'loading';
  (document.getElementById('python') as HTMLButtonElement).disabled = snapshot.state === 'untrusted';
  document.getElementById('announcement')!.textContent = snapshot.state === 'ready' ? `Analysis ${snapshot.report?.complete ? 'complete' : 'incomplete'}. ${snapshot.report?.diagnostics.length} findings.` : snapshot.state === 'loading' ? 'Analyzing assembly.' : snapshot.error ?? '';
}
document.getElementById('refresh')!.addEventListener('click', () => post('analyze'));
document.getElementById('python')!.addEventListener('click', () => post('python'));
document.getElementById('output')!.addEventListener('click', () => post('output'));
window.addEventListener('message', event => {
  if (!event.data || typeof event.data.state !== 'string') { return; }
  snapshot = event.data;
  if (snapshot.uri && snapshot.uri !== currentUri) { selectedFunction = ''; severity = 'all'; currentUri = snapshot.uri; remember(); }
  render();
});
render();
post('ready');
