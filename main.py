import argparse
import json
from pathlib import Path
from engine import build_song

parser=argparse.ArgumentParser(description='Generate a Clone Hero Expert guitar chart from an MP3/WAV file')
parser.add_argument('audio',type=Path)
parser.add_argument('--references',type=Path,default=Path(__file__).parent/'references')
parser.add_argument('--output',type=Path,default=Path(__file__).parent/'output')
parser.add_argument('--title',default='Generated Song')
parser.add_argument('--artist',default='Unknown')
parser.add_argument('--seconds',type=float,default=30,help='Analyze the first N seconds; use 0 for full song')
parser.add_argument('--llm',action='store_true',help='Use OpenAI API if OPENAI_API_KEY is set')
parser.add_argument('--model',default='gpt-4o-mini')
args=parser.parse_args()
result=build_song(args.audio,args.references,args.output,args.title,args.artist,
                  None if args.seconds<=0 else args.seconds,args.llm,args.model)
print(json.dumps(result,indent=2))
