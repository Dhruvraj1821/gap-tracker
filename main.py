from fastapi import FastAPI
from fastapi import Depends, HTTPException, Response, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from dependencies import get_db, get_current_user_id
from models import User
from auth import hash_password, create_access_token, create_refresh_token, verify_password, decode_token
from dotenv import load_dotenv
import jwt

load_dotenv()


app = FastAPI()

class SignupRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
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

@app.post("/login")
async def login(req: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 60 * 60,
    )
    return {"access_token": access_token}

@app.post("/refresh")
async def refresh(request: Request):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    new_access_token = create_access_token(int(payload["sub"]))
    return {"access_token": new_access_token}

@app.get("/me")
async def me(user_id: int = Depends(get_current_user_id)):
    return {"user_id": user_id}