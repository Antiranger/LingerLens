const fs = require('node:fs');
const path = require('node:path');
const asar = require('@electron/asar');

// Build inclusion and post-pack validation share one explicit manifest.
const APP_FILES = Object.freeze(['desktop/main.cjs', 'desktop/backend.cjs', 'desktop/devlog.cjs',
  'desktop/health.cjs', 'desktop/updater.cjs', 'desktop/smoke.cjs',
  'desktop/loading.html', 'desktop/assets/icon.png', 'package.json']);

function auditPackage(directory, platform = process.platform) {
  const root = platform === 'darwin'
    ? path.join(directory, 'LingerLens.app', 'Contents', 'Resources')
    : path.join(directory, 'resources');
  const findings = [];
  function walk(dir) {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, item.name);
      const relative = path.relative(root, file).replaceAll('\\', '/');
      if (/(^|\/)(?:\.scratch|\.planning|\.playwright-cli|\.venv-desktop)(\/|$)/.test(relative)
          || /(^|\/)(?:providers\.json|auth-snapshot\.json|control\.secret|\.env)$/.test(relative)) findings.push(relative);
      if (item.isDirectory()) walk(file);
    }
  }
  walk(root);
  const allowed = new Set(['/desktop', '/desktop/assets', ...APP_FILES.map(file => '/' + file)]);
  for (const file of asar.listPackage(path.join(root, 'app.asar'))) {
    if (!allowed.has(file.replaceAll('\\', '/'))) findings.push(file);
  }
  if (findings.length) throw new Error('Unexpected packaged paths: ' + findings.join(', '));
}
module.exports = { auditPackage, APP_FILES };
