#!/usr/bin/env python3
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; ENV=ROOT/'.env'
D={'OPERATOR_DISPLAY_NAME':'Operator','TERMINAL_DISPLAY_NAME':'Sentinuity','COUNCIL_NAME':'The Council','POLARIS_DISPLAY_NAME':'Polaris','IVARIS_DISPLAY_NAME':'Ivaris','NUGGET_DISPLAY_NAME':'Nugget','ORACLE_DISPLAY_NAME':'Oracle','AXON_DISPLAY_NAME':'Axon','FORGE_DISPLAY_NAME':'Forge','WORLD_THEME':'sovereign_crystalline','NARRATIVE_INTENSITY':'moderate'}
def ask(q,d): return input(f'{q} [{d}]: ').strip() or d
def main():
 vals={}
 if ENV.exists():
  for l in ENV.read_text().splitlines():
   if '=' in l and not l.lstrip().startswith('#'): k,v=l.split('=',1); vals[k]=v
 print('WELCOME TO YOUR SENTINUITY TERMINAL\nDisplay names may change; backend IDs and safety contracts never do.\n')
 vals['OPERATOR_DISPLAY_NAME']=ask('What should the terminal call you?',vals.get('OPERATOR_DISPLAY_NAME','Operator'))
 vals['TERMINAL_DISPLAY_NAME']=ask('What should your terminal be called?',vals.get('TERMINAL_DISPLAY_NAME','Sentinuity'))
 print('1 Sovereign Crystalline  2 Research Terminal  3 Custom')
 ch=ask('Choose a visual world','1'); vals['WORLD_THEME']={'1':'sovereign_crystalline','2':'research_terminal','3':'custom'}.get(ch,'sovereign_crystalline'); vals['NARRATIVE_INTENSITY']='minimal' if ch=='2' else ('custom' if ch=='3' else 'moderate')
 if ask('Customise Council display names? y/N','N').lower().startswith('y'):
  for k,n in [('POLARIS_DISPLAY_NAME','Coordinator'),('IVARIS_DISPLAY_NAME','Critic'),('NUGGET_DISPLAY_NAME','Auditor'),('ORACLE_DISPLAY_NAME','Data Scout'),('AXON_DISPLAY_NAME','Systems Analyst'),('FORGE_DISPLAY_NAME','Builder')]: vals[k]=ask(n,vals.get(k,n))
 lines=ENV.read_text().splitlines() if ENV.exists() else []; managed=set(D); lines=[l for l in lines if not ('=' in l and l.split('=',1)[0] in managed)]
 lines += [f'{k}={vals.get(k,v)}' for k,v in D.items()]; ENV.write_text('\n'.join(lines).rstrip()+'\n')
 print('Personalisation saved. Execution doctrine remains unchanged.')
if __name__=='__main__': main()
