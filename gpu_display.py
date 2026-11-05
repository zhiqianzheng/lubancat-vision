"""Independent X11/EGL preview; load Mali privately without replacing system GL."""
import ctypes as C
import os
import numpy as np


P=C.c_void_p;I=C.c_int;U=C.c_uint;L=C.c_ulong


class ClientData(C.Union):
    _fields_=[('b',C.c_char*20),('s',C.c_short*10),('l',C.c_long*5)]


class ClientEvent(C.Structure):
    _fields_=[('type',I),('serial',L),('send_event',I),('display',P),('window',L),
              ('message_type',L),('format',I),('data',ClientData)]


class ConfigureEvent(C.Structure):
    _fields_=[('type',I),('serial',L),('send_event',I),('display',P),('event',L),('window',L),
              ('x',I),('y',I),('width',I),('height',I),('border_width',I),('above',L),('override_redirect',I)]


class XEvent(C.Union):
    _fields_=[('type',I),('client',ClientEvent),('configure',ConfigureEvent),('pad',C.c_long*24)]


class GpuDisplay:
    def __init__(self,title='LubanCat GPU preview prototype',window=None,width=1,height=1):
        self.owns_window=window is None;self.native_window=window
        self.display=None;self.egl=None;self.surface=None;self.context=None;self.window=0
        self.width=width;self.height=height;self.texture=U();self.programs=[]
        self.overlay_texture=U();self.overlay_size=None
        self.x=C.CDLL('libX11.so.6')
        self.gl=C.CDLL('libmali.so.1',mode=os.RTLD_NOW|getattr(os,'RTLD_DEEPBIND',0))
        self._bind()
        try:self._open(title)
        except BaseException:self.close();raise

    def _bind(self):
        def bind(lib,name,ret,args):
            f=getattr(lib,name);f.restype=ret;f.argtypes=args;setattr(self,name,f)
        for name,ret,args in [
            ('XInitThreads',I,[]),('XOpenDisplay',P,[C.c_char_p]),('XDefaultScreen',I,[P]),
            ('XRootWindow',L,[P,I]),('XDisplayWidth',I,[P,I]),('XDisplayHeight',I,[P,I]),
            ('XDefaultVisual',P,[P,I]),('XVisualIDFromVisual',L,[P]),
            ('XCreateSimpleWindow',L,[P,L,I,I,U,U,U,L,L]),('XStoreName',I,[P,L,C.c_char_p]),
            ('XSelectInput',I,[P,L,C.c_long]),('XInternAtom',L,[P,C.c_char_p,I]),
            ('XSetWMProtocols',I,[P,L,C.POINTER(L),I]),
            ('XChangeProperty',I,[P,L,L,L,I,I,C.POINTER(C.c_ubyte),I]),
            ('XMapWindow',I,[P,L]),('XUnmapWindow',I,[P,L]),('XSync',I,[P,I]),('XPending',I,[P]),
            ('XNextEvent',I,[P,C.POINTER(XEvent)]),('XLookupKeysym',L,[P,I]),
            ('XDestroyWindow',I,[P,L]),('XCloseDisplay',I,[P])]:bind(self.x,name,ret,args)
        for name,ret,args in [
            ('eglGetDisplay',P,[P]),('eglInitialize',U,[P,C.POINTER(I),C.POINTER(I)]),
            ('eglChooseConfig',U,[P,C.POINTER(I),C.POINTER(P),I,C.POINTER(I)]),
            ('eglGetConfigAttrib',U,[P,P,I,C.POINTER(I)]),('eglBindAPI',U,[U]),
            ('eglCreateWindowSurface',P,[P,P,L,C.POINTER(I)]),
            ('eglCreateContext',P,[P,P,P,C.POINTER(I)]),('eglMakeCurrent',U,[P,P,P,P]),
            ('eglSwapInterval',U,[P,I]),('eglSwapBuffers',U,[P,P]),('eglGetError',I,[]),
            ('eglDestroyContext',U,[P,P]),('eglDestroySurface',U,[P,P]),('eglTerminate',U,[P]),
            ('glGetString',C.c_char_p,[U]),('glCreateShader',U,[U]),
            ('glShaderSource',None,[U,I,C.POINTER(C.c_char_p),C.POINTER(I)]),('glCompileShader',None,[U]),
            ('glGetShaderiv',None,[U,U,C.POINTER(I)]),('glGetShaderInfoLog',None,[U,I,C.POINTER(I),P]),
            ('glDeleteShader',None,[U]),('glCreateProgram',U,[]),('glAttachShader',None,[U,U]),
            ('glBindAttribLocation',None,[U,U,C.c_char_p]),('glLinkProgram',None,[U]),
            ('glGetProgramiv',None,[U,U,C.POINTER(I)]),('glGetProgramInfoLog',None,[U,I,C.POINTER(I),P]),
            ('glUseProgram',None,[U]),('glDeleteProgram',None,[U]),
            ('glGenTextures',None,[I,C.POINTER(U)]),('glDeleteTextures',None,[I,C.POINTER(U)]),
            ('glBindTexture',None,[U,U]),('glTexParameteri',None,[U,U,I]),
            ('glTexImage2D',None,[U,I,I,I,I,I,U,U,P]),('glTexSubImage2D',None,[U,I,I,I,I,I,U,U,P]),
            ('glVertexAttribPointer',None,[U,I,U,U,I,P]),('glEnableVertexAttribArray',None,[U]),
            ('glDisableVertexAttribArray',None,[U]),('glViewport',None,[I,I,I,I]),
            ('glDrawArrays',None,[U,I,I]),('glClearColor',None,[C.c_float]*4),('glClear',None,[U]),
            ('glGetError',U,[]),('glEnable',None,[U]),('glDisable',None,[U]),
            ('glBlendFunc',None,[U,U])]:bind(self.gl,name,ret,args)

    def _check(self,ok,operation):
        if not ok:raise RuntimeError('{} failed: EGL 0x{:x}'.format(operation,self.eglGetError()))

    def _open(self,title):
        self.XInitThreads();self.display=self.XOpenDisplay(None)
        if not self.display:raise RuntimeError('Cannot open X11 display')
        screen=self.XDefaultScreen(self.display);root=self.XRootWindow(self.display,screen)
        if self.owns_window:
            self.width=self.XDisplayWidth(self.display,screen);self.height=self.XDisplayHeight(self.display,screen)
        self.egl=self.eglGetDisplay(self.display);a=I();b=I()
        self._check(self.eglInitialize(self.egl,C.byref(a),C.byref(b)),'eglInitialize')
        attrs=(I*13)(0x3033,4,0x3040,4,0x3024,8,0x3023,8,0x3022,8,0x3021,0,0x3038)
        configs=(P*128)();count=I()
        self._check(self.eglChooseConfig(self.egl,attrs,configs,128,C.byref(count)),'eglChooseConfig')
        visual=self.XVisualIDFromVisual(self.XDefaultVisual(self.display,screen));config=None
        for candidate in configs[:count.value]:
            native=I();self.eglGetConfigAttrib(self.egl,candidate,0x302e,C.byref(native))
            if native.value==visual:config=candidate;break
        if config is None:raise RuntimeError('No EGL config matches the X11 window visual')
        if self.owns_window:
            self.window=self.XCreateSimpleWindow(self.display,root,0,0,self.width,self.height,0,0,0)
            self.XStoreName(self.display,self.window,title.encode())
            self.XSelectInput(self.display,self.window,(1<<0)|(1<<15)|(1<<17))
            self.delete=self.XInternAtom(self.display,b'WM_DELETE_WINDOW',0);protocol=L(self.delete)
            self.XSetWMProtocols(self.display,self.window,C.byref(protocol),1)
            state=self.XInternAtom(self.display,b'_NET_WM_STATE',0)
            fullscreen=L(self.XInternAtom(self.display,b'_NET_WM_STATE_FULLSCREEN',0))
            self.XChangeProperty(self.display,self.window,state,4,32,0,C.cast(C.byref(fullscreen),C.POINTER(C.c_ubyte)),1)
            self.XMapWindow(self.display,self.window);self.XSync(self.display,0)
        else:self.window=self.native_window
        self._check(self.eglBindAPI(0x30A0),'eglBindAPI')
        self.surface=self.eglCreateWindowSurface(self.egl,config,self.window,None)
        self._check(self.surface,'eglCreateWindowSurface')
        self.context=self.eglCreateContext(self.egl,config,None,(I*3)(0x3098,2,0x3038))
        self._check(self.context,'eglCreateContext')
        self._check(self.eglMakeCurrent(self.egl,self.surface,self.surface,self.context),'eglMakeCurrent')
        self.vendor=self.glGetString(0x1f00).decode();self.renderer=self.glGetString(0x1f01).decode()
        if 'Mali' not in self.renderer:raise RuntimeError('Hardware Mali renderer required: '+self.renderer)
        self.eglSwapInterval(self.egl,1)
        vertex=b'attribute vec2 position;attribute vec2 texcoord;varying vec2 uv;void main(){uv=texcoord;gl_Position=vec4(position,0.,1.);}'
        fragment=b'precision mediump float;uniform sampler2D image;varying vec2 uv;void main(){gl_FragColor=vec4(texture2D(image,uv).bgr,1.);}'
        self.video=self._program(vertex,fragment)
        self.overlay_program=self._program(vertex,
            b'precision mediump float;uniform sampler2D image;varying vec2 uv;void main(){gl_FragColor=texture2D(image,uv).bgra;}')
        self.boxes=self._program(b'attribute vec2 position;void main(){gl_Position=vec4(position,0.,1.);}',
            b'precision mediump float;void main(){gl_FragColor=vec4(.3,1.,.65,1.);}')
        self.glGenTextures(1,C.byref(self.texture));self.glBindTexture(0x0de1,self.texture.value)
        for parameter,value in [(0x2801,0x2601),(0x2800,0x2601),(0x2802,0x812f),(0x2803,0x812f)]:
            self.glTexParameteri(0x0de1,parameter,value)
        self.glTexImage2D(0x0de1,0,0x1908,640,480,0,0x1908,0x1401,None)
        self.glGenTextures(1,C.byref(self.overlay_texture))
        self.glBindTexture(0x0de1,self.overlay_texture.value)
        for parameter,value in [(0x2801,0x2601),(0x2800,0x2601),(0x2802,0x812f),(0x2803,0x812f)]:
            self.glTexParameteri(0x0de1,parameter,value)
        self.glClearColor(0.,0.,0.,1.)

    def _program(self,vertex,fragment):
        program=self.glCreateProgram();self.programs.append(program)
        for kind,source in [(0x8b31,vertex),(0x8b30,fragment)]:
            shader=self.glCreateShader(kind);pointer=C.c_char_p(source)
            try:
                self.glShaderSource(shader,1,C.byref(pointer),None);self.glCompileShader(shader)
                status=I();self.glGetShaderiv(shader,0x8b81,C.byref(status))
                if not status.value:
                    log=C.create_string_buffer(4096);self.glGetShaderInfoLog(shader,4096,None,log)
                    raise RuntimeError(log.value.decode(errors='replace'))
                self.glAttachShader(program,shader)
            finally:self.glDeleteShader(shader)
        self.glBindAttribLocation(program,0,b'position');self.glBindAttribLocation(program,1,b'texcoord')
        self.glLinkProgram(program);status=I();self.glGetProgramiv(program,0x8b82,C.byref(status))
        if not status.value:
            log=C.create_string_buffer(4096);self.glGetProgramInfoLog(program,4096,None,log)
            raise RuntimeError(log.value.decode(errors='replace'))
        return program

    def poll(self):
        event=XEvent()
        while self.XPending(self.display):
            self.XNextEvent(self.display,C.byref(event))
            if event.type==33 and event.client.data.l[0]==self.delete:return False
            if event.type==2 and self.XLookupKeysym(C.byref(event),0)==0xff1b:return False
            if event.type==22:
                self.width=max(1,event.configure.width);self.height=max(1,event.configure.height)
        return True

    def draw(self,frame,objects=(),overlay=None,fill=True,on_uploaded=None,overlay_changed=True):
        if frame.shape!=(480,640,4) or frame.dtype!=np.uint8 or not frame.flags.c_contiguous:
            raise ValueError('Expected contiguous 640x480 BGRx camera frame')
        w,h=self.width,self.height;scale=(max if fill else min)(w/640.,h/480.)
        sx,sy=640*scale/w,480*scale/h
        vertices=np.asarray([[-sx,-sy,0,1],[sx,-sy,1,1],[-sx,sy,0,0],[sx,sy,1,0]],np.float32)
        self.glViewport(0,0,w,h);self.glClear(0x4000);self.glUseProgram(self.video)
        self.glBindTexture(0x0de1,self.texture.value)
        self.glTexSubImage2D(0x0de1,0,0,0,640,480,0x1908,0x1401,frame.ctypes.data)
        for index,offset in [(0,0),(1,8)]:
            self.glEnableVertexAttribArray(index)
            self.glVertexAttribPointer(index,2,0x1406,0,16,vertices.ctypes.data+offset)
        self.glDrawArrays(5,0,4)
        lines=[]
        for cls,score,x1,y1,x2,y2 in objects:
            corners=[(x1,y1),(x2,y1),(x2,y2),(x1,y2)]
            for a,b in zip(corners,corners[1:]+corners[:1]):
                lines.extend((2*(p[0]-320)*scale/w,-2*(p[1]-240)*scale/h) for p in (a,b))
        if lines:
            data=np.asarray(lines,np.float32)
            self.glUseProgram(self.boxes);self.glDisableVertexAttribArray(1)
            self.glVertexAttribPointer(0,2,0x1406,0,0,data.ctypes.data)
            self.glDrawArrays(1,0,len(lines))
        if overlay is not None:
            if overlay.shape!=(h,w,4) or overlay.dtype!=np.uint8 or not overlay.flags.c_contiguous:
                raise ValueError('Expected native-size premultiplied BGRA overlay')
            self.glBindTexture(0x0de1,self.overlay_texture.value)
            if self.overlay_size!=(w,h):
                self.glTexImage2D(0x0de1,0,0x1908,w,h,0,0x1908,0x1401,None);self.overlay_size=w,h
                overlay_changed=True
            if overlay_changed:self.glTexSubImage2D(0x0de1,0,0,0,w,h,0x1908,0x1401,overlay.ctypes.data)
            quad=np.asarray([[-1,-1,0,1],[1,-1,1,1],[-1,1,0,0],[1,1,1,0]],np.float32)
            self.glUseProgram(self.overlay_program);self.glEnableVertexAttribArray(1)
            for index,offset in [(0,0),(1,8)]:self.glVertexAttribPointer(index,2,0x1406,0,16,quad.ctypes.data+offset)
            self.glEnable(0x0be2);self.glBlendFunc(1,0x0303)
            self.glDrawArrays(5,0,4);self.glDisable(0x0be2)
        error=self.glGetError()
        if error:raise RuntimeError('GL rendering error 0x{:x}'.format(error))
        # glTexSubImage2D has consumed client memory; swapping may still wait for vblank.
        if on_uploaded is not None:on_uploaded()
        self._check(self.eglSwapBuffers(self.egl,self.surface),'eglSwapBuffers')

    def close(self):
        if self.display and self.window and self.owns_window:
            self.XUnmapWindow(self.display,self.window);self.XSync(self.display,0)
        if self.egl:
            if self.context:
                for program in self.programs:self.glDeleteProgram(program)
                if self.texture.value:self.glDeleteTextures(1,C.byref(self.texture))
                if self.overlay_texture.value:self.glDeleteTextures(1,C.byref(self.overlay_texture))
                self.eglMakeCurrent(self.egl,None,None,None)
                self.eglDestroyContext(self.egl,self.context);self.context=None
            if self.surface:self.eglDestroySurface(self.egl,self.surface);self.surface=None
            self.eglTerminate(self.egl);self.egl=None
        if self.display:
            if self.window and self.owns_window:self.XDestroyWindow(self.display,self.window)
            self.window=0
            self.XCloseDisplay(self.display);self.display=None
