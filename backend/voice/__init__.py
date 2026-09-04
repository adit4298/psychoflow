"""§14 voice layer — STT bridge + local Ollama/Gemma intent parsing.

Deliberately empty of imports. `backend/voice/intent_agent.py` must stay
importable in a voice-only context (no SUMO, no torch, no numpy), exactly as
`backend/control_api.py`'s docstring requires of the control surface it calls.
Importing this package must not drag in a simulator.
"""
