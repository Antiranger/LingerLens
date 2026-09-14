module.exports = {
  appId: 'io.github.antiranger.laglingo',
  productName: 'LagLingo',
  directories: { output: 'release' },
  asar: true,
  electronDist: 'build-desktop/electron',
  afterPack: async context => require('./audit-package.cjs').auditPackage(context.appOutDir),
  files: ['desktop/main.cjs', 'desktop/backend.cjs', 'desktop/smoke.cjs', 'desktop/loading.html', 'package.json'],
  extraResources: [
    { from: 'build-desktop/backend/laglingo-backend', to: 'backend', filter: ['**/*'] },
    { from: 'LICENSE', to: 'LICENSE-LagLingo.txt' },
    { from: 'THIRD_PARTY_NOTICES.md', to: 'THIRD_PARTY_NOTICES.md' },
    { from: 'desktop/DEPENDENCIES.md', to: 'DEPENDENCIES.md' },
  ],
  win: { target: [{ target: 'nsis', arch: ['x64'] }],
    artifactName: 'LagLingo-${version}-windows-${arch}-setup.${ext}' },
  nsis: { oneClick: true, perMachine: false, deleteAppDataOnUninstall: false,
    createDesktopShortcut: true, createStartMenuShortcut: true, runAfterFinish: false },
};
