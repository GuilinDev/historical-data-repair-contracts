"""Replay only editing operations on scratch copies; no model or candidate execution."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from editing_ablation_tools import AblationGateway
from resource_task_tools import ResourceDataset
from run_editing_ablation import DATA


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    data=ResourceDataset(DATA); summary=read(OUT/'summary.json')
    assert summary['protocol_and_evaluation_complete']
    counts=Counter(); diagnoses=[]; operations=0
    with tempfile.TemporaryDirectory(prefix='gemma_edit_mechanics_') as temporary:
        scratch=Path(temporary)
        for row in summary['episodes']:
            original=OUT/'episodes'/row['episode']
            journal=read(original/'tool_outputs.json') if (original/'tool_outputs.json').exists() else []
            gateway=object.__new__(AblationGateway)
            gateway.dataset=data; gateway.family=row['family']; gateway.task=data.tasks[row['family']]
            gateway.arm=row['arm']; gateway.out=scratch/row['episode']; gateway.source=gateway.out/'candidate'
            gateway.source.mkdir(parents=True); gateway.edits=[]; gateway.checks=0
            base=data.bundle/'packages'/row['family']/'base'
            for name in gateway.task['allowed']+['tests/test_regression.py']:
                if (base/name).exists():
                    (gateway.source/name).parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(base/name,gateway.source/name)
            for event in journal:
                if event['call']['function']['name']!='apply_edits':continue
                operations+=1; expected=event['result']; args=None
                if row['arm']=='span' and expected.get('tool_error','').startswith(('SyntaxError:','IndentationError:')):
                    args=json.loads(event['call']['function']['arguments'])
                    grouped=defaultdict(list)
                    for edit in args['edits']:grouped[edit['path']].append(edit)
                    reproduced=None; details=[]
                    for name,edits in grouped.items():
                        path=gateway.source/name
                        text=path.read_text(encoding='utf-8') if path.exists() else None
                        if text is None:
                            assert len(edits)==1 and edits[0]['start_line']==edits[0]['end_line']==0
                            proposed=edits[0]['new']
                        else:
                            lines=text.splitlines(keepends=True); offsets=[0]
                            for line in lines:offsets.append(offsets[-1]+len(line))
                            # Independent forward character-offset rendering, unlike
                            # the implementation's reversed list-slice replacement.
                            pieces=[];cursor=0
                            for edit in sorted(edits,key=lambda e:e['start_line']):
                                first,end=edit['start_line'],edit['end_line']
                                assert 1<=first<=end<=len(lines)
                                assert offsets[first-1]>=cursor
                                pieces.extend([text[cursor:offsets[first-1]],edit['new']]);cursor=offsets[end]
                                details.append({'path':name,'start_line':first,'end_line':end,
                                    'original_first_line':lines[first-1].rstrip('\r\n')[:160],
                                    'new_first_line':edit['new'].splitlines()[0][:160] if edit['new'].splitlines() else '',
                                    'replacement_has_literal_backslash_n':'\\n' in edit['new'],
                                    'replacement_ends_with_newline':edit['new'].endswith(('\n','\r'))})
                            pieces.append(text[cursor:]);proposed=''.join(pieces)
                        try:compile(proposed,str(path),'exec')
                        except (SyntaxError,IndentationError) as exc:
                            reproduced=type(exc).__name__+': '+str(exc)
                            diagnoses.append({'episode':row['episode'],'family':row['family'],'round':event['round'],
                                'recorded_error':expected['tool_error'],'independently_reproduced_error':reproduced,
                                'proposed_source_sha256':hashlib.sha256(proposed.encode('utf-8')).hexdigest(),'spans':details})
                            break
                    assert reproduced==expected['tool_error'],(row['episode'],event['round'],reproduced,expected)
                try:
                    if args is None:args=json.loads(event['call']['function']['arguments'])
                    actual=gateway.call('apply_edits',args)
                except Exception as exc:actual={'tool_error':type(exc).__name__+': '+str(exc)}
                assert actual==expected,(row['episode'],event['round'],actual,expected)
                kind=actual.get('tool_error','successful').split(':',1)[0]
                counts[(row['arm'],kind)]+=1
            for name in gateway.task['allowed']+['tests/test_regression.py']:
                path=gateway.source/name; delivered=original/'candidate'/name
                current=path.read_text(encoding='utf-8') if path.exists() else None
                target=delivered.read_text(encoding='utf-8') if delivered.exists() else None
                assert current==target
    assert operations==166 and len(diagnoses)==49
    report={'decision':'PASS_NO_EDITOR_IMPLEMENTATION_MISMATCH_IDENTIFIED','edit_calls_reproduced':operations,
        'all_recorded_edit_results_match_scratch_execution':True,'all_final_allowed_source_texts_match_delivered_candidates':True,
        'span_compile_failures_independently_reconstructed_by_forward_character_offsets':len(diagnoses),
        'counts':[{'arm':arm,'kind':kind,'count':n} for (arm,kind),n in sorted(counts.items())],
        'diagnoses':diagnoses,'limits':['This reproduces input-to-editor behavior, not a causal explanation of final repair outcomes.',
            'The tested interface requires explicit replacement newlines and correct original line spans; these formatting requirements are part of the intervention.',
            'No result establishes that every location-aware editor is worse or that syntax rejections explain every failed repair.'],
        'new_api_calls':0,'candidate_code_executed_on_host':False}
    (OUT/'independent_edit_mechanics.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['diagnoses','limits']}))


if __name__=='__main__':main()
