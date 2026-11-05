HAND_CONNECTIONS = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
                    (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),
                    (13,17),(0,17),(17,18),(18,19),(19,20)]


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
