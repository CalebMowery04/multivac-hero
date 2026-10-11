import os
import tempfile
from pathlib import Path
import streamlit as st
from engine import build_song

st.set_page_config(page_title='Multivac Hero',page_icon='🎸')
st.title('Multivac Hero')
st.write('Generate an Expert Clone Hero MIDI chart from audio, guided by existing human-made charts.')
file=st.file_uploader('Upload song',type=['mp3','wav','ogg','flac'])
title=st.text_input('Song title','Generated Song')
artist=st.text_input('Artist','Unknown')
seconds=st.number_input('Seconds to analyze (0 = full song)',min_value=0,max_value=900,value=30,step=15)
direct_audio = st.checkbox('Let the model listen and create the chart (experimental)', value=False)
if direct_audio:
    st.info('Sends up to the first 15 seconds of uploaded audio to OpenAI using gpt-audio-1.5. The model chooses timing, chords, and sustains. API charges apply; failures use the local baseline.')
llm=st.checkbox('Use OpenAI LLM for note mapping',value=False, disabled=direct_audio)
model = st.selectbox(
    'OpenAI model',
    options=['gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4.1-nano'],
    help='Choose the model used to assign frets. API pricing and model access vary.',
) if llm and not direct_audio else 'gpt-4o-mini'
if (llm or direct_audio) and not os.getenv('OPENAI_API_KEY'):
    st.warning('OPENAI_API_KEY is not set. Reference-pattern fallback will be used.')
if st.button('Generate chart',disabled=file is None):
    with st.spinner('Analyzing audio, retrieving chart patterns, and generating MIDI...'):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/('input'+Path(file.name).suffix.lower())
            src.write_bytes(file.getbuffer())
            try:
                result=build_song(src,Path(__file__).parent/'references',Path(td)/'output',title,artist,
                                  None if seconds==0 else seconds,llm,model,direct_audio=direct_audio)
                data=Path(result['zip_path']).read_bytes()
                st.session_state['result']=result
                st.session_state['zip_data']=data
            except Exception as e:
                st.error(str(e))
if 'zip_data' in st.session_state:
    result = st.session_state['result']
    generation = result['generation']
    if result['mode'] == 'llm':
        st.success(f"Chart generated: LLM succeeded for all {generation['llm_chunks']} chunks.")
    elif result['mode'] == 'mixed':
        st.warning(f"Chart generated: LLM succeeded for {generation['llm_chunks']} chunks; fallback used for {generation['fallback_chunks']} chunks.")
    elif result['mode'] == 'fallback':
        st.warning('Chart generated using fallback: no chunks were mapped successfully by the LLM.')
    else:
        st.success('Chart generated using the reference-pattern baseline (LLM disabled).')
    reasons = sorted({c['reason'] for c in generation['chunks'] if 'reason' in c})
    if reasons:
        st.caption('Fallback reasons: ' + ', '.join(reasons))
    st.json({k:v for k,v in st.session_state['result'].items() if k not in ['zip_path','folder']})
    st.download_button('Download Clone Hero ZIP',data=st.session_state['zip_data'],file_name='multivac-hero-song.zip',mime='application/zip')
