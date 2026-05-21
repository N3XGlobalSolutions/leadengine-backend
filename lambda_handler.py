import sys
import os

print("--- DEBUG LAMBDA START ---")
print("CWD:", os.getcwd())
print("SYS.PATH:", sys.path)
print("FILES:", os.listdir('.'))

try:
    import mangum
    print("MANGUM IMPORT PATH:", mangum.__file__)
except Exception as e:
    print("MANGUM IMPORT FAILED:", repr(e))

from mangum import Mangum
from main import app

handler = Mangum(app, lifespan="off")
