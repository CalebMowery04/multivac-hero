import React, {useEffect, useRef, useState} from 'react';

// Load the 3D player only on result pages, keeping the initial form bundle small.
export default function ChartPlayer({jobID, chartedSeconds}) {
  const host = useRef(null);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState('');
  useEffect(()=>{
    const controller = new AbortController();
    let disposed = false, player;
    setLoading(true);setError('');
    async function loadFile(route, fallbackName) {
      const response = await fetch(`/api/jobs/${jobID}/${route}`,{signal:controller.signal});
      if(!response.ok) throw new Error('Preview files are unavailable. Generate a new chart if this job has expired.');
      return {fileName:response.headers.get('X-Audio-Filename') || fallbackName,data:new Uint8Array(await response.arrayBuffer())};
    }
    async function load() {
      try {
        const [library, midi, audio, ini] = await Promise.all([
          import('chart-preview'),loadFile('midi','notes.mid'),loadFile('audio','song.mp3'),loadFile('ini','song.ini')
        ]);
        if(disposed) return;
        if(!/\.(mp3|ogg|wav|opus)$/i.test(audio.fileName)) throw new Error('This viewer supports MP3, WAV, OGG, and Opus audio. Use MP3 or WAV for browser previews.');
        player = document.createElement('chart-preview-player');
        player.setAttribute('volume','50');
        player.addEventListener('player-error',handleError);
        host.current.replaceChildren(player);
        await player.loadFromChartFiles({files:[midi,audio,ini],instrument:'guitar',difficulty:'expert',animationsEnabled:library.areAnimationsSupported()});
        if(disposed) player.dispose();
        else setLoading(false);
      } catch(err) {
        if(!disposed && err.name!=='AbortError') {setError(err.message || 'Could not load the 3D chart preview.');setLoading(false);}
      }
    }
    function handleError(event) {
      if(!disposed) {setError(String(event.detail?.error?.message || event.detail?.error || 'Could not load chart. Check that WebGL and the texture CDN are available.'));setLoading(false);}
    }
    load();
    return ()=>{
      disposed=true;controller.abort();
      if(player) {player.removeEventListener('player-error',handleError);player.dispose();player.remove();}
    };
  },[jobID]);
  return <section className="chart-player" aria-label="Chart playback preview">
    <h2>Chart preview</h2>
    <p className="caption">Expert guitar · {Number(chartedSeconds).toFixed(1)} seconds charted. Play, pause, seek, or enter fullscreen with the player controls.</p>
    {loading && <p role="status">Loading 3D highway and audio…</p>}
    {error && <p role="alert" className="notice error">{error}</p>}
    <div ref={host} className="chart-player-host"/>
    <p className="caption">Viewer by <a href="https://github.com/Geomitron/chart-preview" target="_blank" rel="noreferrer">Geomitron / chart-preview</a>. Textures load from static.enchor.us; internet access and WebGL are required.</p>
  </section>;
}
