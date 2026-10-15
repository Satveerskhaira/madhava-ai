"""Madhava: explain why a pharmacy claim behaved the way it did.

A planner agent routes each question through a taxonomy of adjudication
domains to domain agents. Each domain agent searches a rules/policy graph for
candidate rules, fetches only the claim and benefit setup fields those rules
read, evaluates them deterministically, and returns findings with evidence.
The planner checks every observed outcome is explained, loops once when it is
not, and writes the answer.
"""

__version__ = "0.1.0"
