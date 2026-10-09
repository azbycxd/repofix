"""Profile routing; v1 preserves its frozen ordering and observations."""


def run_agent(agent):
    if getattr(agent.config, "profile", "v1") == "v1":
        from .v1 import V1Loop
        # The compatibility loop delegates through the same outer object so its
        # injected model, env, trace and usage reader keep their original APIs.
        agent._execute_tool = lambda state: V1Loop._execute_tool(agent, state)
        return V1Loop.run(agent)
    return run_v3(agent)


def run_v3(agent):
    raise ValueError("v3 execution is not enabled until the registry milestone")
