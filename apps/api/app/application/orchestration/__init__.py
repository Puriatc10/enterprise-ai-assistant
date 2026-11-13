"""Conversation orchestration (Phase 2).

Belongs here: prompt assembly, the bounded LLM loop, tool-round accounting, and
the decision to answer partially when a limit is reached.

Does not belong here: HTTP concerns, SQL, provider SDK types, or any rule about
*who* may do what — authorization is enforced by ``app.security`` and the tool
executor before orchestration ever sees a tool result.
"""
