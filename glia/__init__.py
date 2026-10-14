"""glia — a propose-only maintenance agent for a Markdown/Obsidian vault.

Propose-only: the agent side (gateway) reads and records proposals; the only code that writes to a
vault clone is the approval executor (glia.apply), which applies the owner's ticked items one commit each
on a glia/* branch, and refuses in code what the authority ladder forbids (glia.ladder).
"""
__version__ = "0.2.0"
