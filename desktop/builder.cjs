module.exports = {
  appId: 'io.github.antiranger.lingerlens',
  productName: 'LingerLens',
  directories: { output: 'release' },
  asar: true,
  electronDist: 'build-desktop/electron',
  afterPack: async context => require('./audit-package.cjs').auditPackage(context.appOutDir),
  // electron-builder normalizes this array in place; keep the audit manifest immutable.
  files: [...require('./audit-package.cjs').APP_FILES],
  extraResources: [
    { from: 'build-desktop/backend/lingerlens-backend', to: 'backend', filter: ['**/*'] },
    { from: 'LICENSE', to: 'LICENSE-LingerLens.txt' },
    { from: 'THIRD_PARTY_NOTICES.md', to: 'THIRD_PARTY_NOTICES.md' },
    { from: 'licenses', to: 'licenses', filter: ['**/*'] },
    { from: 'desktop/DEPENDENCIES.md', to: 'DEPENDENCIES.md' },
  ],
  win: { target: [{ target: 'nsis', arch: ['x64'] }],
    artifactName: 'LingerLens-${version}-windows-${arch}-setup.${ext}' },
  nsis: { oneClick: true, perMachine: false, deleteAppDataOnUninstall: false,
    createDesktopShortcut: true, createStartMenuShortcut: true, runAfterFinish: false },
};
