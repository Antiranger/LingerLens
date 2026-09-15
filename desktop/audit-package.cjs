const fs = require('node:fs');
const path = require('node:path');
const asar = require('@electron/asar');

function auditPackage(directory) {
  const root = path.join(directory, 'resources');
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
  const allowed = new Set(['/desktop', '/desktop/main.cjs', '/desktop/backend.cjs',
    '/desktop/devlog.cjs', '/desktop/updater.cjs', '/desktop/smoke.cjs',
    '/desktop/loading.html', '/package.json']);
  for (const file of asar.listPackage(path.join(root, 'app.asar'))) {
    if (!allowed.has(file.replaceAll('\\', '/'))) findings.push(file);
  }
  if (findings.length) throw new Error('Unexpected packaged paths: ' + findings.join(', '));
}
module.exports = { auditPackage };
