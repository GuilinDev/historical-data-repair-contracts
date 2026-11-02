"""Render the complete manuscript, embedding the supplied figures as vector PDF."""
import argparse
import html
import json
from pathlib import Path
import re


def render(source, output, embedded_fonts=False):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, KeepTogether, Spacer, PageBreak, Flowable, Preformatted
    from pypdf import PdfReader, PdfWriter, Transformation
    content=json.loads((source/'final_content.json').read_text(encoding='utf-8'))
    normal,bold,italic='Times-Roman','Times-Bold','Times-Italic'
    if embedded_fonts:
        for name,path in [('Paper','C:/Windows/Fonts/times.ttf'),('PaperBold','C:/Windows/Fonts/timesbd.ttf'),('PaperItalic','C:/Windows/Fonts/timesi.ttf')]:
            pdfmetrics.registerFont(TTFont(name,path))
        normal,bold,italic='Paper','PaperBold','PaperItalic'
    width=496.8
    body=ParagraphStyle('Body',fontName=normal,fontSize=10.5,leading=13.25,spaceAfter=6.0)
    small=ParagraphStyle('Caption',parent=body,fontSize=8.6,leading=10.7,spaceAfter=8)
    head=ParagraphStyle('Head',parent=body,fontName=bold,fontSize=12,leading=15,spaceBefore=5,spaceAfter=7,keepWithNext=True)
    title=ParagraphStyle('Title',parent=body,fontName=bold,fontSize=17.8,leading=20.5,alignment=1,spaceAfter=10)
    author=ParagraphStyle('Author',parent=body,alignment=1,spaceAfter=10)
    subtitle=ParagraphStyle('Subtitle',parent=body,fontName=italic,fontSize=11.6,leading=14,alignment=1,spaceAfter=9)
    reference=ParagraphStyle('Reference',parent=body,fontSize=9.25,leading=11.6,spaceAfter=7,leftIndent=20,firstLineIndent=-20)
    cell=ParagraphStyle('Cell',parent=body,fontSize=8.15,leading=10.0,spaceAfter=0)
    cellcenter=ParagraphStyle('CellCenter',parent=cell,alignment=1)
    cellhead=ParagraphStyle('CellHead',parent=cell,fontName=bold,alignment=1)
    figure_positions=[];table_positions=[]
    def p(text,style=body):
        marked=html.escape(str(text)).replace('\n','<br/>')
        for lead in ['Upstream-test comparison.','Numeric ambiguity.','Authorization ambiguity.','Original baseline.','Controlled interface comparison.','Resource card.','Limits.']:
            if marked.startswith(lead):marked='<font name="'+bold+'">'+lead+'</font>'+marked[len(lead):]
        marked=re.sub(r'\bE_([pq])\b',r'E<sub>\1</sub>',marked)
        marked=re.sub(r'\bx_(32|64)\b',r'x<sub>\1</sub>',marked)
        marked=re.sub(r'https://[^\s<>]+',lambda m:'<link href="'+m.group()+'" color="#1E4557">'+m.group()+'</link>',marked)
        return Paragraph(marked,style)
    class VectorFigure(Flowable):
        def __init__(self,fig):
            super().__init__();self.fig=fig;self.width=width;self.height=width*fig['height_in']/fig['width_in']
        def draw(self):
            x,y=self.canv.absolutePosition(0,0)
            figure_positions.append({'page':self.canv.getPageNumber()-1,'x':x,'y':y,'width':self.width,'height':self.height,'file':self.fig['pdf']})
    class AuditTable(Table):
        def draw(self):
            x,y=self.canv.absolutePosition(0,0)
            table_positions.append({'page':self.canv.getPageNumber()-1,'x':x,'y':y,'width':self._width,'height':self._height})
            super().draw()
    def fig(index):
        f=content['figures'][index]
        return KeepTogether([Spacer(1,4),VectorFigure(f),p(f['caption'],small)])
    def table():
        rows=content['tables'][0]
        values=[]
        for ri,row in enumerate(rows):
            values.append([p(x,cellhead if ri<2 else (cellcenter if ci>=3 else cell)) for ci,x in enumerate(row)])
        tab=AuditTable(values,colWidths=[69,94,90,38,49,39,51,33,33],repeatRows=2,hAlign='LEFT')
        tab.setStyle(TableStyle([
            ('SPAN',(0,0),(4,0)),('SPAN',(5,0),(6,0)),('SPAN',(7,0),(8,0)),
            ('LINEABOVE',(0,0),(-1,0),.9,colors.black),('LINEBELOW',(0,0),(-1,0),.35,colors.HexColor('#777777')),
            ('LINEBELOW',(0,1),(-1,1),.7,colors.black),
            ('LINEABOVE',(0,-1),(-1,-1),.7,colors.black),('LINEBELOW',(0,-1),(-1,-1),.9,colors.black),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
            ('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),
            ('BACKGROUND',(7,2),(-1,-2),colors.HexColor('#F2F5F4')),
        ]))
        for index in content['table_group_rows']:
            tab.setStyle(TableStyle([('SPAN',(0,index),(-1,index)),('BACKGROUND',(0,index),(-1,index),colors.HexColor('#E9E9E9')),
                                     ('TOPPADDING',(0,index),(-1,index),2),('BOTTOMPADDING',(0,index),(-1,index),2),
                                     ('LINEABOVE',(0,index),(-1,index),.35,colors.HexColor('#777777'))]))
        return KeepTogether([p(content['captions'][0],small),tab,Spacer(1,5),p(content['table_notes'][0],small)])
    def contract():
        item=content['contract_example']
        explanation=ParagraphStyle('ExampleBody',parent=body,fontSize=9.1,leading=11.3,spaceAfter=0)
        code=ParagraphStyle('ExampleCode',fontName='Courier',fontSize=7.9,leading=10,spaceAfter=0)
        panel=Table([[p(item['explanation'],explanation),'',Preformatted('\n'.join(item['code']),code)]],colWidths=[227,12,257])
        panel.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),
                                  ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),5),
                                  ('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#F4F4F4'))]))
        label=ParagraphStyle('ExampleHead',parent=body,fontName=bold,fontSize=10.1,leading=12,spaceAfter=3)
        return KeepTogether([p(item['title'],label),panel,Spacer(1,4),p(item['caption'],small)])
    def calibration_table():
        item=content['calibration_table']
        rows=[[p(x,cellhead) for x in item['headers']]]+[[p(x,cell if i==0 else cellcenter) for i,x in enumerate(row)] for row in item['rows']]
        tab=AuditTable(rows,colWidths=[208.8,96,96,96],hAlign='LEFT')
        tab.setStyle(TableStyle([('LINEABOVE',(0,0),(-1,0),.8,colors.black),('LINEBELOW',(0,0),(-1,0),.5,colors.black),
            ('LINEBELOW',(0,-1),(-1,-1),.8,colors.black),('BACKGROUND',(0,2),(-1,2),colors.HexColor('#EDF4F1')),
            ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
        return KeepTogether([Spacer(1,3),p(item['caption'],small),tab,Spacer(1,5)])
    story=[p(content['title'],title),p(content['subtitle'],subtitle),p(content['author'],author),p('Abstract',head),p(content['abstract'])]
    for idx,section in enumerate(content['sections']):
        if idx in [1,2,3,4]:story.append(PageBreak())
        story.append(p(section['title'],head))
        for j,paragraph in enumerate(section['paragraphs']):
            story.append(p(paragraph))
            if idx==0 and j==1:story.append(fig(0))
            if idx==1 and j==0:story.extend([table(),contract()])
            if idx==2 and j==2:story.append(fig(1))
            if idx==3 and j==2:story.append(fig(2))
            if idx==2 and j==len(section['paragraphs'])-1:story.append(calibration_table())
    story.append(p(content['conclusion']['title'],head))
    for point in content['conclusion']['points']:
        story.append(p('• '+point))
    story.extend([p('Disclosure',head),p(content['disclosure'],small),PageBreak(),p('References',head)])
    story.extend(p(r,reference) for r in content['references'])
    story.extend([p('Software acknowledgment',head),p(content['software_acknowledgment'],small)])
    output.parent.mkdir(parents=True,exist_ok=True)
    intermediate=output.with_name(output.stem+'.text-layer.pdf')
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont(normal,9);canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawCentredString(306,26,str(doc.page));canvas.restoreState()
    doc=SimpleDocTemplate(str(intermediate),pagesize=(612,792),leftMargin=57.6,rightMargin=57.6,
                          topMargin=38,bottomMargin=40,title=content['title'],author=content['author'])
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    reader=PdfReader(intermediate);writer=PdfWriter()
    for index,page in enumerate(reader.pages):
        for item in figure_positions:
            if item['page']!=index:continue
            fp=PdfReader(source/item['file']).pages[0]
            transform=Transformation().scale(item['width']/float(fp.mediabox.width),item['height']/float(fp.mediabox.height)).translate(item['x'],item['y'])
            page.merge_transformed_page(fp,transform)
        writer.add_page(page)
    writer.add_metadata({'/Title':content['title'],'/Author':content['author'],'/Subject':'Eight historical-data repair contracts and completed Gemma evaluation'})
    with output.open('wb') as stream:writer.write(stream)
    intermediate.unlink()
    receipt={'pages':len(writer.pages),'figure_positions':figure_positions,'table_positions':table_positions,
             'figures_are_vector_pdf':True,'embedded_body_fonts':embedded_fonts,'api_calls':0}
    (output.parent/(output.stem+'.layout.json')).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'manuscript-rerender.pdf')
    args=parser.parse_args()
    print(json.dumps(render(Path(__file__).resolve().parent,args.output)))


if __name__=='__main__':main()
