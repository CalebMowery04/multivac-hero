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
llm=st.checkbox('Use OpenAI LLM for note mapping',value=False)
model=st.text_input('OpenAI model',value='gpt-4o-mini') if llm else 'gpt-4o-mini'
if llm and not os.getenv('OPENAI_API_KEY'):
    st.warning('OPENAI_API_KEY is not set. Reference-pattern fallback will be used.')
if st.button('Generate chart',disabled=file is None):
    with st.spinner('Analyzing audio, retrieving chart patterns, and generating MIDI...'):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/('input'+Path(file.name).suffix.lower())
            src.write_bytes(file.getbuffer())
            try:
                result=build_song(src,Path(__file__).parent/'references',Path(td)/'output',title,artist,
                                  None if seconds==0 else seconds,llm,model)
                data=Path(result['zip_path']).read_bytes()
                st.session_state['result']=result
                st.session_state['zip_data']=data
            except Exception as e:
                st.error(str(e))
if 'zip_data' in st.session_state:
    st.success('Chart generated!')
    st.json({k:v for k,v in st.session_state['result'].items() if k not in ['zip_path','folder']})
    st.download_button('Download Clone Hero ZIP',data=st.session_state['zip_data'],file_name='multivac-hero-song.zip',mime='application/zip')
