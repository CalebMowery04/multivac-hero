import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function App() {
  const [imageMissing, setImageMissing] = useState(false);
  const [directAudio, setDirectAudio] = useState(false);
  const [useLLM, setUseLLM] = useState(false);
  const mode = directAudio ? 'audio' : useLLM ? 'llm' : 'baseline';
  const [model, setModel] = useState('gpt-4o-mini');
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState('Generated Song');
  const [artist, setArtist] = useState('Unknown');
  const [seconds, setSeconds] = useState(30);
  const [job, setJob] = useState(null);
  const [jobID, setJobID] = useState(() => localStorage.getItem('multivac-job') || '');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [health, setHealth] = useState(null);
  useEffect(() => { api('/api/health').then(setHealth).catch(() => setHealth({offline:true})); }, []);
  useEffect(() => {
    if (!jobID) return;
    let disposed = false, timer;
    async function poll() {
      try {
        const next = await api(`/api/jobs/${jobID}`);
        if (disposed) return;
        setJob(next); setError('');
        if (['queued','running'].includes(next.status)) timer = setTimeout(poll, 1500);
      } catch (err) {
        if (!disposed) { setError(err.message); timer = setTimeout(poll, 5000); }
      }
    }
    poll();
    return () => {disposed = true; clearTimeout(timer);};
  }, [jobID]);
  const busy = submitting || (jobID && (!job || ['queued','running'].includes(job.status)));
  async function submit(event) {
    event.preventDefault(); setError(''); setSubmitting(true);
    const form = new FormData();
    form.append('audio',file); form.append('title',title); form.append('artist',artist);
    form.append('seconds',mode === 'audio' ? Math.min(Number(seconds) || 15,15) : seconds);
    form.append('mode',mode); form.append('model',model);
    try {
      const next = await api('/api/jobs',{method:'POST',body:form});
      setJob(next); setJobID(next.id); localStorage.setItem('multivac-job',next.id);
    } catch(err) {setError(err.message);} finally {setSubmitting(false);}
  }
  function forgetJob() {setJobID('');setJob(null);setError('');localStorage.removeItem('multivac-job');}
  const report = job?.report;
  const generation = report?.generation;
  const reasons = [...new Set((generation?.chunks || []).filter(c => c.reason).map(c => `${c.reason}${c.error_type ? ` (${c.error_type})` : ''}`))];
  const resultMessage = report && (report.mode === 'llm'
    ? `Chart generated: LLM succeeded for all ${generation.llm_chunks} chunks.`
    : report.mode === 'mixed'
      ? `Chart generated: LLM succeeded for ${generation.llm_chunks} chunks; fallback used for ${generation.fallback_chunks} chunks.`
      : report.mode === 'fallback'
        ? 'Chart generated using fallback: no chunks were mapped successfully by the LLM.'
        : 'Chart generated using the reference-pattern baseline (LLM disabled).');
  return <main className="shell">
    <h1>Multivac Hero</h1>
    <p>Generate an Expert Clone Hero MIDI chart from audio, guided by existing human-made charts.</p>
    {!imageMissing && <img className="home-image" src="/resources/home.png" alt="Multivac Hero home screen artwork" onError={()=>setImageMissing(true)}/>}
    <form onSubmit={submit}>
      <label>Upload song
        <div className="upload">
          <span>Choose an audio file</span>
          <small>MP3, WAV, OGG or FLAC · up to 100 MB</small>
          <input aria-label="Upload song" type="file" accept=".mp3,.wav,.ogg,.flac" required disabled={busy} onChange={e=>setFile(e.target.files[0] || null)}/>
        </div>
      </label>
      <label>Song title<input value={title} maxLength={100} onChange={e=>setTitle(e.target.value)} disabled={busy}/></label>
      <label>Artist<input value={artist} maxLength={100} onChange={e=>setArtist(e.target.value)} disabled={busy}/></label>
      <label>Seconds to analyze (0 = full song)<input type="number" min="0" max="900" step="15" required value={seconds} disabled={busy} onChange={e=>setSeconds(e.target.value)}/></label>
      <label className="checkbox"><input type="checkbox" checked={directAudio} disabled={busy} onChange={e=>setDirectAudio(e.target.checked)}/>Let the model listen and create the chart (experimental)</label>
      {directAudio && <div className="notice info">Sends up to the first 15 seconds of uploaded audio to OpenAI using gpt-audio-1.5. The model chooses timing, chords, and sustains. API charges apply; failures use the local baseline. The ZIP keeps the full recording.</div>}
      <label className="checkbox"><input type="checkbox" checked={useLLM} disabled={busy || directAudio} onChange={e=>setUseLLM(e.target.checked)}/>Use OpenAI LLM for note mapping</label>
      {useLLM && !directAudio && <label>OpenAI model<select value={model} disabled={busy} onChange={e=>setModel(e.target.value)}>{['gpt-4o-mini','gpt-4.1-mini','gpt-4.1-nano'].map(m=><option key={m}>{m}</option>)}</select></label>}
      {mode!=='baseline' && health && !health.offline && !health.api_key_configured && <div className="notice warning">OPENAI_API_KEY is not set. Reference-pattern fallback will be used.</div>}
      {health?.offline && <div className="notice error">Cannot reach the Go API. Start the backend on port 8080.</div>}
      <button disabled={!file || busy || health?.offline}>{submitting?'Uploading…':'Generate chart'}</button>
    </form>
    <section className="results" aria-live="polite" aria-label="Generation result">
      {job && ['queued','running'].includes(job.status) && <p className="processing"><span className="spinner" aria-hidden="true"/>Analyzing audio, retrieving chart patterns, and generating MIDI...</p>}
      {error && <div role="alert" className="notice error">{error}</div>}
      {job?.error && <div role="alert" className="notice error">{job.error}</div>}
      {report && <>
        <div className={`notice ${['fallback','mixed'].includes(report.mode) ? 'warning' : 'success'}`}>{resultMessage}</div>
        {reasons.length>0 && <p className="caption">Fallback reasons: {reasons.join(', ')}</p>}
        <pre aria-label="Generation report">{JSON.stringify(report,null,2)}</pre>
        <a className="download" href={`/api/jobs/${job.id}/download`}>Download Clone Hero ZIP</a>
      </>}
      {(error || job?.status==='completed' || job?.status==='failed') && <button type="button" className="clear" onClick={forgetJob}>Clear result</button>}
    </section>
  </main>;
}
createRoot(document.getElementById('root')).render(<App/>);
