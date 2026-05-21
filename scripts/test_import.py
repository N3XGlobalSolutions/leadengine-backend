
import sys
import os

sys.path.append(os.getcwd())

try:
    from app.models.conversation import Conversation, ConversationState
    print("Import successful")
except Exception as e:
    print(f"Import failed: {e}")
