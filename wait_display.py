import sys
import time
import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk

for _ in range(60):
    display = Gdk.Display.open(':0')
    if display:
        display.close()
        sys.exit(0)
    time.sleep(1)
sys.exit(1)
