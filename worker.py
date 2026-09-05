from dotenv import load_dotenv
load_dotenv()

import asyncio
from database import AsyncSessionLocal
from models import Submission
from llm_analysis import analyze_gap
from sqlalchemy import select

POLL_INTERVAL_SECONDS = 3

async def process_one_submission() -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Submission)
            .where(Submission.status == "pending")
            .order_by(Submission.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        submission = result.scalar_one_or_none()

        if submission is None:
            return False

        submission.status = "processing"
        await db.commit()

        try:
            analysis = analyze_gap(
                submission.problem_title,
                submission.problem_statement,
                submission.wrong_code,
                submission.correct_code,
            )
            submission.gap_category = analysis["category"]
            submission.gap_note = analysis["note"]
            submission.topic_tags = ",".join(analysis.get("topic_tags", []))
            submission.status = "complete"
        except Exception as e:
            print(f"Submission {submission.id} failed: {e}")
            submission.status = "failed"

        await db.commit()
        return True

async def main():
    print("Worker started, polling for pending submissions...")
    while True:
        processed = await process_one_submission()
        if not processed:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(main())