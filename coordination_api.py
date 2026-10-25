"""Shared validated operation entry point for MCP and the native CLI."""
import os


def specification(name):
    from mcp_server import TOOLS
    try:return next(spec for n,_,spec in TOOLS if n==name)
    except StopIteration:raise ValueError('Unknown Scrum operation') from None


def execute(name,args,dispatch=None,spec=None,validator=None):
    if spec is None:spec=specification(name)
    if validator is None:
        from mcp_server import _validate
        validator=_validate
    validator(args,spec)
    if dispatch is None:
        from store import run
        dispatch=run
    result=dispatch(name,args)
    if isinstance(result,dict) and name not in ('agent_checkpoint','coordination_metrics','project_metrics'):
        import run_cost
        notice=run_cost.notice(os.environ.get('SCRUM_RUN_COST_STATE'))
        if notice:result['efficiency_notice']=notice
    return result
