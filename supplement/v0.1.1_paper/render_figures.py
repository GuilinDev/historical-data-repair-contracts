"""Static publication figures. All plotted outcomes come from figure_data.json."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Patch
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
OUT = HERE / 'figures'
INK='#202020'; BLUE='#005A86'; TEAL='#006B55'; GRAY='#666666'


def main():
    data=json.loads((HERE/'figure_data.json').read_text(encoding='utf-8'))
    OUT.mkdir(exist_ok=True)
    figures=[]
    settings={'font.family':'DejaVu Sans','font.size':9, 'axes.labelsize':9,'axes.titlesize':10,
              'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'path','svg.hashsalt':'historical-data-repair-contracts','figure.facecolor':'white',
              'axes.facecolor':'white','savefig.facecolor':'white','text.color':INK,'axes.labelcolor':INK}
    def save(fig, name, caption, alt):
        for ext in ['pdf','svg','png']:
            metadata=({'Creator':'Historical-Data Repair Contracts','CreationDate':None,'ModDate':None} if ext=='pdf' else
                      {'Creator':'Historical-Data Repair Contracts','Date':None} if ext=='svg' else {'Software':'Historical-Data Repair Contracts'})
            fig.savefig(OUT/(name+'.'+ext),dpi=600,facecolor='white',transparent=False,metadata=metadata)
        text=PdfReader(OUT/(name+'.pdf')).pages[0].extract_text()
        record={'id':name,'pdf':'figures/'+name+'.pdf','png':'figures/'+name+'.png','svg':'figures/'+name+'.svg',
                'width_in':float(fig.get_figwidth()),'height_in':float(fig.get_figheight()),'caption':caption,'alt':alt,
                'label_text':text, 'pdf_sha256':hashlib.sha256((OUT/(name+'.pdf')).read_bytes()).hexdigest(),
                'transformations':'No smoothing, uncertainty interval, sampling, or omitted attempts. Exact counts and source identities retained.'}
        figures.append(record);plt.close(fig)
    with plt.rc_context(settings):
        # A measured non-injective encoding, not an illustrative synthetic format.
        fig=plt.figure(figsize=(6.9,1.95));ax=fig.add_axes([.005,.03,.99,.94]);ax.set(xlim=(0,10),ylim=(0,3.1));ax.axis('off')
        def box(x,y,w,h,head,body,edge=GRAY):
            ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.04,rounding_size=.08',facecolor='white',edgecolor=edge,lw=1.0))
            ax.text(x+w/2,y+h-.23,head,ha='center',va='top',fontsize=9.4,fontweight='bold',color=edge)
            ax.text(x+w/2,y+.18,body,ha='center',va='bottom',fontsize=8.5,linespacing=1.3)
        box(.10,1.75,2.00,1.12,'State 1: float32','x = [1.125, -3.25]',BLUE)
        box(.10,.18,2.00,1.12,'State 2: float64','x = [1.125, -3.25]',BLUE)
        box(3.08,.84,2.55,1.38,'Identical saved bytes','jsonpickle 4.1.1; tag pd/f\n267 bytes\nSHA-256: 2f4332f63357...',INK)
        for y in [2.27,.74]:
            ax.annotate('',xy=(3.03,1.53),xytext=(2.17,y),arrowprops={'arrowstyle':'->','color':GRAY,'lw':1.1})
        ax.text(2.58,2.84,'encode',ha='center',fontsize=8.3)
        ax.annotate('',xy=(6.30,1.53),xytext=(5.69,1.53),arrowprops={'arrowstyle':'->','color':GRAY,'lw':1.1})
        ax.text(8.08,2.57,'No unique original width',ha='center',fontsize=10,fontweight='bold')
        ax.text(8.08,1.67,'Preserve values and numeric kind.\nWiden when width is ambiguous.\nKeep explicit widths exact.',ha='center',va='center',fontsize=9,linespacing=1.7)
        ax.text(8.08,.54,'One file-only reader sees the same input.',ha='center',fontsize=8.1,color=GRAY)
        save(fig,'figure1_collision','Figure 1. Two historical producer states yield the same complete document. Each state self-roundtrips with its original width, but the saved bytes cannot distinguish them. The pair was found in six targeted producer executions; this is an existence witness, not a collision-rate estimate.',
             'A float32 frame and float64 frame with column x and values 1.125 and -3.25 both produce the identical 267-byte old JSON document. A reader cannot uniquely infer the original width from that document. Values and numeric kind remain mandatory; ambiguous widths may widen.')

        fig=plt.figure(figsize=(6.9,2.35));ax=fig.add_axes([.31,.23,.61,.60])
        policies=data['calibration']; ys=[2,1,0]
        ax.barh([y+.17 for y in ys],[r['admissible'] for r in policies],height=.29,color=TEAL,label='Admissible contract')
        ax.barh([y-.17 for y in ys],[r['exact'] for r in policies],height=.29,color='white',edgecolor=BLUE,hatch='///',linewidth=.9,label='Literal reference equality')
        for y,r in zip(ys,policies):
            for off,key in [(.17,'admissible'),(-.17,'exact')]:
                ax.text(r[key]+1.4,y+off,str(r[key])+'/84',va='center',fontsize=8.7)
        ax.set_yticks(ys,[r['policy'] for r in policies]);ax.tick_params(axis='y',length=0,pad=9)
        ax.set_xlim(0,100);ax.set_xticks([0,24,48,72,84]);ax.set_xlabel('Observations satisfying the criterion (of 84)')
        ax.spines[['top','right','left']].set_visible(False);ax.spines['bottom'].set_color('#999999');ax.tick_params(axis='x',color='#999999')
        ax.set_axisbelow(True);ax.grid(axis='x',color='#DDDDDD',linewidth=.5)
        fig.legend(handles=[Patch(facecolor=TEAL,label='Admissible contract'),Patch(facecolor='white',edgecolor=BLUE,hatch='///',label='Literal reference equality')],
                   loc='upper center',bbox_to_anchor=(.57,1.00),ncol=2,frameon=False,fontsize=8.8)
        save(fig,'figure2_calibration','Figure 2. Complete source-repair controls on the numeric-code lineage. All three load and restart 84/84 observations. The alternate valid repair differs from literal reference output on 60 observations (five old files, twelve processes). These are researcher-built controls; literal equality is not the upstream test suite.',
             'Paired horizontal bars start at zero. Conservative backport: 84 of 84 admissible and exact. Document exclusion v2: 84 of 84 admissible, 24 of 84 exact. Explicit-width narrowing: zero under both criteria. No uncertainty intervals because these are complete fixed-corpus counts, not independent samples.')

        fig=plt.figure(figsize=(6.9,2.95));ax=fig.add_axes([.005,.18,.99,.80]);ax.set(xlim=(0,7),ylim=(-.28,9.0));ax.axis('off')
        xs=[2.50,3.65,4.96,6.11];cellw=1.04;cellh=.72
        ax.text(3.075,8.65,'A: 26B A4B / BF16',ha='center',fontsize=9.2,fontweight='bold')
        ax.text(5.535,8.65,'B: 31B / FP4',ha='center',fontsize=9.2,fontweight='bold')
        for x,rep in zip(xs,[1,2,1,2]):ax.text(x,8.13,'Attempt '+str(rep),ha='center',fontsize=8.5)
        for idx,row in enumerate(data['rows']):
            y=7.45-idx*.91
            ax.text(.05,y,row['lineage'],ha='left',va='center',fontsize=9)
            group=[r for r in data['episodes'] if r['family']==row['task']]
            for x,r in zip(xs,group):
                ax.add_patch(Rectangle((x-cellw/2,y-cellh/2),cellw,cellh,facecolor=TEAL if r['accepted'] else '#F3F3F3',edgecolor='white',lw=1))
                flags=('PASS' if r['accepted'] else ('U' if r['unchanged'] else 'E'))+(' C' if r['context_stop'] else '')
                label=f'{r["passed"]}/{r["n"]} '+flags
                ax.text(x,y,label,ha='center',va='center',fontsize=8.6,color='white' if r['accepted'] else INK,fontweight='bold' if r['accepted'] else 'normal')
        ax.plot([.03,6.70],[4.26,4.26],color='#777777',lw=.6,ls='--')
        fig.text(.015,.080,'PASS: semantic + source acceptance; U: unchanged; E: edited, incomplete repair.',fontsize=8.5)
        fig.text(.015,.025,'C: exploration context bound; it can accompany U, E or PASS.',fontsize=8.5)
        save(fig,'figure3_attempts','Figure 3. Every completed Gemma conversation, with passed/declared observations from the original final evaluation. The dashed line separates development-exposed lineages (above) from newly added lineages (below). Counts are dependent coverage, not partial task successes. Providers and precision differ between A and B.',
             'Eight rows and four attempt columns show all 32 completed model attempts. Only four cells pass the entire contract: both B joblib endian attempts, one A joblib compressed attempt, and one B jsonpickle CSV attempt. Sixteen leave source unchanged and fifteen hit the exploration context bound. Full data and episode identities accompany the figure.')
    manifest={'figures':figures,'data_sha256':hashlib.sha256((HERE/'figure_data.json').read_bytes()).hexdigest(),
              'matplotlib_version':matplotlib.__version__,'dpi':600,'pdf_fonttype':42,
              'audience':'Gemma paper track manuscript; final width 6.9 inches; no publisher-specific style inferred',
              'independence':'8 defect lineages in 6 projects; repeated files/checks/conversations are dependent',
              'uncertainty':'No intervals: complete descriptive fixed-resource counts; no sampling inference',
              'accessibility':'Direct labels, PASS text, U/C flags and hatching supplement color; alt text and source data included',
              'software_acknowledgement':'Scientific Agent Skills, Kassis et al. (2026), https://doi.org/10.48550/arXiv.2609.00065'}
    (HERE/'figure_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'figures':len(figures),'formats':['PDF','SVG','PNG'],'dpi':600,'api_calls':0}))


if __name__=='__main__': main()
