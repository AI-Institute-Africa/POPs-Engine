"""ID/KYC: document recognition, face match and rules-based identity verification.

Same contract as the payment engine: the readers and the face matcher only produce facts (text,
scores). A person, or a short list of deterministic rules in `auto.py`, decides. Nothing here moves
money, and anything uncertain goes to a reviewer.
"""
