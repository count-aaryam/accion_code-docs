from fastapi import APIRouter

router = APIRouter()

@router.get("/users/{id}")
def get_user(id: str):
    return {"id": id}

