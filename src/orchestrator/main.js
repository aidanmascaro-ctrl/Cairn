// Cairn — Electron main process
//
// Runs inside DaVinci Resolve's Workflow Integration host. Follows the
// sandboxed model from Blackmagic's SamplePromisePlugin (Developer/
// Workflow Integrations/Examples/SamplePromisePlugin): contextIsolation
// enabled, no nodeIntegration in the renderer, all Resolve operations
// brokered through preload + ipcMain.
//
// The renderer (built by Vite from src/ui) loads dist/index.html and
// calls window.cairn.<op>() which resolves through this process.

'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');

// WorkflowIntegration.node is a native Node addon shipped by Resolve.
// In development we copy it alongside this file via packaging/install-dev-plugin.sh.
// At runtime we tolerate its absence (dev UI still renders, Resolve calls return null).
const PLUGIN_ID = 'com.aidanmascaro.cairn';

/** @type {any} */
let WorkflowIntegration = null;
try {
    WorkflowIntegration = require('./WorkflowIntegration.node');
} catch (err) {
    // Expected when running `npm run start` outside Resolve. Logged, not fatal.
    console.warn(`[cairn] WorkflowIntegration.node not available: ${err.message}`);
    console.warn('[cairn] Install the plugin into Resolve to exercise the real API.');
}

/** Cached Resolve interface objects — initialised on first use. */
let initObj = null;
let resolvePromiseObj = null;

/** @returns {Promise<object|null>} */
async function initialiseResolve() {
    if (!WorkflowIntegration) return null;
    if (initObj) return initObj;

    initObj = await WorkflowIntegration.InitializePromise(PLUGIN_ID);
    if (!initObj) {
        console.error('[cairn] WorkflowIntegration.InitializePromise returned null.');
    }
    return initObj;
}

/** @returns {Promise<object|null>} */
async function getResolve() {
    if (resolvePromiseObj) return resolvePromiseObj;
    if (!WorkflowIntegration) return null;

    if (!(await initialiseResolve())) return null;

    resolvePromiseObj = WorkflowIntegration.GetResolvePromise();
    return resolvePromiseObj;
}

/**
 * Proof-of-life: returns a small snapshot of Resolve's current state.
 * @returns {Promise<{available: boolean, projectName: string|null, timelineName: string|null, error?: string}>}
 */
async function getResolveSnapshot() {
    if (!WorkflowIntegration) {
        return {
            available: false,
            projectName: null,
            timelineName: null,
            error: 'WorkflowIntegration.node not loaded — install the plugin into Resolve.',
        };
    }

    try {
        const resolve = await getResolve();
        if (!resolve) {
            return {
                available: false,
                projectName: null,
                timelineName: null,
                error: 'Resolve interface did not initialise.',
            };
        }

        const projectManager = await resolve.GetProjectManager();
        const project = projectManager ? await projectManager.GetCurrentProject() : null;
        const projectName = project ? await project.GetName() : null;
        const timeline = project ? await project.GetCurrentTimeline() : null;
        const timelineName = timeline ? await timeline.GetName() : null;

        return {
            available: true,
            projectName,
            timelineName,
        };
    } catch (err) {
        return {
            available: false,
            projectName: null,
            timelineName: null,
            error: err instanceof Error ? err.message : String(err),
        };
    }
}

function registerIpc() {
    ipcMain.handle('cairn:resolve:snapshot', async () => getResolveSnapshot());
    ipcMain.handle('cairn:ping', async () => ({
        ok: true,
        pluginId: PLUGIN_ID,
        electron: process.versions.electron,
        node: process.versions.node,
        chrome: process.versions.chrome,
    }));
}

function createMainWindow() {
    const win = new BrowserWindow({
        width: 1040,
        height: 720,
        useContentSize: true,
        autoHideMenuBar: true,
        title: 'Cairn',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
        },
    });

    // Load the Vite-built renderer from ../../dist/index.html
    // (main.js lives at src/orchestrator/main.js; dist is at the plugin root)
    const indexHtml = path.join(__dirname, '..', '..', 'dist', 'index.html');
    win.loadFile(indexHtml).catch((err) => {
        console.error(`[cairn] Failed to load ${indexHtml}: ${err.message}`);
    });

    // Open DevTools only when CAIRN_DEVTOOLS=1 in the environment.
    if (process.env.CAIRN_DEVTOOLS === '1') {
        win.webContents.openDevTools({ mode: 'detach' });
    }

    win.on('close', () => {
        if (WorkflowIntegration) {
            try { WorkflowIntegration.CleanUp(); } catch (_) { /* noop */ }
        }
    });
}

app.whenReady().then(() => {
    registerIpc();
    createMainWindow();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
});

app.on('window-all-closed', () => {
    if (WorkflowIntegration) {
        try { WorkflowIntegration.CleanUp(); } catch (_) { /* noop */ }
    }
    if (process.platform !== 'darwin') app.quit();
});
