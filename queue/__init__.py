import sys
import os

# Prevent standard library 'queue' name collision by importing the standard library module
# and exposing its contents through our local 'queue' package.

# Save our own module and temporarily delete 'queue' from sys.modules so Python doesn't return it
_our_module = sys.modules.get('queue')
if 'queue' in sys.modules:
    del sys.modules['queue']

_original_path = sys.path.copy()
_cwd = os.getcwd()
sys.path = [p for p in sys.path if p not in ('', _cwd, os.path.abspath('.'))]

# Import standard library queue
import queue as _std_queue

# Restore sys.path and sys.modules
sys.path = _original_path
if _our_module:
    sys.modules['queue'] = _our_module

# Expose everything from standard library queue
globals().update({k: v for k, v in _std_queue.__dict__.items() if not k.startswith('__')})
