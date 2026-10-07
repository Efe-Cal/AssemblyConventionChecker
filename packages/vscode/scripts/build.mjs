import {cpSync, mkdirSync, readdirSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const checker = path.resolve(root, '../checker/assembly_convention_checker');
const bundle = path.join(root, 'python/assembly_convention_checker');
mkdirSync(bundle, {recursive: true});
for (const name of readdirSync(checker).filter(name => name.endsWith('.py'))) {
  cpSync(path.join(checker, name), path.join(bundle, name));
}
mkdirSync(path.join(root, 'out/webview'), {recursive: true});
mkdirSync(path.resolve(root, '../../dist'), {recursive: true});
cpSync(path.join(root, 'webview/report.css'), path.join(root, 'out/webview/report.css'));
const compiler = path.resolve(root, '../../node_modules/typescript/bin/tsc');
const watch = process.argv.includes('--watch');
const run = project => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [compiler, '-p', project, ...(watch ? ['--watch', '--preserveWatchOutput'] : [])], {cwd: root, stdio: 'inherit'});
  child.on('error', reject);
  child.on('exit', code => code === 0 ? resolve() : reject(new Error(`TypeScript exited ${code}`)));
});
await Promise.all([run('tsconfig.json'), run('webview/tsconfig.json')]);
