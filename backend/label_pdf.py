"""A4 4x7 label PDF, adjustable Formtec 3990 profile."""
import base64, io, math, os, re
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.graphics import renderPDF
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

DEFAULTS={'left':20.5,'top':7,'pitch_x':43,'pitch_y':40,'offset_x':0,'offset_y':0}
def positions(count,start,settings):
    start=int(start)
    if not 1<=start<=28:raise ValueError('시작 칸은 1~28입니다.')
    p={k:float(settings.get(k,v)) for k,v in DEFAULTS.items()}
    if not all(math.isfinite(v) for v in p.values()):raise ValueError('유효한 위치 값을 입력하세요.')
    if p['pitch_x']<40 or p['pitch_y']<40:raise ValueError('라벨 간격은 40mm 이상이어야 합니다.')
    left=p['left']+p['offset_x'];top=p['top']+p['offset_y']
    if left<0 or top<0 or left+3*p['pitch_x']+40>210 or top+6*p['pitch_y']+40>297:raise ValueError('라벨 위치가 A4 용지를 벗어납니다.')
    return [( (start-1+i)//28, (start-1+i)%28,
              left+((start-1+i)%28)%4*p['pitch_x'],
              top+((start-1+i)%28)//4*p['pitch_y']) for i in range(count)]
def make_pdf(assets,base_url,logo_path,options):
    if not assets or len(assets)>500:raise ValueError('자산 1~500개를 선택하세요.')
    places=positions(len(assets),options.get('start',1),options)
    font='Helvetica-Bold'
    candidates=[os.environ.get('LABEL_FONT',''),'C:/Windows/Fonts/malgun.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            if 'LabelFont' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('LabelFont',candidate))
            font='LabelFont';break
    logo=None
    if Path(logo_path).exists():
        match=re.search(r'xlink:href="data:image/[^;]+;base64,([^"]+)"',Path(logo_path).read_text('utf-8'))
        if match:logo=ImageReader(io.BytesIO(base64.b64decode(match.group(1))))
    out=io.BytesIO();c=canvas.Canvas(out,pagesize=A4,pageCompression=1);c.setTitle('Bumjin QRP-3990 labels')
    page=-1
    for a,(current,slot,x,top) in zip(assets,places):
        if current!=page:
            if page!=-1:c.showPage()
            page=current
            if options.get('guides'):
                guide_options={**options,'start':1}
                for _,_,gx,gy in positions(28,1,guide_options):
                    c.setStrokeColorRGB(.7,.7,.7);c.setLineWidth(.3);c.rect(gx*mm,A4[1]-(gy+40)*mm,40*mm,40*mm)
                c.setFillColorRGB(0,0,0)
        x*=mm;y=A4[1]-(top+40)*mm
        url=base_url.rstrip('/')+'/a/'+a['id']
        q=QrCodeWidget(url,barLevel='H',barBorder=4);x0,y0,x1,y1=q.getBounds()
        size=32.3*mm;drawing=Drawing(size,size,transform=[size/(x1-x0),0,0,size/(y1-y0),0,0])
        drawing.add(Rect(0,0,x1-x0,y1-y0,fillColor=__import__('reportlab.lib.colors',fromlist=['white']).white,strokeColor=None));drawing.add(q)
        qx=x+(40*mm-size)/2;qy=y+40*mm-1.95*mm-size
        renderPDF.draw(drawing,c,qx,qy)
        if logo:
            c.setFillColorRGB(1,1,1);c.roundRect(qx+49/240*size,qy+102/240*size,142/240*size,36/240*size,.25*mm,stroke=0,fill=1)
            c.drawImage(logo,qx+52/240*size,qy+105/240*size,136/240*size,30/240*size,preserveAspectRatio=True,anchor='c',mask='auto')
        text=str(a['number']);fontsize=10.45
        while pdfmetrics.stringWidth(text,font,fontsize)>36.1*mm and fontsize>4:fontsize-=.25
        if pdfmetrics.stringWidth(text,font,fontsize)>36.1*mm:raise ValueError('자산코드가 라벨에 표시하기 너무 깁니다.')
        c.setFillColorRGB(0,0,0);c.setFont(font,fontsize);c.drawCentredString(x+20*mm,y+3.2*mm,text)
    c.save();return out.getvalue()
