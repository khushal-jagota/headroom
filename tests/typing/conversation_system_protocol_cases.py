"""Strict-mypy cases for the conversation contract's implementation boundary.

This module is type-checked, not run. Binding an implementation to a
``ConversationSystem``-typed name is what turns signature drift between the Protocol and
its implementations into a mypy error instead of a runtime surprise; structural typing
checks nothing until someone writes the binding down.
"""

from __future__ import annotations

from planner.conversation2.contracts import ConversationSystem
from planner.conversation2.in_memory_conversation_system import InMemoryConversationSystem


def _in_memory_conversation_system_case() -> ConversationSystem:
    return InMemoryConversationSystem()
