"""Pure interaction state and bounded offscreen rendering; no background threads."""
import math
import random


def clamp(x,a,b):return max(a,min(b,x))
def palm_center(points):return tuple(sum(float(points[i][k]) for i in (0,5,9,13,17))/5. for k in (0,1))
def distance(a,b):return math.hypot(float(a[0]-b[0]),float(a[1]-b[1]))

class LayerScene:
    def __init__(self):self.layer=None;self.last=None;self.tracking=False
    def step(self,now):
        dt=min(.05,max(0.,now-self.last)) if self.last is not None else 1./30
        self.last=now;return dt
    def draw(self,cr,now,video=None):
        import cairo
        if self.layer is None:self.layer=cairo.ImageSurface(cairo.FORMAT_ARGB32,320,240)
        c=cairo.Context(self.layer);c.set_operator(cairo.OPERATOR_CLEAR);c.paint();c.set_operator(cairo.OPERATOR_OVER);c.scale(.5,.5)
        self.render(c,now,video)
        cr.save()
        bounds=self.paint_bounds() if hasattr(self,'paint_bounds') else None
        if bounds is not None:
            x,y,w,h=bounds;cr.rectangle(x,y,w,h);cr.clip()
        cr.scale(2.,2.);cr.set_source_surface(self.layer,0,0);cr.paint();cr.restore()
    @staticmethod
    def text(cr,text,x,y,size=18,color=(.9,.96,1.)):
        cr.set_source_rgb(*color);cr.select_font_face('Sans');cr.set_font_size(size);cr.move_to(x,y);cr.show_text(str(text));cr.new_path()
    @staticmethod
    def field(cr):
        cr.set_source_rgba(.02,.04,.09,.72);cr.rectangle(20,70,600,340);cr.fill()
        cr.set_source_rgba(.2,.85,1.,.7);cr.set_line_width(1.);cr.rectangle(20,70,600,340);cr.stroke()

