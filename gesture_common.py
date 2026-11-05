HAND_CONNECTIONS = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
                    (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),
                    (13,17),(0,17),(17,18),(18,19),(19,20)]


class DoublePalmStop:
    """Require fresh, distinct two-open-hand observations before a clean exit."""
    def __init__(self, hold=2.):self.hold=hold;self.reset()
    def reset(self):self.since=None;self.last=None;self.sample=None;self.samples=0
    def update(self,result,now):
        if self.last is not None and now-self.last>1.2:self.reset()
        if result is None:self.reset();return False
        sample=result.get('sample_id')
        if sample is None or sample==self.sample:return False
        self.sample=sample
        if not result.get('stop_checked'):return False
        if not result.get('stop'):self.reset();return False
        if self.since is None:self.since=now
        self.last=now;self.samples+=1
        return self.samples>=3 and now-self.since>=self.hold


class NumberOkSequence:
    """Select a stable digit, then confirm it with a separate stable OK pose."""
    def __init__(self, timeout=5.):self.timeout=timeout;self.reset()
    def reset(self):
        self.selected=None;self.deadline=0.;self.last_sample=None;self.last_seen=None
        self.candidate=None;self.since=0.;self.samples=0
    def update(self, result, now, enabled=True):
        if not enabled:self.reset();return None
        if self.selected is not None and now>self.deadline:self.reset()
        if result is None:
            self.candidate=None;self.samples=0;return None
        sample=result.get('sample_id')
        if sample is None or sample==self.last_sample:return None
        if self.last_seen is not None and now-self.last_seen>1.:
            self.candidate=None;self.samples=0
        self.last_seen=now
        self.last_sample=sample
        number=result.get('number')
        pose=number if number in (1,2,3,4,5) else 'OK' if result.get('label')=='OK' else None
        if pose is None:self.candidate=None;self.samples=0;return None
        if pose!=self.candidate:
            self.candidate=pose;self.since=now;self.samples=0
        self.samples+=1
        if self.samples<2 or now-self.since<.25:return None
        if pose=='OK':
            selected=self.selected
            self.reset()
            return selected
        self.selected=pose;self.deadline=now+self.timeout
        return None


class StableHandNumber:
    """Require consecutive model samples; never carry a digit across hand loss."""
    def __init__(self):self.reset()
    def reset(self):self.candidate=None;self.since=0.;self.last=None;self.samples=0
    def update(self, number, now):
        if number not in (1,2,3,4,5):self.reset();return None
        if number!=self.candidate or self.last is None or now-self.last>1.:
            self.candidate=number;self.since=now;self.samples=0
        self.last=now;self.samples+=1
        return number if self.samples>=2 and now-self.since>=.18 else None


class CoveredLensReset:
    """One reset per sustained dark cover, using fresh camera frames only."""
    def __init__(self, hold=1.2):
        self.hold=hold;self.dark_since=None;self.clear_since=None
        self.last_sample=None;self.latched=False

    def update(self, frame, now, active):
        import numpy as np
        if self.last_sample is not None and now-self.last_sample<.1:return False
        if self.last_sample is not None and now-self.last_sample>.4:
            self.dark_since=self.clear_since=None
        self.last_sample=now
        # BGR only: the fourth byte is padding, not image brightness.
        pixels=frame[::12,::12,:3].astype(np.uint16)
        luma=(pixels[:,:,0]*29+pixels[:,:,1]*150+pixels[:,:,2]*77)/256.
        dark=float(luma.mean())<30 and float(np.mean(luma<45))>=.98
        if dark:
            self.clear_since=None
            if not active or self.latched:self.dark_since=None;return False
            if self.dark_since is None:self.dark_since=now
            if now-self.dark_since>=self.hold:
                self.latched=True;self.dark_since=None;return True
        else:
            self.dark_since=None
            if float(luma.mean())>=45:
                if self.clear_since is None:self.clear_since=now
                if now-self.clear_since>=.6:self.latched=False
            else:self.clear_since=None
        return False


class FullscreenGestureSequence:
    """Accept deliberate fist/palm edges from distinct, stable inference samples."""
    def __init__(self, hold=.25, gap=3., cooldown=1.):
        self.hold, self.gap, self.cooldown = hold, gap, cooldown
        self.reset()

    def reset(self):
        self.anchor = self.candidate = self.last_sample = None
        self.candidate_since = self.last_seen = None
        self.samples = 0
        self.last_action = float('-inf')

    def update(self, label, sample, now, enabled=True):
        if not enabled:
            self.reset()
            return None
        if self.last_seen is not None and now-self.last_seen > self.gap:
            self.reset()
        if sample is None or sample == self.last_sample:
            return None
        self.last_sample = sample
        if label not in ('握拳', '张开手掌'):
            self.candidate = None
            self.samples = 0
            if label:
                self.anchor = None
                self.last_seen = None
            return None
        self.last_seen = now
        if label != self.candidate:
            self.candidate = label
            self.candidate_since = now
            self.samples = 1
            return None
        self.samples += 1
        if self.samples < 2 or now-self.candidate_since < self.hold:
            return None
        if self.anchor is None:
            self.anchor = label
            return None
        if self.anchor == label or now-self.last_action < self.cooldown:
            return None
        self.anchor = label
        self.last_action = now
        return label == '张开手掌'
