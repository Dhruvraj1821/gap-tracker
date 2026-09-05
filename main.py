from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi import Depends, HTTPException, Response, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from dependencies import get_db, get_current_user_id
from models import User
from auth import hash_password, create_access_token, create_refresh_token, verify_password, decode_token
import jwt
from llm_analysis import analyze_gap
from models import Submission
from sqlalchemy import func as sqlfunc
from fastapi.middleware.cors import CORSMiddleware
import datetime
import os

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

app = FastAPI()

RATE_LIMIT_PER_HOUR = 10

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SignupRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SubmitRequest(BaseModel):
    problem_title: str
    problem_statement: str
    wrong_code: str

class AddCorrectSolutionRequest(BaseModel):
    correct_code: str

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
        secure=IS_PRODUCTION,
        samesite="strict" if IS_PRODUCTION else "lax",
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

@app.get("/submissions")
async def list_submissions(
    limit: int = 20,
    offset: int = 0,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Submission)
        .where(Submission.user_id == user_id)
        .order_by(Submission.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    submissions = result.scalars().all()
    return [
        {
            "id": s.id,
            "problem_title": s.problem_title,
            "status": s.status,
            "category": s.gap_category,
            "note": s.gap_note,
            "topic_tags": s.topic_tags.split(",") if s.topic_tags else [],
            "created_at": s.created_at.isoformat(),
        }
        for s in submissions
    ]

@app.patch("/submissions/{submission_id}/correct-solution")
async def add_correct_solution(
    submission_id: int,
    req: AddCorrectSolutionRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Submission).where(Submission.id == submission_id, Submission.user_id == user_id))
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    submission.correct_code = req.correct_code
    analysis = analyze_gap(submission.problem_title, submission.problem_statement, submission.wrong_code, req.correct_code)
    submission.gap_category = analysis["category"]
    submission.gap_note = analysis["note"]
    submission.topic_tags = ",".join(analysis.get("topic_tags", []))

    await db.commit()
    return {"message": "re-analyzed", "category": submission.gap_category, "note": submission.gap_note}

@app.get("/patterns")
async def get_patterns(user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Submission.gap_category, sqlfunc.count())
        .where(Submission.user_id == user_id)
        .group_by(Submission.gap_category)
    )
    category_counts = {row[0]: row[1] for row in result.all()}

    tag_result = await db.execute(select(Submission.topic_tags).where(Submission.user_id == user_id))
    tag_counter = {}
    for (tags_str,) in tag_result.all():
        if tags_str:
            for tag in tags_str.split(","):
                tag_counter[tag] = tag_counter.get(tag, 0) + 1

    return {"category_counts": category_counts, "topic_tag_counts": tag_counter}

@app.get("/submissions/{submission_id}")
async def get_submission(
    submission_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Submission).where(Submission.id == submission_id, Submission.user_id == user_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    return {
        "id": submission.id,
        "status": submission.status,
        "category": submission.gap_category,
        "note": submission.gap_note,
        "topic_tags": submission.topic_tags.split(",") if submission.topic_tags else [],
    }