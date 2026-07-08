"""AWS Lambda entrypoint (API via Mangum).

NOTE: This wraps the FastAPI app for API Gateway/ALB events only. Background SQS
worker threads started in the FastAPI lifespan do NOT run under Lambda
(lifespan is disabled). SQS processing must be wired as a dedicated
SQS-event-source Lambda/worker service. See MANUAL_ACTIONS_REQUIRED.md.
"""

from mangum import Mangum

from main import app

handler = Mangum(app, lifespan="off")