class PinchScene(LayerScene):
    def __init__(self):super().__init__();self.reset()
    def reset(self):
        self.cx,self.cy,self.scale=320.,240.,1.;self.grab=False;self.zoom=None;self.sample=None
        self.tx,self.ty,self.target_scale=self.cx,self.cy,self.scale
        self.offset=(0.,0.);self.cursor=None;self.photo=None;self.span=None
        self.last_seen=None;self.release_since=None;self.zoom_seen=0.;self.input_time=float('-inf')
    @staticmethod
    def straight(p,a,b,c):
        u=(float(p[a][0]-p[b][0]),float(p[a][1]-p[b][1]))
        v=(float(p[c][0]-p[b][0]),float(p[c][1]-p[b][1]));den=math.hypot(*u)*math.hypot(*v)
        return den>1e-6 and (u[0]*v[0]+u[1]*v[1])/den<-.72
    def _zoom_pose(self,result):
        p=result['points'];valid=result.get('valid_points',[True]*21)
        if not(valid[8] and valid[12]):return False
        if result['label']=='胜利手势':return True
        return (self.straight(p,5,6,8) and self.straight(p,9,10,12)
                and not self.straight(p,13,14,16) and not self.straight(p,17,18,20))
    def update(self,result,now):
        dt=self.step(now)
        measurement=result.get('tracked_at',result.get('source_time',now)) if result is not None else now
        if result is not None and measurement>=self.input_time and result.get('sample_id')!=self.sample:
            elapsed=min(.3,max(.01,now-self.last_seen)) if self.last_seen is not None else .05
            self.sample=result.get('sample_id');self.input_time=measurement
            p=result['points'];valid=result.get('valid_points',[True]*21)
            can_drag=valid[4] and valid[8]
            can_zoom=self._zoom_pose(result)
            if not(can_drag or can_zoom):result=None
            else:
                self.last_seen=now;self.tracking=True
                mid=((float(p[4][0])+float(p[8][0]))/2.,(float(p[4][1])+float(p[8][1]))/2.)
                span=max(distance(p[0],p[9]),distance(p[5],p[17]),15.)
                a=1.-math.exp(-elapsed/.15);self.span=span if self.span is None else self.span+(span-self.span)*a
                ratio=distance(p[4],p[8])/self.span if can_drag else 1.
                if not can_drag:mid=((float(p[8][0])+float(p[12][0]))/2.,(float(p[8][1])+float(p[12][1]))/2.)
                inside=abs(mid[0]-self.cx)<120*self.scale+32 and abs(mid[1]-self.cy)<90*self.scale+32
                self.cursor=mid
                if self.grab:
                    if ratio>.78:
                        if self.release_since is None:self.release_since=now
                        if now-self.release_since>=.10:self.grab=False;self.release_since=None
                    else:self.release_since=None
                    if self.grab and can_drag:self.tx=mid[0]+self.offset[0];self.ty=mid[1]+self.offset[1]
                elif ratio<.44 and inside:
                    self.grab=True;self.zoom=None;self.release_since=None
                    self.offset=(self.cx-mid[0],self.cy-mid[1]);self.tx,self.ty=self.cx,self.cy
                if not self.grab:
                    if can_zoom:
                        spread=clamp(distance(p[8],p[12])/self.span,.12,2.5)
                        if self.zoom is None:self.zoom=(spread,self.scale)
                        desired=clamp(self.zoom[1]*spread/self.zoom[0],.5,1.8)
                        if abs(math.log(desired/self.target_scale))>.018:self.target_scale=desired
                        self.zoom_seen=now
                    elif self.zoom is not None and now-self.zoom_seen>.3:self.zoom=None
        if self.last_seen is None or now-self.last_seen>.45:
            self.tracking=False;self.grab=False;self.zoom=None;self.cursor=None;self.release_since=None
            self.tx,self.ty=self.cx,self.cy
        # Smooth between fresh tracked samples, rather than stepping at model rate.
        travel=math.hypot(self.tx-self.cx,self.ty-self.cy)
        alpha=1.-math.exp(-dt/(.025 if travel>25 else .045))
        self.cx+=(self.tx-self.cx)*alpha;self.cy+=(self.ty-self.cy)*alpha
        self.scale=math.exp(math.log(self.scale)+(math.log(self.target_scale)-math.log(self.scale))*(1.-math.exp(-dt/.07)))
        self.cx=clamp(self.cx,12+120*self.scale,628-120*self.scale)
        self.cy=clamp(self.cy,32+90*self.scale,448-90*self.scale)
        return True
    def paint_bounds(self):
        half_w,half_h=120*self.scale,90*self.scale
        xs=[self.cx-half_w-6,self.cx+half_w+12];ys=[self.cy-half_h-6,self.cy+half_h+12]
        if self.cursor is not None:
            xs.extend([self.cursor[0]-12,self.cursor[0]+12]);ys.extend([self.cursor[1]-12,self.cursor[1]+12])
        x0,x1=clamp(min(xs),0,640),clamp(max(xs),0,640)
        y0,y1=clamp(min(ys),0,480),clamp(max(ys),0,480)
        return x0,y0,max(0.,x1-x0),max(0.,y1-y0)
    def render(self,c,now,video):
        if self.photo is None and video is not None:
            import cairo
            self.photo=cairo.ImageSurface(cairo.FORMAT_RGB24,640,480);a=cairo.Context(self.photo);a.set_source_surface(video,0,0);a.paint()
        w,h=240*self.scale,180*self.scale;x,y=self.cx-w/2,self.cy-h/2
        c.set_source_rgba(0.,0.,0.,.35);c.rectangle(x+6,y+6,w,h);c.fill()
        c.save();c.rectangle(x,y,w,h);c.clip()
        if self.photo is not None:
            c.translate(x,y);c.scale(w/640.,h/480.);c.set_source_surface(self.photo,0,0);c.paint()
        else:c.set_source_rgb(.12,.25,.5);c.paint()
        c.restore()
        c.set_source_rgb(*((.2,1.,.65) if self.grab else (.8,.45,1.) if self.zoom is not None else (.3,.75,1.)))
        c.set_line_width(3);c.rectangle(x,y,w,h);c.stroke();self.text(c,'DRAG' if self.grab else 'ZOOM' if self.zoom is not None else 'PINCH TO GRAB',x+8,y+24,14)
        if self.cursor:
            c.set_source_rgb(.2,1.,.65) if self.grab else c.set_source_rgb(1.,.85,.2)
            c.arc(*self.cursor,9 if self.grab else 7,0,math.tau);c.stroke()
    def status(self):
        if self.grab:return '已抓住：移动手拖动卡片 · 张开拇指食指松开'
        if self.zoom is not None:return '缩放中：食指中指张大放大、靠拢缩小'
        return '拇指食指捏合抓取 · 比耶开合缩放 · 绿色表示已抓住'

