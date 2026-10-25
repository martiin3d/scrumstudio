"""Small default catalog for a worker already claimed by its supervisor."""
EXECUTION_TOOLS=('task_get','task_search','task_plan','task_release','task_update',
                 'task_changes','work_finish','agent_checkpoint','file_locks')

# Shared by reservation planning, telemetry validation and the public schema.
MAX_CONTEXT_BATCH_TASKS=6
