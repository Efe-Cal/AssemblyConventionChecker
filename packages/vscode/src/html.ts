export const reportMark = `<svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M10 5 3 16l7 11M22 5l7 11-7 11" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="m11 16 4 4 7-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export function reportHtml(css: string, script: string, nonce: string, resourceOrigin: string): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${resourceOrigin}; script-src 'nonce-${nonce}'; img-src ${resourceOrigin}; font-src ${resourceOrigin};">
<title>Assembly Convention Checker</title><link rel="stylesheet" href="${css}"></head>
<body>
<header class="masthead"><div class="brand"><span class="brand-mark">${reportMark}</span><div><div class="brand-name">CONVENTION<span class="brand-slash"> / </span>CHECKER</div><div class="brand-meta">SYSTEM V AMD64<span class="meta-dot">·</span>SOURCE ANALYSIS</div></div></div><div class="toolbar"><button id="python" class="tool-button" title="Select Python interpreter">Python<span aria-hidden="true">↗</span></button><button id="refresh" class="tool-button primary" title="Analyze current file"><span aria-hidden="true">↻</span> Analyze</button></div></header>
<main id="content" aria-label="Assembly analysis report"></main>
<footer class="footer"><span class="footer-mark" aria-hidden="true">⌁</span><span>GNU AT&amp;T · Linux / ELF · x86-64</span><button id="output" class="footer-link">Output ↗</button></footer>
<div id="announcement" class="sr-only" role="status" aria-live="polite"></div>
<script nonce="${nonce}" src="${script}"></script>
</body></html>`;
}
