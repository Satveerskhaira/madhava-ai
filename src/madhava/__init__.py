"""Madhava core agent: explains why a pharmacy claim behaved the way it did.

The core planner routes each question through the adjudication taxonomy to the
pharmacy domain agents (installed from the madhava-ai-subagent repository and
called in process), checks every observed outcome is explained, runs at most
one gap round, and answers with rule citations. The core is the only agent
exposed over A2A. Built on the rulegraph kernel (kernel/ in this repository).
"""

from .system import CoreSystem, build, procedures_path

__version__ = "0.2.0"
__all__ = ["CoreSystem", "build", "procedures_path"]
