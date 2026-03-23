import sys
import os

# Add the skills directory to the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../skills/transaction-inbox')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../skills/transaction-inbox/lib')))
