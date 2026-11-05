"""Bounded, model-free drawing and palm particles in camera coordinates."""
import math
import random
from interaction_games import PinchScene, BrickGame, BodyGame, ScanScene

PALETTE = [(0.15,.95,1.),(.95,.3,.9),(.6,.45,1.),(1.,.78,.2),(.3,1.,.55)]

class VisualDemo:
    def __init__(self):
        self.mode='normal';self.color=0;self.scene=None
        self.surface=None;self.particle_surface=None;self.glow_masks={};self.pending=[];self.clear_pending=True;self.points_drawn=0
        self.cursor=None;self.target=None;self.pen=False
        self.pen_seen=None;self.pen_release=None
        self.center=None;self.radius=45.;self.particles=[];self.credit=0.
        self.last_tick=None;self.last_sample=None;self.pose='';self.pose_since=0.;self.fired=False
        self.tracking=False;self.enabled=True;self.error='';self.notice='';self.notice_until=0.
        self.random=random.Random()

    @property
    def active(self):return self.mode!='normal'

    def set_mode(self,mode):
        if mode not in ('normal','paint','particles','pinch','bricks','scanner','body'):raise ValueError('unknown demo mode')
        self.mode=mode;self.clear();self.particles.clear();self.center=None
        self.last_tick=None;self.last_sample=None;self.pose='';self.fired=False
        self.tracking=False;self.notice=''
        classes={'pinch':PinchScene,'bricks':BrickGame,'body':BodyGame,'scanner':ScanScene}
        self.scene=classes[mode]() if mode in classes else None

    def clear(self):
        self.pending.clear();self.clear_pending=True;self.points_drawn=0
        self.cursor=self.target=None;self.pen=False
        self.pen_seen=self.pen_release=None

    @staticmethod
    def _index_pose(points):
        def extended(base):
            a,b,c=points[base],points[base+1],points[base+3]
            u=[float(a[i]-b[i]) for i in range(3)]
            v=[float(c[i]-b[i]) for i in range(3)]
            length=math.sqrt(sum(x*x for x in u)*sum(x*x for x in v))
            tip=sum(float(c[i]-points[0][i])**2 for i in range(3))
            joint=sum(float(b[i]-points[0][i])**2 for i in range(3))
            return length>1e-6 and sum(x*y for x,y in zip(u,v))/length<-.75 and tip>1.2*joint
        return extended(5) and not any(extended(base) for base in (9,13,17))

    def _update_pen(self,result,now):
        label=result['label'];points=result['points']
        # Bridge the model's brief label-stability blanks only when geometry agrees.
        drawing=label=='食指向上' or (not label and self.pen and self._index_pose(points))
        if drawing:
            target=(max(0.,min(639.,float(points[8][0]))),max(0.,min(479.,float(points[8][1]))))
            gap=now-self.pen_seen if self.pen_seen is not None else 0.
            jump=math.hypot(target[0]-self.cursor[0],target[1]-self.cursor[1]) if self.cursor is not None else 0.
            limit=min(320.,80.+1000.*max(.05,gap))
            if not self.pen or self.cursor is None or gap>.65 or jump>limit:self.cursor=target
            self.pen=True;self.target=target;self.pen_seen=now;self.pen_release=None
        elif label:
            if self.pen_release is None:self.pen_release=now
            if now-self.pen_release>=.18:
                self.pen=False;self.cursor=self.target=None
        else:self.pen_release=None

    def update(self,result,enabled,error,now):
        if not self.active:return False
        self.enabled=enabled;self.error=error
        if self.last_tick is not None and now-self.last_tick<1./30:return False
        dt=min(.08,max(0.,now-self.last_tick)) if self.last_tick is not None else 1./30
        self.last_tick=now
        if self.scene is not None:
            return self.scene.update(result if enabled and not error else None,now)
        self.tracking=bool(enabled and not error and result is not None)
        if not self.tracking:
            if self.mode!='paint' or not enabled or error:
                self.pen=False;self.cursor=self.target=None
            self.pose='';self.last_sample=None
        elif result.get('sample_id')!=self.last_sample:
            self.last_sample=result.get('sample_id');label=result['label'];points=result['points']
            if label!=self.pose:
                self.pose=label;self.pose_since=now;self.fired=False
            if label=='胜利手势' and now-self.pose_since>=.55 and not self.fired:
                self.color=(self.color+1)%len(PALETTE);self.fired=True
                self.notice='已切换颜色';self.notice_until=now+1.3
            if self.mode=='paint':
                if label=='张开手掌' and now-self.pose_since>=.8 and not self.fired:
                    self.clear();self.fired=True;self.notice='画布已清空';self.notice_until=now+1.3
                self._update_pen(result,now)
            else:
                center=tuple(sum(float(points[i][axis]) for i in (0,5,9,13,17))/5. for axis in (0,1))
                center=(max(0.,min(639.,center[0])),max(0.,min(479.,center[1])))
                if self.center is None:self.center=center
                else:self.center=tuple(a*.45+b*.55 for a,b in zip(self.center,center))
                self.radius=max(22.,min(70.,math.hypot(float(points[9][0]-points[0][0]),float(points[9][1]-points[0][1]))*.7))
        if self.mode=='paint':
            if self.pen_seen is None or now-self.pen_seen>.65:
                self.pen=False;self.cursor=self.target=None
            if self.pen and self.target is not None:
                previous=self.cursor
                a=1.-math.exp(-dt/0.065)
                self.cursor=tuple(x+(y-x)*a for x,y in zip(self.cursor,self.target))
                if len(self.pending)<512 and math.hypot(self.cursor[0]-previous[0],self.cursor[1]-previous[1])>.1:
                    self.pending.append((previous,self.cursor,PALETTE[self.color]));self.points_drawn+=1
            return True
        self._particles(dt,now)
        return True

    def _particles(self,dt,now):
        active=self.tracking and self.pose in ('张开手掌','握拳') and self.center is not None
        gather=active and self.pose=='握拳'
        if active:
            self.credit=min(5.,self.credit+dt*(40 if not gather else 20))
            while self.credit>=1. and len(self.particles)<48:
                self.credit-=1.;angle=self.random.random()*math.tau
                radius=self.radius*(.4 if gather else .15)
                x=self.center[0]+math.cos(angle)*radius;y=self.center[1]+math.sin(angle)*radius
                speed=self.random.uniform(20,95)
                self.particles.append([x,y,math.cos(angle)*speed,math.sin(angle)*speed,now,self.random.uniform(.7,1.5),self.random.uniform(1.,2.8),PALETTE[self.color]])
        else:self.credit=0.
        kept=[]
        for particle in self.particles:
            x,y,vx,vy,birth,life,size,color=particle
            if now-birth>=life:continue
            if gather:
                vx+=(self.center[0]-x)*14*dt;vy+=(self.center[1]-y)*14*dt
                drag=math.exp(-dt*3.);vx*=drag;vy*=drag
            else:vy-=12*dt
            particle[:4]=[x+vx*dt,y+vy*dt,vx,vy];kept.append(particle)
        self.particles=kept

    def reset_current(self):
        if self.scene is not None:self.scene.reset()
        else:self.clear()

    def background(self,live):
        return self.scene.preview(live) if self.mode=='scanner' else live

    def draw(self,cr,now,video=None):
        if self.scene is not None:self.scene.draw(cr,now,video)
        elif self.mode=='paint':self._draw_ink(cr)
        elif self.mode=='particles':self._draw_particles(cr,now)

    def _draw_ink(self,cr):
        import cairo
        if self.surface is None:self.surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,640,480)
        ink=cairo.Context(self.surface)
        if self.clear_pending:
            ink.set_operator(cairo.OPERATOR_CLEAR);ink.paint();ink.set_operator(cairo.OPERATOR_OVER);self.clear_pending=False
        ink.set_line_cap(cairo.LINE_CAP_ROUND);ink.set_line_join(cairo.LINE_JOIN_ROUND)
        for start,end,color in self.pending:
            for width,alpha in [(10.,.12),(5.,.4),(2.,1.)]:
                ink.set_source_rgba(*color,alpha);ink.set_line_width(width)
                ink.move_to(*start);ink.line_to(*end);ink.stroke()
        self.pending.clear()
        cr.set_source_surface(self.surface,0,0);cr.paint()
        if self.pen and self.cursor is not None:
            x,y=self.cursor;color=PALETTE[self.color]
            cr.set_source_rgba(*color,.2);cr.arc(x,y,9.,0,math.tau);cr.fill()
            cr.set_source_rgb(1.,1.,1.);cr.arc(x,y,2.5,0,math.tau);cr.fill()

    def _draw_particles(self,cr,now):
        import cairo
        if not self.particles and not (self.tracking and self.center is not None):
            return
        if self.particle_surface is None:
            self.particle_surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,320,240)
        layer=cairo.Context(self.particle_surface)
        layer.set_operator(cairo.OPERATOR_CLEAR);layer.paint();layer.set_operator(cairo.OPERATOR_OVER)
        layer.scale(.5,.5)
        self._render_particles(layer,now)
        xs=[p[0] for p in self.particles];ys=[p[1] for p in self.particles]
        if self.tracking and self.center is not None:
            radius=self.radius+12
            xs.extend([self.center[0]-radius,self.center[0]+radius])
            ys.extend([self.center[1]-radius,self.center[1]+radius])
        x0=max(0.,min(xs)-12);x1=min(640.,max(xs)+12)
        y0=max(0.,min(ys)-12);y1=min(480.,max(ys)+12)
        if x1<=x0 or y1<=y0:return
        cr.save();cr.rectangle(x0,y0,x1-x0,y1-y0);cr.clip();cr.scale(2.,2.)
        cr.set_source_surface(self.particle_surface,0,0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint();cr.restore()

    def _render_particles(self,cr,now):
        if not self.glow_masks:
            import cairo
            for bucket in (1,2,3):
                side=bucket*6+2;center=side/2.
                mask=cairo.ImageSurface(cairo.FORMAT_A8,side,side)
                brush=cairo.Context(mask)
                glow=cairo.RadialGradient(center,center,0.,center,center,bucket*3.)
                for offset,alpha in [(0.,1.),(.2,1.),(.4,.3),(1.,0.)]:
                    glow.add_color_stop_rgba(offset,1.,1.,1.,alpha)
                brush.set_source(glow);brush.paint()
                self.glow_masks[bucket]=mask
        for x,y,vx,vy,birth,life,size,color in self.particles:
            alpha=max(0.,1.-(now-birth)/life)
            mask=self.glow_masks[max(1,min(3,int(round(size))))]
            cr.set_source_rgba(*color,alpha)
            cr.mask_surface(mask,x-mask.get_width()/2.,y-mask.get_height()/2.)
        if self.tracking and self.center is not None:
            x,y=self.center;gather=self.pose=='握拳'
            radius=12. if gather else self.radius;color=PALETTE[self.color]
            cr.set_source_rgba(*color,.65);cr.set_line_width(1.2)
            for offset in (0.,7.):
                cr.arc(x,y,radius+offset,now*1.7,now*1.7+math.tau*.8);cr.stroke()
            for i in range(6):
                angle=now*.9+i*math.tau/6
                cr.arc(x+math.cos(angle)*radius,y+math.sin(angle)*radius,1.8,0,math.tau);cr.fill()

    def status_text(self,now):
        if self.scene is not None:
            if self.mode=='scanner':return self.scene.status()
            if self.error:return '识别暂不可用：'+self.error[:60]
            if not self.enabled:return '请开启手势识别' if self.mode!='body' else '人体识别已关闭'
            return self.scene.status()
        if self.error:return '演示：手势识别暂不可用'
        if not self.enabled:return '演示：请开启手势识别'
        if self.notice and now<self.notice_until:return self.notice
        if self.mode=='paint':
            return '画画：食指画 · 握拳停 · 张掌清空 · 比耶换色'
        return '粒子：张掌释放 · 握拳聚拢 · 比耶换色'
