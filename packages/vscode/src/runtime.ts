import {spawn} from 'node:child_process';
import * as path from 'node:path';
import {AnalysisReport, parseReport} from './model';

export class Cancelled extends Error { constructor() { super('Analysis cancelled'); } }
interface ProcessResult {stdout: string; stderr: string; code: number | null}
export interface Interpreter {command: string; args: string[]; label: string}

export function runProcess(command: string, args: string[], input = '', signal?: AbortSignal, timeout = 10_000): Promise<ProcessResult> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Cancelled()); return; }
    let settled = false;
    let stdout = '', stderr = '', bytes = 0;
    const child = spawn(command, args, {shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe']});
    const finish = (error?: Error, result?: ProcessResult) => {
      if (settled) { return; }
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      if (error) { child.kill(); reject(error); } else { resolve(result!); }
    };
    const abort = () => finish(new Cancelled());
    const timer = setTimeout(() => finish(new Error('Analysis exceeded 10 seconds. Try a smaller file or fewer entry labels.')), timeout);
    signal?.addEventListener('abort', abort, {once: true});
    const read = (buffer: Buffer, stream: 'stdout' | 'stderr') => {
      bytes += buffer.length;
      if (bytes > 8 * 1024 * 1024) { finish(new Error('The checker report exceeded the 8 MB output limit.')); return; }
      if (stream === 'stdout') { stdout += buffer.toString('utf8'); } else { stderr += buffer.toString('utf8'); }
    };
    // Decode split UTF-8 characters correctly instead of decoding individual pipe chunks.
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => read(Buffer.from(chunk), 'stdout'));
    child.stderr.on('data', (chunk: string) => read(Buffer.from(chunk), 'stderr'));
    child.on('error', error => finish(error));
    child.stdin.on('error', error => { if ((error as NodeJS.ErrnoException).code !== 'EPIPE') { finish(error); } });
    child.on('close', code => finish(undefined, {stdout, stderr, code}));
    child.stdin.end(input, 'utf8');
    if (signal?.aborted) { abort(); }
  });
}

export class CheckerRuntime {
  private interpreters = new Map<string, Promise<Interpreter>>();
  constructor(private bundlePath: string, private log: (message: string) => void) {}
  reset() { this.interpreters.clear(); }
  async discover(configured: string): Promise<Interpreter> {
    const previous = this.interpreters.get(configured);
    if (previous) { return previous; }
    const pending = this.find(configured);
    this.interpreters.set(configured, pending);
    try { return await pending; } catch (error) { this.interpreters.delete(configured); throw error; }
  }
  private async find(configured: string): Promise<Interpreter> {
    const candidates = configured ? [{command: configured, args: []}] : process.platform === 'win32'
      ? [{command: 'py', args: ['-3']}, {command: 'python', args: []}, {command: 'python3', args: []}]
      : [{command: 'python3', args: []}, {command: 'python', args: []}];
    for (const candidate of candidates) {
      try {
        const result = await runProcess(candidate.command, [...candidate.args, '-I', '-S', '-X', 'utf8', '-c', 'import json,sys; print(json.dumps(list(sys.version_info[:3])))'], '', undefined, 3_000);
        const version = JSON.parse(result.stdout);
        if (result.code === 0 && Array.isArray(version) && version[0] === 3 && version[1] >= 11) {
          return {...candidate, label: `Python ${version.join('.')} · ${candidate.command}`};
        }
        this.log(`Python candidate ${candidate.command}: Python 3.11+ required.`);
      } catch (error) { this.log(`Python candidate ${candidate.command}: ${error instanceof Error ? error.message : String(error)}`); }
    }
    throw new Error(configured ? 'The selected executable could not run Python 3.11+. Choose another Python interpreter.' : 'Python 3.11+ was not found. Install Python on this extension host, or select its executable path.');
  }
  async analyze(source: string, entries: string[], pythonPath: string, signal: AbortSignal, filename?: string, buffers: Record<string, string> = {}): Promise<{report: AnalysisReport; interpreter: string; sources: Record<string, string>}> {
    const interpreter = await this.discover(pythonPath);
    if (signal.aborted) { throw new Cancelled(); }
    const result = await runProcess(interpreter.command, [...interpreter.args, '-I', '-S', '-B', '-X', 'utf8', path.join(this.bundlePath, 'runner.py'), '-', '--format', 'json', '--buffer-json', ...(filename ? ['--stdin-filename', filename] : []), ...entries.flatMap(entry => ['--entry', entry])], JSON.stringify({source, buffers}), signal);
    if (result.stderr.trim()) { this.log(result.stderr.trim()); }
    if (![0, 1, 2].includes(result.code ?? -1)) { throw new Error('The checker process failed. Open Output for details.'); }
    try {
      const sources = JSON.parse(result.stdout).sources;
      if (!sources || typeof sources !== 'object' || Array.isArray(sources) || !Object.values(sources).every(value => typeof value === 'string')) { throw new Error('Invalid included source buffers'); }
      return {report: parseReport(result.stdout), interpreter: interpreter.label, sources};
    }
    catch (error) { this.log(`Invalid checker output (exit ${result.code}). ${result.stderr}`); throw error; }
  }
}