class BrickGame(LayerScene):
    def __init__(self):super().__init__();self.reset()
    def reset(self):
        self.paddle=320.;self.target=320.;self.ball=[320.,365.];self.velocity=[65.,-105.]
        self.score=0;self.lives=3;self.level=1;self.running=False;self.wait_since=None
        self.bricks=[];self._bricks();self.trail=[];self.sample=None;self.pose='';self.seen=0.
    def _bricks(self):self.bricks=[[38+col*81,95+row*24,73,17,row] for row in range(4) for col in range(7)]
    def _serve(self,now):self.running=False;self.ball=[self.paddle,365.];self.velocity=[65.,-105.];self.wait_since=now;self.trail=[]
    def update(self,result,now):
        dt=self.step(now)
        if result is not None:
            self.tracking=True;self.seen=now;self.pose=result['label'];x=palm_center(result['points'])[0]
            self.target=clamp(70+(x-100)*500/440,72,568)
        else:self.tracking=False
        self.paddle+=(self.target-self.paddle)*(1-math.exp(-dt/0.07))
        if not self.tracking:return True
        if self.pose=='握拳':self.running=False;self.wait_since=None
        if not self.running:
            if self.ball[1]>360:self.ball=[self.paddle,365.]
            if self.pose=='张开手掌':
                if self.wait_since is None:self.wait_since=now
                if now-self.wait_since>.5:
                    if self.lives<=0:self.reset();self.seen=now;self.tracking=True
                    self.running=True;self.wait_since=None
            else:self.wait_since=None
            return True
        steps=max(1,int(math.ceil(dt/.008)));step=dt/steps
        for _ in range(steps):
            old=self.ball[:];self.ball[0]+=self.velocity[0]*step;self.ball[1]+=self.velocity[1]*step
            x,y=self.ball
            if x<27:self.ball[0]=27;self.velocity[0]=abs(self.velocity[0])
            if x>613:self.ball[0]=613;self.velocity[0]=-abs(self.velocity[0])
            if y<78:self.ball[1]=78;self.velocity[1]=abs(self.velocity[1])
            if self.velocity[1]>0 and old[1]<=379<=y+6 and abs(x-self.paddle)<59:
                self.ball[1]=372;offset=clamp((x-self.paddle)/52.,-1,1)
                speed=min(185,125+self.score*.08);self.velocity=[offset*speed*.85,-math.sqrt(max(1000,speed*speed-(offset*speed*.85)**2))]
            for brick in self.bricks:
                bx,by,bw,bh,row=brick
                if bx-5<=self.ball[0]<=bx+bw+5 and by-5<=self.ball[1]<=by+bh+5:
                    if old[1]<by-4 or old[1]>by+bh+4:self.velocity[1]*=-1
                    else:self.velocity[0]*=-1
                    self.bricks.remove(brick);self.score+=10;break
            if self.ball[1]>407:
                self.lives-=1;self._serve(now);break
            if not self.bricks:self.level+=1;self._bricks();self._serve(now);break
        self.trail.append(tuple(self.ball));self.trail=self.trail[-10:];return True
    def render(self,c,now,video):
        self.field(c);colors=[(.9,.3,.6),(.8,.45,1.),(.25,.7,1.),(.2,.9,.65)]
        for x,y,w,h,row in self.bricks:c.set_source_rgb(*colors[row]);c.rectangle(x,y,w,h);c.fill()
        for n,(x,y) in enumerate(self.trail):c.set_source_rgba(.2,.9,1.,n/max(1,len(self.trail))*.4);c.arc(x,y,3.,0,math.tau);c.fill()
        c.set_source_rgb(.2,.95,1.);c.rectangle(self.paddle-52,379,104,9);c.fill()
        c.set_source_rgb(1.,.95,.65);c.arc(*self.ball,6.,0,math.tau);c.fill()
        self.text(c,'SCORE %d   LIVES %d   LEVEL %d'%(self.score,self.lives,self.level),36,91,14)
        if not self.running or not self.tracking:self.text(c,'OPEN PALM TO PLAY' if self.tracking else 'SHOW YOUR HAND',205,270,20)
    def status(self):
        if self.lives<=0:return '游戏结束 · 张掌保持一秒重开'
        if not self.tracking:return '打砖块：伸入单手；手部丢失时自动暂停'
        return '张掌开始/继续 · 左右移动手掌接球 · 握拳暂停'

