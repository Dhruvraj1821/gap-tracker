from fastapi import FastAPI
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from dependencies import get_db
from models import User
from auth import hash_password

app = FastAPI()

class SignupRequest(BaseModel):
    email: EmailStr
    password: str

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/signup")
async def signup(req: SignupRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=req.email, hashed_password=hash_password(req.password))
    db.add(user)
    await db.commit()
    return {"message": "signup successful"}