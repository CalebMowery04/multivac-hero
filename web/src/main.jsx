import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import ChartPlayer from './ChartPlayer.jsx';

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || 'Request failed');
    error.status = response.status;
    throw error;
  }
  return data;
}
function App() {
  const [imageMissing, setImageMissing] = useState(false);
  // Other generation modes remain available in the backend and CLI.
  const directAudio = true;
  const mode = 'audio';
  const model = 'gpt-4o-mini'; // Legacy request field; audio mode uses gpt-audio-1.5.
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState('Generated Song');
  const [artist, setArtist] = useState('Unknown');
  const [seconds, setSeconds] = useState(30);
  const [referenceSeconds, setReferenceSeconds] = useState(30);
  const [job, setJob] = useState(null);
  const [jobID, setJobID] = useState(() => localStorage.getItem('multivac-job') || '');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [referenceFiles, setReferenceFiles] = useState([]);
  const [selectedTracks, setSelectedTracks] = useState([]);
  const [savedReferences, setSavedReferences] = useState([]);
  const [availableDifficulties, setAvailableDifficulties] = useState([]);
  const [selectedDifficulties, setSelectedDifficulties] = useState([]);
  const [difficultyLoading, setDifficultyLoading] = useState(false);
  const [difficultyError, setDifficultyError] = useState('');
  const [referenceID, setReferenceID] = useState('');
  const [pollVersion, setPollVersion] = useState(0);
  const [health, setHealth] = useState(null);
  useEffect(()=>{
    let cancelled=false;
    const midi=referenceFiles.find(f=>/\.mid$/i.test(f.name));
    if(!midi && !referenceID) {setAvailableDifficulties([]);setSelectedDifficulties([]);return;}
    setDifficultyLoading(true);setDifficultyError('');
    async function inspect() {
      try {
        const {parseChartFile}=await import('scan-chart');
        let buffer;
        if(midi) buffer=await midi.arrayBuffer();
        else {
          const response=await fetch(`/api/references/${referenceID}/midi`);
          if(!response.ok) throw new Error('Could not load saved reference MIDI.');
          buffer=await response.arrayBuffer();
        }
        const parsed=parseChartFile(new Uint8Array(buffer),'mid',{song_length:0,hopo_frequency:0,eighthnote_hopo:false,multiplier_note:0,sustain_cutoff_threshold:-1,chord_snap_threshold:0,five_lane_drums:false,pro_drums:false});
        const available=['easy','medium','hard','expert'].filter(d=>parsed.trackData.some(t=>t.instrument==='guitar' && t.difficulty===d && t.noteEventGroups.length>0));
        if(!available.length) throw new Error('No five-fret guitar difficulties found in this reference MIDI.');
        if(!cancelled) {setAvailableDifficulties(available);setSelectedDifficulties(available);}
      } catch(err) {if(!cancelled) {setDifficultyError(err.message);setAvailableDifficulties([]);setSelectedDifficulties([]);}}
      finally {if(!cancelled) setDifficultyLoading(false);}
    }
    inspect();return ()=>{cancelled=true;};
  },[referenceFiles,referenceID]);
  useEffect(() => { api('/api/references').then(setSavedReferences).catch(()=>{}); }, [job?.status]);
  useEffect(() => { api('/api/health').then(setHealth).catch(() => setHealth({offline:true})); }, []);
  useEffect(() => {
    if (!jobID) return;
    let disposed = false, timer;
    async function poll() {
      try {
        const next = await api(`/api/jobs/${jobID}`);
        if (disposed) return;
        setJob(next); setError('');
        if (next.status === 'awaiting_approval') {
          setReferenceSeconds(next.report.requested_reference_seconds ?? 0);
          setSeconds(next.report.requested_target_seconds ?? 0);
          if(next.report.available_difficulties) setAvailableDifficulties(next.report.available_difficulties);
          if(next.report.selected_difficulties) setSelectedDifficulties(next.report.selected_difficulties);
        }
        if (['queued','running'].includes(next.status)) timer = setTimeout(poll, 1500);
      } catch (err) {
        if (!disposed) {
          if (err.status === 404) {
            setJobID(''); setJob(null); localStorage.removeItem('multivac-job');
            setError('The previous job is no longer available after the server restart. Choose your target song again to start a new job.');
          } else {
            setError(err.message); timer = setTimeout(poll, 5000);
          }
        }
      }
    }
    poll();
    return () => {disposed = true; clearTimeout(timer);};
  }, [jobID, pollVersion]);
  const busy = submitting || (jobID && (!job || ['queued','running','awaiting_approval'].includes(job.status)));
  const disabledReason = submitting ? 'Uploading or submitting your request…'
    : job?.status === 'awaiting_approval' ? 'Your mix is prepared. Review it below and approve or cancel before starting another.'
    : busy ? 'Waiting for the current job to finish or reconnect…'
    : !file ? 'Choose the song you want to chart under “Upload song” above. The reference folder is a separate input.'
    : health?.offline ? 'The backend is offline. Start the Go server and refresh this page.' : '';
  async function submit(event) {
    event.preventDefault(); setError(''); setSubmitting(true);
    const form = new FormData();
    form.append('audio',file); form.append('title',title); form.append('artist',artist);
    const hasReference = referenceFiles.length > 0 || referenceID;
    form.append('seconds',mode === 'audio' && !hasReference ? Math.min(Number(seconds) || 15,15) : seconds);
    if (mode === 'audio') {
      form.append('reference_id',referenceID);
      form.append('reference_seconds',referenceSeconds);
      for(const difficulty of selectedDifficulties) form.append('reference_difficulties',difficulty);
      for (const ref of referenceFiles) {
        if (ref.name.toLowerCase().endsWith('.mid') || selectedTracks.includes(ref.name)) form.append('reference',ref,ref.name);
      }
    }
    form.append('mode',mode); form.append('model',model);
    try {
      const next = await api('/api/jobs',{method:'POST',body:form});
      setJob(next); setJobID(next.id); localStorage.setItem('multivac-job',next.id);
    } catch(err) {setError(err.message);} finally {setSubmitting(false);}
  }
  async function cancelPrepared() {
    setSubmitting(true);
    try {await api(`/api/jobs/${jobID}/cancel`,{method:'POST'});forgetJob();}
    catch(err) {setError(err.message);} finally {setSubmitting(false);}
  }
  async function recalculate() {
    setSubmitting(true);setError('');
    try {
      const next = await api(`/api/jobs/${jobID}/recalculate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference_seconds:Number(referenceSeconds),seconds:Number(seconds),reference_difficulties:selectedDifficulties})});
      setJob(next);setPollVersion(v=>v+1);
    } catch(err) {setError(err.message);} finally {setSubmitting(false);}
  }
  async function approve() {
    setSubmitting(true);setError('');
    try {
      const next = await api(`/api/jobs/${jobID}/approve`,{method:'POST'});
      setJob(next);setPollVersion(v=>v+1);
    } catch(err) {setError(err.message);} finally {setSubmitting(false);}
  }
  function chooseFolder(event) {
    const files = Array.from(event.target.files || []);
    const usable = files.filter(f => /\.(mp3|mid)$/i.test(f.name) && !/^preview\.mp3$/i.test(f.name));
    const directories = new Set(usable.map(f=>f.webkitRelativePath.split('/').slice(0,-1).join('/')));
    if (directories.size > 1) {setError('Choose one song folder, without nested song folders.');return;}
    setReferenceFiles(usable);setReferenceID('');setSelectedTracks([]);setError('');
  }
  function forgetJob() {setJobID('');setJob(null);setError('');localStorage.removeItem('multivac-job');}
  const estimate = job?.status === 'awaiting_approval' ? job.report : null;
  const estimateChanged = estimate && (Number(referenceSeconds)!==estimate.requested_reference_seconds || Number(seconds)!==estimate.requested_target_seconds || JSON.stringify([...selectedDifficulties].sort())!==JSON.stringify([...(estimate.selected_difficulties || [])].sort()));
  const invalidDurations = !Number.isInteger(Number(referenceSeconds)) || Number(referenceSeconds)<0 || Number(referenceSeconds)>1800 || !Number.isInteger(Number(seconds)) || Number(seconds)<0 || Number(seconds)>900;
  const report = job?.status === 'completed' ? job.report : null;
  const generation = report?.generation;
  const reasons = [...new Set((generation?.chunks || []).filter(c => c.reason).map(c => `${c.reason}${c.error_type ? ` (${c.error_type})` : ''}`))];
  const resultMessage = report && (report.mode === 'llm'
    ? `Chart generated: LLM succeeded for all ${generation.llm_chunks} chunks.`
    : report.mode === 'mixed'
      ? `Chart generated: LLM succeeded for ${generation.llm_chunks} chunks; fallback used for ${generation.fallback_chunks} chunks.`
      : report.mode === 'fallback'
        ? 'Chart generated using fallback: no chunks were mapped successfully by the LLM.'
        : 'Chart generated using the reference-pattern baseline (LLM disabled).');
  const hasReference = referenceFiles.length>0 || referenceID;
  const difficultyBlocked = hasReference && (difficultyLoading || !!difficultyError || selectedDifficulties.length===0);
  function difficultyChoices(disabled) {
    return <fieldset className="difficulty-options"><legend>Reference guitar difficulties</legend>
      {difficultyLoading && <small>Reading MIDI difficulties…</small>}
      {difficultyError && <p className="notice error">{difficultyError}</p>}
      {availableDifficulties.map(d=><label className="checkbox" key={d}><input type="checkbox" disabled={disabled} checked={selectedDifficulties.includes(d)} onChange={e=>setSelectedDifficulties(values=>e.target.checked?[...values,d]:values.filter(v=>v!==d))}/>{d[0].toUpperCase()+d.slice(1)}</label>)}
      {availableDifficulties.length>0 && selectedDifficulties.length===0 && <small>Choose at least one reference difficulty.</small>}
      <small>Filters the example guitar notes; generated output is still Expert guitar.</small>
    </fieldset>;
  }
  return <main className="shell">
    {!imageMissing && <img className="home-image" src="/resources/home.png" alt="Multivac Hero home screen artwork" onError={()=>setImageMissing(true)}/>}
    <p>Generate an Expert Clone Hero MIDI chart from audio, guided by existing human-made charts.</p>
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
      <label>Target seconds to chart (0 = full song)<input type="number" min="0" max="900" step="1" required value={seconds} disabled={busy} onChange={e=>setSeconds(e.target.value)}/></label>
      {directAudio && <fieldset className="reference-panel"><legend>Reference song folder</legend>
        <p>Choose one folder containing a .mid chart and MP3 tracks. Preview MP3s and other files are ignored. Select only tracks that should play together; song.mp3 may already be a full mix.</p>
        <input aria-label="Reference song folder" type="file" webkitdirectory="" multiple disabled={busy} onChange={chooseFolder}/>
        {referenceFiles.filter(f=>f.name.toLowerCase().endsWith('.mid')).map(f=><p key={f.name}>Chart: {f.name}</p>)}
        {referenceFiles.filter(f=>f.name.toLowerCase().endsWith('.mp3')).map(f=><label className="checkbox" key={f.name}><input type="checkbox" disabled={busy} checked={selectedTracks.includes(f.name)} onChange={e=>setSelectedTracks(list=>e.target.checked?[...list,f.name]:list.filter(n=>n!==f.name))}/>{f.name}</label>)}
        {referenceFiles.length>0 && <small>Select at least one MP3. The folder must contain exactly one MIDI file.</small>}
        {savedReferences.length>0 && <label>Or reuse a saved reference<select disabled={busy} value={referenceID} onChange={e=>{setReferenceID(e.target.value);setReferenceFiles([]);setSelectedTracks([]);}}><option value="">None</option>{savedReferences.map(ref=><option key={ref.id} value={ref.id}>{ref.files.join(', ')} ({ref.id.slice(0,6)})</option>)}</select></label>}
        {hasReference && difficultyChoices(busy)}
        <label>Reference seconds to use (0 = full reference)
          <input type="number" min="0" max="1800" step="1" value={referenceSeconds} required disabled={busy} onChange={e=>setReferenceSeconds(e.target.value)}/>
          <small>Uses the beginning of the mix and matching MIDI; held notes are trimmed at the end.</small>
        </label>
        <small>Selected files are saved locally for reuse. Nothing is sent to OpenAI until you approve the prepared request. Mixes assume all tracks start at zero; metadata offsets are not applied.</small>
      </fieldset>}
      {mode!=='baseline' && health && !health.offline && !health.api_key_configured && <div className="notice warning">OPENAI_API_KEY is not set. {directAudio && (referenceFiles.length>0 || referenceID) ? 'Configure it on the backend before approving this request.' : 'Reference-pattern fallback will be used.'}</div>}
      {health?.offline && <div className="notice error">Cannot reach the Go API. Start the backend on port 8080.</div>}
      {disabledReason && <p className="caption" role="status">{disabledReason}</p>}
      <button title={disabledReason || undefined} disabled={!file || busy || health?.offline || difficultyBlocked}>{submitting?'Uploading…':directAudio && (referenceFiles.length>0 || referenceID)?'Calculate estimate and preview':'Generate chart'}</button>
    </form>
    <section className="results" aria-live="polite" aria-label="Generation result">
      {estimate && <div className="notice info">
        <h2>Review reference mix</h2>
        <p>Tracks: {estimate.reference_files.join(', ')}</p>
        <p>Reference: {Number(estimate.reference_seconds).toFixed(1)} seconds. Target: {Number(estimate.target_seconds).toFixed(1)} seconds.</p>
        {estimate.preview_ready && <audio key={estimate.reference_seconds} controls src={`/api/jobs/${jobID}/preview?v=${estimate.reference_seconds}`}/>}
        <p>Estimated request cost: <strong>${Number(estimate.estimated_cost_usd).toFixed(2)}</strong></p>
        <table className="estimate-table"><caption>Estimated token usage</caption><tbody>
          <tr><th>Reference audio</th><td>{estimate.size_breakdown?.reference_audio_tokens?.toLocaleString()}</td></tr>
          <tr><th>Target audio</th><td>{estimate.size_breakdown?.target_audio_tokens?.toLocaleString()}</td></tr>
          <tr><th>Matching MIDI text</th><td>{estimate.size_breakdown?.midi_text_tokens?.toLocaleString()}</td></tr>
          <tr><th>Prompt allowance</th><td>{estimate.size_breakdown?.prompt_allowance_tokens?.toLocaleString()}</td></tr>
          <tr><th>Total input / allowance</th><td>{estimate.estimated_input_tokens?.toLocaleString()} / {estimate.size_breakdown?.input_allowance_tokens?.toLocaleString()}</td></tr>
          <tr><th>Output allowance</th><td>{estimate.estimated_output_tokens?.toLocaleString()}</td></tr>
          <tr><th>Total including output allowance</th><td>{estimate.estimated_total_tokens?.toLocaleString()}</td></tr>
        </tbody></table>
        {difficultyChoices(submitting)}
        <label>Reference seconds (0 = full reference)<input type="number" min="0" max="1800" step="1" value={referenceSeconds} onChange={e=>setReferenceSeconds(e.target.value)}/></label>
        <label>Target seconds to chart (0 = full song)<input type="number" min="0" max="900" step="1" value={seconds} onChange={e=>setSeconds(e.target.value)}/></label>
        <button type="button" disabled={submitting || invalidDurations || selectedDifficulties.length===0} onClick={recalculate}>Recalculate estimate and preview</button>
        {estimateChanged && <p className="notice warning">Reference settings changed. Recalculate before approving; the estimate above is for the previous settings.</p>}
        {estimate.size_exceeded && <p className="notice warning">{estimate.size_message}</p>}
        <p>Approval sends the selected reference mix, matching MIDI event text, and selected target audio to OpenAI. Once submitted, the request cannot be stopped at an exact dollar amount.</p>
        <button type="button" disabled={submitting || invalidDurations || selectedDifficulties.length===0 || estimateChanged || estimate.size_exceeded} onClick={approve}>Approve cost and generate</button>
        <button type="button" disabled={submitting} className="clear" onClick={cancelPrepared}>Do not send</button>
      </div>}
      {job && ['queued','running'].includes(job.status) && <p className="processing"><span className="spinner" aria-hidden="true"/>Analyzing audio, retrieving chart patterns, and generating MIDI...</p>}
      {error && <div role="alert" className="notice error">{error}</div>}
      {job?.error && <div role="alert" className="notice error">{job.error}</div>}
      {job?.status === 'failed' && job.report?.size_breakdown && <details><summary>Request-size breakdown</summary><pre>{JSON.stringify(job.report.size_breakdown,null,2)}</pre></details>}
      {report && <>
        <div className={`notice ${['fallback','mixed'].includes(report.mode) ? 'warning' : 'success'}`}>{resultMessage}</div>
        {reasons.length>0 && <p className="caption">Fallback reasons: {reasons.join(', ')}</p>}
        <ChartPlayer key={job.id} jobID={job.id} chartedSeconds={report.charted_seconds}/>
        <a className="download" href={`/api/jobs/${job.id}/download`}>Download Clone Hero ZIP</a>
      </>}
      {(error || job?.status==='completed' || job?.status==='failed') && <button type="button" className="clear" onClick={forgetJob}>Clear result</button>}
    </section>
  </main>;
}
createRoot(document.getElementById('root')).render(<App/>);
