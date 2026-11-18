"""LangGraph orchestration.

pipeline.py   the agentic pipeline: an intake graph per supplier notice and a persistent alert-lifecycle
              graph per drug (plan, check, pause for a person, act, follow up, replan). Everything agents
              and people decide happens here; the rest of the app starts and resumes it (AgentPipeline).
daily.py      the daily schedule: loads data and detects signals in code, and hands the agentic steps to
              the pipeline.

The graphs only orchestrate. Nodes call the same functions as the classic code path, so code checks,
read-only tools, typed handoffs and the approval rules are unchanged.
"""
