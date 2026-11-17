from fastapi import FastAPI
from app.api.rides import router as rides_router
from app.api.users import router as users_router

app = FastAPI()
app.include_router(rides_router)
app.include_router(users_router)

@app.get("/health")
def health():
    return {"status": "ok"}