BODY_EDGES=[(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)]

def knee_angle(a,b,c):
    u=(a[0]-b[0],a[1]-b[1]);v=(c[0]-b[0],c[1]-b[1]);den=math.hypot(*u)*math.hypot(*v)
    return math.degrees(math.acos(clamp((u[0]*v[0]+u[1]*v[1])/den,-1,1))) if den>1e-6 else 180.

class BodyGame(LayerScene):
    def __init__(self):super().__init__();self.random=random.Random();self.reset()
    def reset(self):
        self.baseline=None;self.lane=1;self.x=320.;self.points=None;self.score=0;self.lives=3
        self.obstacles=[];self.running=False;self.spawn=0.;self.raised_since=None;self.raised_fired=False;self.duck=False
    def update(self,result,now):
        dt=self.step(now);self.tracking=False
        if result is None:self.points=None
        if result is not None:
            p=result['points'];self.points=p
            if all(float(p[i][2])>=.3 for i in (5,6,11,12)):
                self.tracking=True;hip=(float(p[11][0])+float(p[12][0]))/2.;width=max(25.,abs(float(p[5][0]-p[6][0])))
                if self.baseline is None:self.baseline=hip
                shift=(hip-self.baseline)/width;self.lane=0 if shift<-.45 else 2 if shift>.45 else 1
                raised=all(float(p[i][2])>.3 for i in (9,10)) and p[9][1]<p[5][1] and p[10][1]<p[6][1]
                self.duck=all(float(p[i][2])>.3 for i in (13,14,15,16)) and (knee_angle(p[11],p[13],p[15])+knee_angle(p[12],p[14],p[16]))/2.<140
                if raised:
                    if self.raised_since is None:self.raised_since=now
                    if now-self.raised_since>.5 and not self.raised_fired:
                        if self.lives<=0:self.reset();self.baseline=hip
                        self.running=not self.running;self.raised_fired=True
                else:self.raised_since=None;self.raised_fired=False
        if not self.tracking:self.raised_since=None;return True
        self.x+=([140.,320.,500.][self.lane]-self.x)*(1-math.exp(-dt/.1))
        if not self.running:return True
        self.spawn+=dt
        if self.spawn>=max(1.4,2.2-self.score/500.):
            self.spawn=0.;self.obstacles.append([self.random.randrange(3),80.,self.random.choice(['bar','wall','coin'])])
        keep=[]
        for lane,y,kind in self.obstacles:
            old=y;y+=dt*min(100.,60+self.score*.1)
            if old<370<=y and abs(self.x-[140,320,500][lane])<75:
                if kind=='coin':self.score+=20
                elif kind=='bar' and self.duck:self.score+=15
                else:self.lives-=1
                if self.lives<=0:self.running=False
                continue
            if y<410:keep.append([lane,y,kind])
            else:self.score+=5
        self.obstacles=keep;return True
    def render(self,c,now,video):
        self.field(c)
        for x in (230,410):c.set_source_rgba(.5,.7,.9,.2);c.move_to(x,75);c.line_to(x,405);c.stroke()
        if self.points is not None:
            c.set_source_rgba(.3,1.,.8,.7);c.set_line_width(2)
            for a,b in BODY_EDGES:
                if self.points[a][2]>.3 and self.points[b][2]>.3:c.move_to(float(self.points[a][0]),float(self.points[a][1]));c.line_to(float(self.points[b][0]),float(self.points[b][1]))
            c.stroke()
        for lane,y,kind in self.obstacles:
            x=[140,320,500][lane]
            if kind=='coin':c.set_source_rgb(1.,.85,.15);c.arc(x,y,13,0,math.tau);c.fill()
            else:
                c.set_source_rgb(*((.85,.3,1.) if kind=='bar' else (1.,.3,.35)));c.rectangle(x-55,y-9 if kind=='bar' else y-24,110,18 if kind=='bar' else 48);c.fill()
        c.set_source_rgb(.15,.95,1.);height=15 if self.duck else 35;c.rectangle(self.x-25,390-height,50,height);c.fill()
        self.text(c,'SCORE %d   LIVES %d'%(self.score,self.lives),35,93,16)
        if not self.running or not self.tracking:self.text(c,'RAISE BOTH HANDS' if self.tracking else 'STEP BACK / SHOW BODY',190,260,18)
    def status(self):
        if not self.tracking:return '人体游戏：后退使肩、髋、腿入镜，识别丢失时暂停'
        if self.lives<=0:return '游戏结束：举双手重开，或点击重开/校准'
        return '举双手开始/暂停 · 左右移身躲红块 · 下蹲躲紫杆 · 接金币'

