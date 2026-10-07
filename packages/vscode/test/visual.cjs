const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const {reportHtml} = require('../out/host/html');
const {CheckerRuntime} = require('../out/host/runtime');
const {ruleTitles} = require('../out/host/model');
const root=path.resolve(__dirname,'../../..');
const artifacts=path.join(root,'artifacts','visual');
fs.mkdirSync(artifacts,{recursive:true});
const runtime=new CheckerRuntime(path.resolve(__dirname,'../python'),console.log);
const python=process.env.ACC_TEST_PYTHON || (process.platform==='win32'?'python':'python3');
const themeCss={
  dark: ':root{--vscode-editor-background:#10151e;--vscode-editor-foreground:#d9e0eb;--vscode-sideBar-background:#171e29;--vscode-panel-border:#2c3544;--vscode-descriptionForeground:#9aa6b8;--vscode-textLink-foreground:#87baf1;--vscode-editorError-foreground:#f28b91;--vscode-editorWarning-foreground:#e7bc77;--vscode-editorInfo-foreground:#86b9dc;--vscode-testing-iconPassed:#8bc5a3;--vscode-focusBorder:#87baf1;}',
  light: ':root{--vscode-editor-background:#ffffff;--vscode-editor-foreground:#252d3a;--vscode-sideBar-background:#f5f7fa;--vscode-panel-border:#dde2e9;--vscode-descriptionForeground:#616d7e;--vscode-textLink-foreground:#1764a6;--vscode-editorError-foreground:#b42a3b;--vscode-editorWarning-foreground:#85601d;--vscode-editorInfo-foreground:#276c8f;--vscode-testing-iconPassed:#247145;--vscode-focusBorder:#1764a6;--vscode-symbolIcon-variableForeground:#376789;--vscode-symbolIcon-keywordForeground:#7357a0;--vscode-symbolIcon-numberForeground:#427537;}',
  contrast: ':root{--vscode-editor-background:#000000;--vscode-editor-foreground:#ffffff;--vscode-sideBar-background:#000000;--vscode-panel-border:#6fc3df;--vscode-contrastBorder:#6fc3df;--vscode-descriptionForeground:#e5e5e5;--vscode-textLink-foreground:#75beff;--vscode-editorError-foreground:#ff9999;--vscode-editorWarning-foreground:#ffdc00;--vscode-editorInfo-foreground:#75beff;--vscode-testing-iconPassed:#b5f5b0;--vscode-focusBorder:#f38518;}',
};
const bridge="window.messages=[];window.persisted=undefined;window.acquireVsCodeApi=()=>({postMessage:m=>window.messages.push(m),getState:()=>window.persisted,setState:s=>window.persisted=s});";
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/'){
    const theme=url.searchParams.get('theme')||'dark';
    const origin=`http://127.0.0.1:${server.address().port}`;
    let html=reportHtml('/report.css','/report.js','visual-test',origin);
    html=html.replace('<body>',`<body class="${theme==='light'?'vscode-light':theme==='contrast'?'vscode-high-contrast':'vscode-dark'}">`).replace('</head>',`<link rel="stylesheet" href="/theme.css?theme=${theme}"></head>`).replace('<script nonce="visual-test" src="/report.js">','<script nonce="visual-test" src="/bridge.js"></script><script nonce="visual-test" src="/report.js">');
    res.setHeader('Content-Type','text/html');res.end(html);
  }else if(url.pathname==='/bridge.js'){res.setHeader('Content-Type','text/javascript');res.end(bridge);}
  else if(url.pathname==='/theme.css'){res.setHeader('Content-Type','text/css');res.end(themeCss[url.searchParams.get('theme')]||themeCss.dark);}
  else if(['/report.css','/report.js'].includes(url.pathname)){res.setHeader('Content-Type',url.pathname.endsWith('.css')?'text/css':'text/javascript');res.end(fs.readFileSync(path.resolve(__dirname,'../out/webview',url.pathname.slice(1))));}
  else{res.writeHead(404);res.end();}
});
async function snapshot(source,filename){
  const result=await runtime.analyze(source,[],python,new AbortController().signal);
  return {state:'ready',uri:`file:///workspace/${filename}`,filename:`/workspace/assembly/${filename}`,version:1,source,report:result.report,duration:42,interpreter:'Python 3.11 · bundled checker',titles:ruleTitles};
}
async function run(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,...(process.env.ACC_CHROMIUM_PATH?{executablePath:process.env.ACC_CHROMIUM_PATH}:{})});
  const failures=[];
  try{
    const source=fs.readFileSync(path.join(root,'packages/checker/examples/broken.s'),'utf8');
    const broken=await snapshot(source,'broken.s');
    const clean=await snapshot('.globl add_numbers\n.type add_numbers,@function\nadd_numbers:\n    leaq (%rdi,%rsi),%rax\n    ret\n.size add_numbers,.-add_numbers\n','add_numbers.s');
    const incomplete=await snapshot('.globl review\nreview: syscall\n','review.s');
    const page=await browser.newPage({viewport:{width:1180,height:950},reducedMotion:'reduce'});
    page.on('pageerror',error=>failures.push(error.message));
    const show=async(theme,data,width=1180)=>{
      await page.setViewportSize({width,height:950});
      await page.goto(`http://127.0.0.1:${server.address().port}/?theme=${theme}`);
      await page.waitForFunction(()=>window.messages?.some(m=>m.type==='ready'));
      await page.evaluate(data=>window.postMessage(data,'*'),data);
      await page.waitForFunction(state=>document.querySelector('#content')?.textContent && (state==='ready'?!!document.querySelector('.report'):!!document.querySelector('.state-view')),data.state);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No page-level horizontal overflow');
    };
    for(const theme of ['dark','light','contrast']){
      for(const width of [1180,520]){
        await show(theme,broken,width);
        await page.screenshot({path:path.join(artifacts,`report-${theme}-${width}.png`),fullPage:true});
      }
    }
    const includeRoot=path.join(root,'artifacts','visual','main.s');
    const includeChild=path.join(path.dirname(includeRoot),'helper.s');
    const includeSource='.include "helper.s"\n.globl f\nf: subq $8,%rsp; call helper; addq $8,%rsp; ret\n';
    const includeResult=await runtime.analyze(includeSource,[],python,new AbortController().signal,includeRoot,{[includeChild]:'helper:\n    std\n    ret\n'});
    const included={...broken,source:includeSource,filename:includeRoot,report:includeResult.report,sources:includeResult.sources};
    await show('dark',included,520);
    assert.ok((await page.locator('.source-excerpt').innerText()).includes('std'),'Included excerpt uses the included source');
    assert.ok((await page.locator('.location-button').innerText()).includes('helper.s'),'Included location identifies its file');
    await page.locator('.location-button').click();
    assert.ok(await page.evaluate(()=>window.messages.some(m=>m.type==='navigate' && m.kind==='finding')),'Included finding requests navigation');
    await page.screenshot({path:path.join(artifacts,'report-included-source.png'),fullPage:true});
    await show('dark',broken);
    await page.evaluate(data=>window.postMessage({...data,state:'loading'},'*'),broken);
    await page.waitForFunction(()=>document.querySelector('.hero-topline')?.textContent.includes('Updating'));
    assert.equal(await page.locator('.finding-card').count(),broken.report.diagnostics.length,'Live refresh preserves report layout');
    assert.equal(await page.locator('.finding-title').first().isDisabled(),true,'Stale navigation is disabled');
    await page.evaluate(data=>window.postMessage(data,'*'),broken);
    await page.waitForFunction(()=>!document.querySelector('.finding-title')?.disabled);
    await page.getByRole('button',{name:'Warnings',exact:true}).click();
    assert.equal(await page.locator('.finding-card').count(),0);
    await page.getByRole('button',{name:'All findings',exact:true}).click();
    assert.equal(await page.locator('.finding-card').count(),broken.report.diagnostics.length);
    await page.locator('.finding-title').first().focus(); await page.keyboard.press('Enter');
    assert.ok(await page.evaluate(()=>window.messages.some(m=>m.type==='navigate'&&m.kind==='finding')),'Keyboard navigation emits source action');
    await page.locator('.function-button').nth(1).click();
    assert.ok(await page.locator('.selected-function-detail').count());
    await page.getByRole('button',{name:'Analysis gaps',exact:true}).focus();
    await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab');
    assert.notEqual(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle),'none','Visible keyboard focus');
    await show('light',clean);await page.screenshot({path:path.join(artifacts,'report-clean.png'),fullPage:true});
    assert.match(await page.locator('.findings-empty').innerText(),/No issues found in the supported checks/);
    await show('dark',incomplete);await page.screenshot({path:path.join(artifacts,'report-incomplete.png'),fullPage:true});
    assert.match(await page.locator('.coverage-heading').innerText(),/Incomplete/);
    for(const state of ['empty','loading','error','disabled','untrusted']){
      await show('dark',{state,error:state==='error'?'Python 3.11+ was not found. Install Python or select its executable.':undefined});
      await page.screenshot({path:path.join(artifacts,`state-${state}.png`),fullPage:true});
      if(state==='loading')assert.equal(await page.locator('.state-symbol').evaluate(node=>getComputedStyle(node).animationName),'none');
      if(state==='untrusted')assert.equal(await page.locator('#refresh').isDisabled(),true);
    }
    const long=structuredClone(broken);long.report.diagnostics[0].message='A long explanation with register names and source details. '.repeat(30);long.report.diagnostics[0].suggestion='<script>window.injected=true</script>';long.filename='/workspace/'+'long_path_segment/'.repeat(15)+'long_name.s';
    await show('light',long,520);
    assert.equal(await page.evaluate(()=>window.injected),undefined,'Source and findings are escaped');
    await page.screenshot({path:path.join(artifacts,'report-long-finding.png'),fullPage:true});
    assert.deepEqual(failures,[],'No renderer errors');
    fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify({passed:true,themes:['dark','light','high contrast'],widths:[1180,520],checks:['layout overflow','severity filters','function selection','keyboard navigation','focus visibility','clean and incomplete wording','all setup states','reduced motion','escaped findings','renderer errors']},null,2));
    console.log(`Visual checks passed. Screenshots: ${artifacts}`);
  }finally{await browser.close();server.close();}
}
run().catch(error=>{console.error(error);server.close();process.exitCode=1;});
