from fastapi import APIRouter
from app.services.ride_service import create_ride

router = APIRouter()

@router.post("/rides")
def create():
    return create_ride()

@router.get("/rides/{id}")
def get_ride(id: str):
    return {"id": id}

