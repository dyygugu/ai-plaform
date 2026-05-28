from fastapi import APIRouter

from app.api.v1.routes import accounts, ai, ai_timer, alerts, audit, auto_answer_runs, auto_production, backups, bon8_production, data_quality, delivery, earnings, execution_devices, final_acceptance, freeze, health, incidents, inspection, local_agent, notifications, observability, operation_recordings, ops, restore_drills, roadmap_final, score_loop, settings, submitted_history, task_abilities, task_auto_runs, tasks, workers

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(accounts.router)
api_router.include_router(tasks.router)
api_router.include_router(settings.router)
api_router.include_router(audit.router)
api_router.include_router(backups.router)
api_router.include_router(ai.router)
api_router.include_router(ai_timer.router)
api_router.include_router(score_loop.router)
api_router.include_router(bon8_production.router)
api_router.include_router(workers.router)
api_router.include_router(operation_recordings.router)
api_router.include_router(alerts.router)
api_router.include_router(notifications.router)
api_router.include_router(restore_drills.router)
api_router.include_router(earnings.router)
api_router.include_router(data_quality.router)
api_router.include_router(final_acceptance.router)
api_router.include_router(roadmap_final.router)
api_router.include_router(delivery.router)
api_router.include_router(inspection.router)
api_router.include_router(freeze.router)
api_router.include_router(incidents.router)
api_router.include_router(task_abilities.router)
api_router.include_router(task_auto_runs.router)
api_router.include_router(auto_answer_runs.router)
api_router.include_router(auto_production.router)
api_router.include_router(execution_devices.router)
api_router.include_router(local_agent.router)
api_router.include_router(submitted_history.router)
api_router.include_router(ops.router)
api_router.include_router(observability.router)







