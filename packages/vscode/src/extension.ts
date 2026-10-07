import * as vscode from 'vscode';
import {randomBytes} from 'node:crypto';
import {RequestLedger, Snapshot, SourceLocation, ruleTitles, sourceRange} from './model';
import {Cancelled, CheckerRuntime} from './runtime';
import {reportHtml} from './html';

const prefix = 'assemblyConventionChecker';
const assemblyLanguages = new Set(['gas', 'asm', 'assembly', 'x86', 'x86asm', 'nasm']);
function eligible(document: vscode.TextDocument) {
  return !document.isClosed && (document.uri.scheme === 'file' || document.uri.scheme === 'untitled' || document.uri.scheme === 'vscode-remote') && (/\.s$/i.test(document.uri.path) || (document.isUntitled && assemblyLanguages.has(document.languageId)));
}
function includedSource(sources: Record<string, string> | undefined, filename: string) {
  const key = process.platform === 'win32' ? Object.keys(sources ?? {}).find(name => name.toLowerCase() === filename.toLowerCase()) : filename;
  return key === undefined ? undefined : sources?.[key];
}

export class ExtensionController implements vscode.Disposable {
  private diagnostics = vscode.languages.createDiagnosticCollection('Assembly Convention Checker');
  private output = vscode.window.createOutputChannel('Assembly Convention Checker');
  private status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 30);
  private runtime: CheckerRuntime;
  private ledger = new RequestLedger();
  private pending = new Map<string, {abort: AbortController; timer?: NodeJS.Timeout}>();
  private snapshots = new Map<string, Snapshot>();
  private selected?: vscode.TextDocument;
  private sourceColumn = vscode.ViewColumn.One;
  private panel?: vscode.WebviewPanel;
  private subscriptions: vscode.Disposable[] = [];

  constructor(private context: vscode.ExtensionContext) {
    this.runtime = new CheckerRuntime(vscode.Uri.joinPath(context.extensionUri, 'python').fsPath, message => this.output.appendLine(message));
    this.status.command = `${prefix}.openReport`;
    this.status.name = 'Assembly Convention Checker';
    const register = (name: string, callback: (...args: any[]) => any) => this.subscriptions.push(vscode.commands.registerCommand(`${prefix}.${name}`, callback));
    register('analyze', () => this.analyzeCurrent());
    register('openReport', () => this.openReport());
    register('selectPython', () => this.selectPython());
    register('showOutput', () => this.output.show(true));
    this.subscriptions.push(
      vscode.workspace.onDidOpenTextDocument(doc => { if (eligible(doc)) { this.auto(doc, 'open'); } }),
      vscode.workspace.onDidSaveTextDocument(doc => { if (eligible(doc) || this.snapshots.has(doc.uri.toString())) { this.auto(doc, 'save'); } }),
      vscode.workspace.onDidChangeTextDocument(event => {
        const doc = event.document;
        if (event.contentChanges.length) { this.invalidateDependents(doc.uri); }
        if (!event.contentChanges.length || (!eligible(doc) && !this.snapshots.has(doc.uri.toString()))) { return; }
        this.cancel(doc.uri.toString()); this.diagnostics.delete(doc.uri);
        if (this.config(doc).runOn === 'change') { this.schedule(doc, 400); }
        else { this.set(doc, {state: 'empty', error: 'The source has changed. Analyze to refresh this report.'}); }
      }),
      vscode.workspace.onDidCloseTextDocument(doc => {
        const uri = doc.uri.toString(); this.cancel(uri); this.snapshots.delete(uri); this.diagnostics.delete(doc.uri);
        this.invalidateDependents(doc.uri); this.refreshDiagnostics();
        if (this.selected === doc) { this.selected = undefined; this.updateUi(); }
      }),
      vscode.window.onDidChangeActiveTextEditor(editor => {
        if (!editor) { return; } // Keep the source selected while the report has focus.
        if (eligible(editor.document) || this.snapshots.has(editor.document.uri.toString())) {
          this.selected = editor.document;
          this.sourceColumn = editor.viewColumn ?? this.sourceColumn;
          if (!this.snapshots.has(editor.document.uri.toString())) { this.auto(editor.document, 'open'); }
        } else { this.selected = undefined; }
        this.updateUi();
      }),
      vscode.workspace.onDidChangeConfiguration(event => {
        if (!event.affectsConfiguration(prefix)) { return; }
        this.runtime.reset();
        for (const doc of vscode.workspace.textDocuments) {
          if (eligible(doc) || this.snapshots.has(doc.uri.toString())) {
            this.cancel(doc.uri.toString()); this.diagnostics.delete(doc.uri); this.snapshots.delete(doc.uri.toString());
            this.auto(doc, 'open');
          }
        }
        this.updateUi();
      }),
      vscode.workspace.onDidGrantWorkspaceTrust(() => {
        for (const doc of vscode.workspace.textDocuments) { if (eligible(doc)) { this.auto(doc, 'open'); } }
        this.updateUi();
      }),
    );
    const watcher = vscode.workspace.createFileSystemWatcher('**/*.{s,S}');
    this.subscriptions.push(watcher, watcher.onDidChange(uri => this.invalidateDependents(uri)),
      watcher.onDidCreate(uri => this.invalidateDependents(uri)), watcher.onDidDelete(uri => this.invalidateDependents(uri)));
    this.selected = vscode.window.activeTextEditor?.document;
    this.sourceColumn = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
    if (this.selected && !eligible(this.selected)) { this.selected = undefined; }
    for (const doc of vscode.workspace.textDocuments) { if (eligible(doc)) { this.auto(doc, 'open'); } }
    this.updateUi();
  }

  private config(doc: vscode.TextDocument) {
    const config = vscode.workspace.getConfiguration(prefix, doc.uri);
    return {enabled: config.get<boolean>('enabled', true), runOn: config.get<string>('runOn', 'change'), pythonPath: config.get<string>('pythonPath', ''), entries: config.get<string[]>('entries', [])};
  }
  private gate(doc: vscode.TextDocument): Snapshot | undefined {
    if (!vscode.workspace.isTrusted) { return {state: 'untrusted'}; }
    if (!this.config(doc).enabled) { return {state: 'disabled'}; }
  }
  private auto(doc: vscode.TextDocument, event: 'open' | 'save') {
    const gate = this.gate(doc);
    if (gate) { this.set(doc, gate); return; }
    if (this.config(doc).runOn !== 'manual') { this.schedule(doc, event === 'save' ? 0 : 100); }
    else { this.set(doc, {state: 'empty', error: 'Run analysis to inspect this file.'}); }
  }
  private cancel(uri: string) {
    const pending = this.pending.get(uri);
    if (pending) { clearTimeout(pending.timer); pending.abort.abort(); this.pending.delete(uri); }
    this.ledger.invalidate(uri);
  }
  private set(doc: vscode.TextDocument, snapshot: Snapshot) {
    this.snapshots.set(doc.uri.toString(), {...snapshot, uri: doc.uri.toString(), filename: doc.uri.scheme === 'untitled' ? doc.uri.path : doc.uri.fsPath, version: doc.version});
    this.refreshDiagnostics();
    this.updateUi();
  }
  private invalidateDependents(changed: vscode.Uri) {
    for (const [uri, snapshot] of [...this.snapshots]) {
      if (uri === changed.toString() || includedSource(snapshot.sources, changed.fsPath) === undefined) { continue; }
      const doc = vscode.workspace.textDocuments.find(document => document.uri.toString() === uri);
      if (!doc) { continue; }
      this.cancel(uri);
      if (this.config(doc).runOn !== 'manual') { void this.schedule(doc, 400); }
      else { this.set(doc, {state: 'empty', error: 'An included source has changed. Analyze to refresh this report.'}); }
    }
  }
  private schedule(doc: vscode.TextDocument, delay = 0): Promise<void> {
    const uri = doc.uri.toString();
    this.cancel(uri);
    this.diagnostics.delete(doc.uri);
    const gate = this.gate(doc);
    if (gate) { this.set(doc, gate); return Promise.resolve(); }
    const version = doc.version;
    const generation = this.ledger.begin(uri, version);
    const abort = new AbortController();
    // Keep the last report's layout stable while clearly marking it as updating.
    const previous = this.snapshots.get(uri);
    this.set(doc, {state: 'loading', source: previous?.source, sources: previous?.sources, report: previous?.report, duration: previous?.duration, interpreter: previous?.interpreter});
    return new Promise(resolve => {
      const timer = setTimeout(async () => {
        const source = doc.getText();
        const started = Date.now();
        const current = () => !doc.isClosed && doc.version === version && this.ledger.accepts(uri, generation, version) && !abort.signal.aborted;
        try {
          const config = this.config(doc);
          const buffers = Object.fromEntries(vscode.workspace.textDocuments.filter(document => !document.isUntitled && !document.isClosed).map(document => [document.uri.fsPath, document.getText()]));
          const result = await this.runtime.analyze(source, config.entries, config.pythonPath, abort.signal, doc.isUntitled ? undefined : doc.uri.fsPath, buffers);
          if (!current()) { return; }
          if (vscode.workspace.textDocuments.some(document => {
            const included = includedSource(result.sources, document.uri.fsPath);
            return included !== undefined && document.getText() !== included;
          })) {
            void this.schedule(doc, 100); return;
          }
          this.set(doc, {state: 'ready', source, sources: result.sources, report: result.report, duration: Date.now() - started, interpreter: result.interpreter});
          this.output.appendLine(`${doc.uri.toString()}: ${result.report.diagnostics.length} findings, ${result.report.complete ? 'complete' : 'incomplete'} analysis (${Date.now() - started} ms).`);
        } catch (error) {
          if (!(error instanceof Cancelled) && current()) {
            const message = error instanceof Error ? error.message : String(error);
            this.output.appendLine(message); this.diagnostics.delete(doc.uri);
            this.set(doc, {state: 'error', error: message});
          }
        } finally {
          if (this.ledger.accepts(uri, generation, version)) { this.pending.delete(uri); }
          resolve();
        }
      }, delay);
      // Resolve a caller awaiting a request that was superseded before its timer ran.
      abort.signal.addEventListener('abort', () => resolve(), {once: true});
      this.pending.set(uri, {abort, timer});
    });
  }
  private range(source: string, location: SourceLocation): vscode.Range {
    const value = sourceRange(source, location);
    return new vscode.Range(value.line, value.start, value.line, value.end);
  }
  private refreshDiagnostics() {
    const severity = {error: vscode.DiagnosticSeverity.Error, warning: vscode.DiagnosticSeverity.Warning, analysis_gap: vscode.DiagnosticSeverity.Information};
    const grouped = new Map<string, {uri: vscode.Uri; findings: Map<string, vscode.Diagnostic>}>();
    for (const snapshot of this.snapshots.values()) {
      if (snapshot.state !== 'ready' || !snapshot.report || !snapshot.uri || snapshot.source === undefined) { continue; }
      const root = vscode.Uri.parse(snapshot.uri);
      const locate = (location: SourceLocation) => {
        const source = location.filename === snapshot.report!.filename ? snapshot.source : snapshot.sources?.[location.filename];
        if (source === undefined) { return; }
        const uri = location.filename === snapshot.report!.filename ? root : root.with({path: vscode.Uri.file(location.filename).path});
        return new vscode.Location(uri, this.range(source, location));
      };
      for (const finding of snapshot.report.diagnostics) {
        const location = locate(finding.location);
        if (!location) { continue; }
        const diagnostic = new vscode.Diagnostic(location.range, `${finding.message}${finding.suggestion ? '\nSuggestion: ' + finding.suggestion : ''}`, severity[finding.category]);
        diagnostic.code = finding.rule_id; diagnostic.source = 'Assembly Convention Checker';
        diagnostic.relatedInformation = finding.related_locations.flatMap(loc => {
          const related = locate(loc);
          return related ? [new vscode.DiagnosticRelatedInformation(related, 'Related operation')] : [];
        });
        const key = location.uri.toString();
        if (!grouped.has(key)) { grouped.set(key, {uri: location.uri, findings: new Map()}); }
        grouped.get(key)!.findings.set(JSON.stringify([finding.location, finding.rule_id, finding.category, finding.message]), diagnostic);
      }
    }
    this.diagnostics.clear();
    for (const {uri, findings} of grouped.values()) { this.diagnostics.set(uri, [...findings.values()]); }
  }
  async analyzeCurrent() {
    const active = vscode.window.activeTextEditor;
    const doc = active?.document ?? this.selected;
    if (!doc) { this.openReport(); return; }
    this.selected = doc;
    if (active) { this.sourceColumn = active.viewColumn ?? this.sourceColumn; }
    await this.schedule(doc);
  }
  async selectPython() {
    if (!vscode.workspace.isTrusted) { this.updateUi(); return; }
    const uri = this.selected?.uri;
    const config = vscode.workspace.getConfiguration(prefix, uri);
    const value = await vscode.window.showInputBox({title: 'Select Python 3.11+ Interpreter', prompt: 'Executable path on the extension host. Leave empty for automatic discovery.', value: config.get<string>('pythonPath', ''), placeHolder: process.platform === 'win32' ? 'C:\\Python311\\python.exe' : '/usr/bin/python3', ignoreFocusOut: true});
    if (value === undefined) { return; }
    const inspect = config.inspect<string>('pythonPath');
    const target = inspect?.workspaceFolderValue !== undefined ? vscode.ConfigurationTarget.WorkspaceFolder : inspect?.workspaceValue !== undefined ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
    await config.update('pythonPath', value.trim(), target);
  }
  openReport() {
    const active = vscode.window.activeTextEditor;
    const doc = active?.document;
    if (doc && eligible(doc)) { this.selected = doc; this.sourceColumn = active?.viewColumn ?? this.sourceColumn; }
    if (this.panel) { this.panel.reveal(this.panel.viewColumn, true); this.updateUi(); return; }
    const assets = vscode.Uri.joinPath(this.context.extensionUri, 'out', 'webview');
    const panel = vscode.window.createWebviewPanel(`${prefix}.report`, 'Assembly · Convention Report', {viewColumn: vscode.ViewColumn.Beside, preserveFocus: true}, {enableScripts: true, localResourceRoots: [assets]});
    this.panel = panel;
    panel.iconPath = vscode.Uri.joinPath(this.context.extensionUri, 'media', 'mark.svg');
    panel.webview.html = reportHtml(panel.webview.asWebviewUri(vscode.Uri.joinPath(assets, 'report.css')).toString(), panel.webview.asWebviewUri(vscode.Uri.joinPath(assets, 'report.js')).toString(), randomBytes(16).toString('hex'), panel.webview.cspSource);
    panel.onDidDispose(() => { if (this.panel === panel) { this.panel = undefined; } });
    panel.webview.onDidReceiveMessage(message => this.receive(message));
    this.updateUi();
  }
  private async receive(message: unknown) {
    if (!message || typeof message !== 'object') { return; }
    const data = message as Record<string, unknown>;
    if (data.type === 'ready') { this.updateUi(); return; }
    if (data.type === 'analyze') { await this.analyzeCurrent(); return; }
    if (data.type === 'python') { await this.selectPython(); return; }
    if (data.type === 'output') { this.output.show(true); return; }
    if (data.type !== 'navigate' || !this.selected) { return; }
    const snapshot = this.getSnapshot(this.selected.uri.toString());
    if (!snapshot.report || snapshot.state !== 'ready' || data.uri !== snapshot.uri || data.version !== snapshot.version || this.selected.version !== snapshot.version || !Number.isSafeInteger(data.index) || (data.index as number) < 0) { return; }
    let location: SourceLocation | undefined;
    if (data.kind === 'function') { location = snapshot.report.functions[data.index as number]?.location; }
    else if (data.kind === 'finding') { location = snapshot.report.diagnostics[data.index as number]?.location; }
    else if (data.kind === 'related' && Number.isSafeInteger(data.relatedIndex) && (data.relatedIndex as number) >= 0) { location = snapshot.report.diagnostics[data.index as number]?.related_locations[data.relatedIndex as number]; }
    if (!location || snapshot.source === undefined) { return; }
    const source = location.filename === snapshot.report.filename ? snapshot.source : snapshot.sources?.[location.filename];
    if (source === undefined) { return; }
    const target = location.filename === snapshot.report.filename ? this.selected : await vscode.workspace.openTextDocument(this.selected.uri.with({path: vscode.Uri.file(location.filename).path}));
    if (target.getText() !== source) { return; }
    const range = this.range(source, location);
    const editor = await vscode.window.showTextDocument(target, {viewColumn: this.sourceColumn, preserveFocus: true, selection: range});
    editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
  }
  getSnapshot(uri?: string): Snapshot {
    if (!vscode.workspace.isTrusted) { return {state: 'untrusted'}; }
    if (!uri) { return {state: 'empty'}; }
    const doc = vscode.workspace.textDocuments.find(document => document.uri.toString() === uri);
    if (doc) { const gate = this.gate(doc); if (gate) { return {...gate, uri}; } }
    return this.snapshots.get(uri) ?? {state: 'empty'};
  }
  private updateUi() {
    const snapshot = this.getSnapshot(this.selected?.uri.toString());
    if (this.selected) {
      const errors = snapshot.report?.diagnostics.filter(d => d.category === 'error').length ?? 0;
      const warnings = snapshot.report?.diagnostics.filter(d => d.category === 'warning').length ?? 0;
      this.status.text = snapshot.state === 'loading' ? '$(sync~spin) ABI' : snapshot.state === 'ready' ? `${errors ? '$(error)' : snapshot.report?.complete ? '$(check)' : '$(info)'} ABI${errors || warnings ? ` ${errors}E · ${warnings}W` : snapshot.report?.complete ? '' : ' incomplete'}` : '$(circle-slash) ABI';
      this.status.tooltip = snapshot.error ?? (snapshot.state === 'ready' ? `Analysis ${snapshot.report?.complete ? 'complete' : 'incomplete'}. Open the convention report.` : `Assembly analysis: ${snapshot.state}. Open report.`);
      this.status.show();
    } else { this.status.hide(); }
    void this.panel?.webview.postMessage({...snapshot, titles: ruleTitles});
  }
  dispose() {
    for (const uri of this.pending.keys()) { this.cancel(uri); }
    this.panel?.dispose(); this.status.dispose(); this.diagnostics.dispose(); this.output.dispose();
    this.subscriptions.forEach(subscription => subscription.dispose());
  }
}

export function activate(context: vscode.ExtensionContext) {
  const controller = new ExtensionController(context);
  context.subscriptions.push(controller);
  return controller;
}