class ScanScene(LayerScene):
    def __init__(self):super().__init__();self.reset()
    def reset(self):
        self.request_id=None;self.started=0.;self.results=None;self.best=None;self.photo=None;self.pixels=None
        self.frame=None;self.error='';self.saved='';self.label=''
    def start(self,frame,request_id,now):
        self.reset();self.frame=frame.copy();self.request_id=request_id;self.started=now
    def accept(self,results,label):
        self.results=results;self.best=None;self.error=''
        choices=[]
        for item in results:
            cls,score,x1,y1,x2,y2=item;cx,cy=(x1+x2)/2.,(y1+y2)/2.
            if not(160<=cx<=480 and 100<=cy<=380):continue
            inter=max(0.,min(x2,480)-max(x1,160))*max(0.,min(y2,380)-max(y1,100))
            area=max(1.,(x2-x1)*(y2-y1));choices.append((inter/area*score,item))
        if choices:self.best=max(choices,key=lambda v:v[0])[1];self.label=label(self.best[0])
    def update(self,result,now):
        self.step(now)
        if self.request_id is not None and self.results is None and now-self.started>8:self.error='扫描超时，请重试'
        return True
    def preview(self,live):
        if self.frame is None:return live
        if self.photo is None:
            import cairo
            self.pixels=bytearray(self.frame.tobytes());self.photo=cairo.ImageSurface.create_for_data(self.pixels,cairo.FORMAT_RGB24,640,480,640*4)
        return self.photo
    def render(self,c,now,video):
        c.set_source_rgba(0.,0.,0.,.35)
        for x,y,w,h in [(0,0,640,100),(0,380,640,100),(0,100,160,280),(480,100,160,280)]:c.rectangle(x,y,w,h)
        c.fill();c.set_source_rgb(.2,1.,.8);c.set_line_width(3);c.rectangle(160,100,320,280);c.stroke()
        if self.request_id is not None and (self.results is None or now-self.started<.8):
            y=100+((now-self.started)/.8%1)*280;c.set_source_rgba(.2,1.,.8,.8);c.move_to(160,y);c.line_to(480,y);c.stroke()
        if self.best is not None:
            _,score,x1,y1,x2,y2=self.best;c.set_source_rgb(1.,.85,.2);c.rectangle(x1,y1,x2-x1,y2-y1);c.stroke();self.text(c,'%.0f%%'%(score*100),x1,y1-7,18)
    def status(self):
        if self.error:return self.error
        if self.saved:return '识别卡片已保存'
        if self.request_id is None:return '扫描仪：物体放入中央框，点击扫描或按空格'
        if self.results is None:return '正在扫描…'
        if self.best is None:return '框内未识别到支持的物体，请调整位置后重试'
        return '扫描结果：%s %.0f%% · 保存卡片 / 重新取景'%(self.label,self.best[1]*100)
    def save(self,path):
        if self.best is None:raise ValueError('没有可保存的识别结果')
        import cairo
        from gi.repository import Pango,PangoCairo
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,640,560);c=cairo.Context(surface)
        c.set_source_rgb(.03,.06,.1);c.paint();c.set_source_surface(self.preview(None),0,0);c.paint()
        _,score,x1,y1,x2,y2=self.best;c.set_source_rgb(.2,1.,.8);c.set_line_width(3);c.rectangle(x1,y1,x2-x1,y2-y1);c.stroke()
        c.set_source_rgb(.03,.06,.1);c.rectangle(0,480,640,80);c.fill()
        layout=PangoCairo.create_layout(c);layout.set_font_description(Pango.FontDescription('Noto Sans CJK SC 18'));layout.set_text('%s  %.0f%%'%(self.label,score*100),-1)
        c.set_source_rgb(1.,1.,1.);c.move_to(18,498);PangoCairo.show_layout(c,layout)
        surface.write_to_png(str(path));self.saved=str(path)
