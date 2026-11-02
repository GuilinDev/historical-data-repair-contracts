"""Observed counts for the complete frozen 64-attempt campaign; no sampling."""
from pathlib import Path
import json,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pypdf import PdfReader
HERE=Path(__file__).resolve().parent

def main():
    data=json.loads((HERE/'figure_data.json').read_text(encoding='utf-8'))
    labels=[r['lineage'] for r in data['rows']];tasks=[r['task'] for r in data['rows']]
    with plt.rc_context({'font.family':'DejaVu Sans','font.size':9,'pdf.fonttype':42,'svg.fonttype':'path','svg.hashsalt':'hdr-v012','text.color':'#202020','figure.facecolor':'white'}):
        fig,axes=plt.subplots(1,2,figsize=(6.9,2.9),sharey=True);fig.subplots_adjust(left=.235,right=.97,bottom=.18,top=.84,wspace=.27)
        for ax,(arm,color,marker,label) in zip(axes,[('exact','#005A86','o','Exact text: 10/32'),('span','#9D4319','s','Version-checked span: 4/32')]):
            vals=[data['controlled'][t][arm] for t in tasks];ys=[7-i for i in range(8)]
            ax.scatter(vals,ys,s=36,c=color,marker=marker,label=label,zorder=3)
            for x,y in zip(vals,ys):ax.text(x+.11,y,f'{x}/4',va='center',fontsize=8.2,color=color)
            ax.set_yticks(list(range(7,-1,-1)),labels);ax.tick_params(axis='y',length=0,pad=9)
            ax.set_xlim(-.12,4.7);ax.set_xticks(range(5));ax.set_ylim(-.5,7.6)
            ax.set_title(label,color=color,fontsize=9.3,pad=8)
            ax.grid(axis='x',color='#DDDDDD',linewidth=.6);ax.set_axisbelow(True)
            ax.spines[['top','right','left']].set_visible(False);ax.spines['bottom'].set_color('#888888')
        fig.text(.59,.04,'Complete repairs (four attempts per interface and lineage)',ha='center',fontsize=9)
        name='figure3_controlled'
        for ext in ['pdf','svg','png']:
            meta={'Creator':'Historical-Data Repair Contracts','CreationDate':None,'ModDate':None} if ext=='pdf' else ({'Date':None} if ext=='svg' else {})
            fig.savefig(HERE/'figures'/f'{name}.{ext}',dpi=600,transparent=False,facecolor='white',metadata=meta)
        plt.close(fig)
    pdf=HERE/'figures'/f'{name}.pdf'
    f={'id':name,'pdf':f'figures/{name}.pdf','png':f'figures/{name}.png','svg':f'figures/{name}.svg','width_in':6.9,'height_in':2.9,
       'caption':'Figure 3. Complete repair acceptance in all 64 new attempts. Each point counts four conversations on one lineage with the same Gemma 4 31B deployment. Circles: exact text, 10/32 total; squares: version-checked spans, 4/32. Repetitions are nested in eight selected lineages; these are observed counts, not a general editor ranking.',
       'alt':'Eight paired rows show exact-text versus version-checked-span complete repairs out of four. packaging0/0, attrs2/0, CSV2/0, SQLAlchemy3/3, endian1/1, compressed0/0, dtype0/0, pandas2/0. Zero is shown explicitly; no missing attempts.',
       'label_text':PdfReader(pdf).pages[0].extract_text(),'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
       'transformations':'All64attempts included. Counts of actual source changes passing complete contracts and source review. No smoothing, normalization, omitted attempts, or inferential intervals. Underlying data and nested replicate structure retained.'}
    p=json.loads((HERE/'final_content.json').read_text(encoding='utf-8'));p['figures']=p['figures'][:2]+[f]
    (HERE/'final_content.json').write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    (HERE/'figure_manifest.json').write_text(json.dumps({'figures':p['figures'],'data_sha256':hashlib.sha256((HERE/'figure_data.json').read_bytes()).hexdigest()},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
