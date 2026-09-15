const fs = require('node:fs/promises');
const path = require('node:path');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);

/*
 * The packaged app ships an LGPL FFmpeg (`--disable-libx264`, libopenh264
 * enabled), while a developer machine usually has the GPL build with the
 * opposite arrangement. The fixture only needs *some* H.264 encoder, so ask the
 * binary that is actually going to run it instead of hard-coding one and
 * failing on the other side -- which is what happened: with libx264 hard-coded,
 * the packaged smoke test could never pass.
 */
async function h264Encoder(ffmpeg) {
  const { stdout } = await execFile(ffmpeg, ['-hide_banner', '-encoders'],
    { windowsHide: true, timeout: 20000, maxBuffer: 4 * 1024 * 1024 });
  for (const name of ['libx264', 'libopenh264']) {
    if (new RegExp(`\\b${name}\\b`).test(stdout)) return name;
  }
  throw new Error(`The FFmpeg at ${ffmpeg} offers neither libx264 nor libopenh264, so the smoke stream cannot be encoded.`);
}

async function runSmoke({ window, dataDir, ffmpeg, outputDir, backendPid }) {
  const publicDir = path.join(dataDir, 'runtime', 'media', 'public');
  await fs.mkdir(publicDir, { recursive: true });
  const encoder = await h264Encoder(ffmpeg);
  await execFile(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
    '-t', '3', '-c:v', encoder, '-pix_fmt', 'yuv420p', '-g', '24', '-c:a', 'aac',
    '-f', 'hls', '-hls_time', '1', '-hls_segment_type', 'fmp4', '-hls_playlist_type', 'vod',
    '-hls_segment_filename', path.join(publicDir, 'smoke_%03d.m4s'), path.join(publicDir, 'smoke.m3u8')],
  { windowsHide: true, timeout: 20000, cwd: publicDir });
  await fs.mkdir(outputDir, { recursive: true });
  await fs.writeFile(path.join(outputDir, 'media-files.json'), JSON.stringify({ files: await fs.readdir(publicDir), playlist: await fs.readFile(path.join(publicDir, 'smoke.m3u8'), 'utf8') }, null, 2));
  const result = await window.webContents.executeJavaScript(`(async () => {
    const status = await fetch('/api/status');
    const languages = await fetch('/api/languages');
    const settings = await fetch('/api/model-settings').then(r => r.json());
    const previous = localStorage.getItem('desktop-smoke'); localStorage.setItem('desktop-smoke', 'persisted');
    const video = document.createElement('video'); video.muted = true; document.body.append(video);
    const hls = new Hls();
    let played;
    try {
      played = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Synthetic HLS timed out')), 15000);
        video.addEventListener('timeupdate', () => { if (video.currentTime > 0.2) { clearTimeout(timeout); resolve(video.currentTime); } });
        hls.on(Hls.Events.ERROR, (_, data) => { if (data.fatal) { clearTimeout(timeout); reject(new Error(JSON.stringify({ details: data.details, url: data.frag?.url, status: data.response?.code }))); } });
        hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(reject));
        hls.loadSource('lingerlens://app/hls/smoke.m3u8'); hls.attachMedia(video);
      });
    } finally { hls.destroy(); video.remove(); }
    return { title: document.title, video: !!document.querySelector('video'), status: status.status,
      languages: languages.status, settingsLoaded: !!settings, previous, syntheticPlaybackSeconds: played };
  })()`);
  if (!result.video || result.status !== 200 || result.languages !== 200 || result.syntheticPlaybackSeconds < 0.2) throw new Error('Smoke failed');
  await fs.mkdir(outputDir, { recursive: true });
  await fs.writeFile(path.join(outputDir, 'result.json'), JSON.stringify({ ...result, backendPid }, null, 2));
  await fs.writeFile(path.join(outputDir, 'window.png'), (await window.webContents.capturePage()).toPNG());
}
module.exports = { runSmoke };
