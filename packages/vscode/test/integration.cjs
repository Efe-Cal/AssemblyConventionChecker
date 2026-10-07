const path = require('node:path');
const fs = require('node:fs');
const {runTests} = require('@vscode/test-electron');
const root = path.resolve(__dirname,'../../..');
const artifacts = path.join(root,'artifacts','integration');
const workspace = process.env.ACC_TEST_WORKSPACE || path.join(artifacts,'workspace');
fs.mkdirSync(workspace,{recursive:true});
fs.writeFileSync(path.join(workspace,'sample.s'),'.globl sample\nsample: ret\n');
// A hostile cwd must not override stdlib or the bundled analysis engine.
fs.writeFileSync(path.join(workspace,'json.py'),'raise RuntimeError("workspace import must never run")\n');
fs.writeFileSync(path.join(workspace,'assembly_convention_checker.py'),'raise RuntimeError("workspace import must never run")\n');
const extensionPath = process.env.ACC_EXTENSION_PATH || path.resolve(__dirname,'..');
runTests({
  extensionDevelopmentPath: extensionPath,
  extensionTestsPath: path.join(__dirname,'host.cjs'),
  ...(process.env.ACC_VSCODE_EXECUTABLE ? {vscodeExecutablePath: process.env.ACC_VSCODE_EXECUTABLE} : {version: '1.100.3', cachePath: path.join(root,'.vscode-test')}),
  launchArgs: [workspace,'--disable-extensions', '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes', '--user-data-dir',path.join(artifacts,'profile')],
}).catch(error=>{console.error(error);process.exitCode=1;});
