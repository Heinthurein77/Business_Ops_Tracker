"""Business-logic services with no HTTP layer.

m-21: the 08:30 cron job (src/jobs/scheduled_alerts.py) used to import
the three run_* alert functions from src/routers/ops.py, dragging
FastAPI, schemas, R2 and upload validation into the job. The functions
now live here; both the router endpoints and the job import from here.
"""
