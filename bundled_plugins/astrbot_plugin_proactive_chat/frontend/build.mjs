import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { build, transform } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../pages/proactive-chat');
const files = [
    'utils/auth.js', 'utils/storage.js', 'utils/http.js', 'utils/formatters.js',
    'utils/markdown.js', 'context/AppContext.jsx', 'hooks/useApi.js',
    'hooks/useWebSocket.js', 'components/layout/Sidebar.jsx',
    'components/layout/Header.jsx', 'views/StatusView.jsx',
    'components/config/ConfigRenderer.jsx', 'views/ConfigView.jsx',
    'views/TasksView.jsx', 'views/NotificationsView.jsx',
    'views/MarkdownDocsView.jsx', 'app.jsx',
];
const source = (await Promise.all(files.map(file =>
    fs.readFile(path.join(root, 'js', file), 'utf8')))).join('\n;\n');
const names = new Set(['ThemeProvider', 'createTheme']);
for (const match of source.matchAll(/\{([^{}]+)\}\s*=\s*MaterialUI/g)) {
    for (const name of match[1].split(',').map(value => value.trim()).filter(Boolean)) {
        if (!/^[a-zA-Z][a-zA-Z0-9]*$/.test(name)) throw new Error('Unexpected MUI import');
        names.add(name);
    }
}
const imports = [...names].sort().join(', ');
await build({
    absWorkingDir: here,
    stdin: {
        contents: `import React from 'react';
import * as ReactDOM from 'react-dom/client';
import { ${imports} } from '@mui/material';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
Object.assign(window, { React, ReactDOM, MaterialUI: { ${imports} }, marked, DOMPurify });`,
        resolveDir: here,
    },
    bundle: true, minify: true, format: 'iife', target: 'es2020',
    define: { 'process.env.NODE_ENV': '"production"' },
    legalComments: 'eof',
    outfile: path.join(root, 'js/vendor.bundle.js'),
});
const application = await transform(source, {
    loader: 'jsx', target: 'es2020', minifyWhitespace: true,
    minifySyntax: true, minifyIdentifiers: false, legalComments: 'eof',
});
await fs.writeFile(path.join(root, 'js/app.bundle.js'), application.code);
for (const file of ['vendor.bundle.js', 'app.bundle.js']) {
    const data = await fs.readFile(path.join(root, 'js', file));
    console.log(`${file}: ${data.length} bytes, gzip ${gzipSync(data).length} bytes`);
}
